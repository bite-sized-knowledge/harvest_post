import asyncio
import os
import re
import sys
import subprocess
import argparse
from dataclasses import dataclass, asdict
from http import HTTPStatus
from typing import Optional

from db_conn import Connection
from embedder import TextEmbeddings
from qdrant_config import QdrantVectorStore
from config import (
    INSERT_QUERY, QUEUE_QUERY, COLUMN_NAMES, get_metadata, update_model_config_query,
    REJECTED_INSERT_QUERY, QUALITY_REJECT_THRESHOLD,
    ENABLE_INFERENCE_LOG, LLM_CONFIG, PROMPT_VERSION,
)
from response import HTTPResponse
from llm_pipeline import LangChainModel
from llm_pipeline.inference_logger import InferenceLogger
from preprocessing import BlogPostProcessor, parse_article_text_from_html
from errors import ProcessingError, ErrorCategory, ErrorSeverity, classify_exception
from logger import logger
from sqlalchemy.sql import text, bindparam

# --- Reject reason constants ---------------------------------------------------
REASON_EMPTY_TITLE = "empty_title"
REASON_EMPTY_CONTENT = "empty_content"
REASON_LLM_FAILED = "llm_extraction_failed"
REASON_LOW_QUALITY_PREFIX = "low_quality"

# Concurrency limits (vLLM continuous batching 활용)
LLM_SEMAPHORE = asyncio.Semaphore(16)  # vLLM continuous batching
EMBEDDING_SEMAPHORE = asyncio.Semaphore(4)  # Ollama 임베딩 병렬

# Call Preprocessor
PREPROCESSOR = BlogPostProcessor()

# Call Model
MODEL = LangChainModel()


# --- Terminal rejection marker -------------------------------------------------
# process_article can return one of four things:
#   1. (values, article_id) tuple  → accepted, INSERT into article
#   2. RejectedArticle instance    → terminal reject, INSERT into article_rejected
#   3. ProcessingError             → transient or permanent error (existing logic)
#   4. None                        → transient failure, leave in queue for retry
@dataclass
class RejectedArticle:
    article_id: str
    blog_id: Optional[int]
    url: Optional[str]
    title: Optional[str]
    thumbnail: Optional[str]
    description: Optional[str]
    content: Optional[str]
    content_length: int
    lang: Optional[str]
    published_at: object  # datetime or None
    quality_score: Optional[int]
    reject_reason: str


# Matches video embeds we should credit even when prose is short.
# Covers YouTube/Vimeo iframes, native <video> tags, and direct watch links.
_VIDEO_EMBED_RE = re.compile(
    r'<iframe[^>]*(?:youtube\.com|youtu\.be|youtube-nocookie\.com|vimeo\.com)'
    r'|<video\b'
    r'|youtube\.com/watch'
    r'|youtu\.be/',
    re.IGNORECASE,
)


def has_video_embed(html: Optional[str]) -> bool:
    if not html:
        return False
    return bool(_VIDEO_EMBED_RE.search(html))


def _build_rejection(data, reason: str, quality_score: Optional[int] = None) -> RejectedArticle:
    """Construct a RejectedArticle from the raw queue row + a reason."""
    content = data.get("content") or ""
    return RejectedArticle(
        article_id=data.get("article_id"),
        blog_id=int(data["blog_id"]) if data.get("blog_id") is not None else None,
        url=data.get("url"),
        title=data.get("title"),
        thumbnail=data.get("thumbnail"),
        description=data.get("description"),
        content=content if content else None,
        content_length=len(content),
        lang=None,
        published_at=data.get("published_at"),
        quality_score=quality_score,
        reject_reason=reason,
    )


async def process_article(data):
    """비동기로 개별 데이터를 처리하는 함수.

    Returns one of:
      - (values_tuple, article_id): article accepted, to be inserted into `article`
      - RejectedArticle: terminal rejection, to be moved to `article_rejected`
      - ProcessingError: classified error (retryable or permanent)
      - None: transient failure, retry on next run
    """
    article_id = data.get('article_id')
    url = data.get('url')
    blog_id = int(data.get("blog_id"))
    title = data.get("title")
    description = data.get("description")
    content = data.get("content")

    published_at = data.get('published_at')
    created_at = data.get('created_at')
    updated_at = data.get('updated_at')

    print(f"[START] Processing article: {article_id}")

    # --- Pre-LLM gate 1: empty title is never recoverable. ---
    if not (title or "").strip():
        print(f"[REJECT] {article_id} — empty title")
        return _build_rejection(data, reason=REASON_EMPTY_TITLE, quality_score=1), None, None

    try:
        parsed_text = await asyncio.to_thread(parse_article_text_from_html, content)
        parsed_is_empty = not parsed_text.strip()
        video_present = has_video_embed(content)

        # --- Pre-LLM gate 2: empty body AND no video embed → nothing to judge. ---
        if parsed_is_empty and not video_present:
            print(f"[REJECT] {article_id} — empty content, no video")
            return _build_rejection(data, reason=REASON_EMPTY_CONTENT, quality_score=1), None, None

        print(f"[PREPROCESS] Article ID: {article_id}")

        if parsed_is_empty and video_present:
            # Video-centric post (Toss SLASH / Naver DAN style): skip body
            # preprocessing, score on title + description alone. Tell the LLM
            # explicitly that this is a video post so short prose isn't penalized.
            print(f"[VIDEO-ONLY] {article_id} — scoring on title/description only")
            desc_processed = await asyncio.to_thread(PREPROCESSOR.process, description)
            preprocessed = ""
            llm_query = (
                f"Title : {title}, Description : {desc_processed}, "
                f"Body Content : [NOTE: This is a video-centric post (e.g. conference session). "
                f"Body text is minimal because the main content is an embedded video. "
                f"Score quality based on the title and description.]"
            )
        else:
            # Normal path.
            preprocessed, desc_processed = await asyncio.gather(
                asyncio.to_thread(PREPROCESSOR.process, parsed_text),
                asyncio.to_thread(PREPROCESSOR.process, description),
            )
            # 4K context window 보호를 위해 본문 길이 제한 (~6000자 ≈ ~3000 토큰)
            max_body_len = 6000
            if len(preprocessed) > max_body_len:
                preprocessed = preprocessed[:max_body_len]
                print(f"[TRUNCATE] Body truncated to {max_body_len} chars for article {article_id}")
            llm_query = f"Title : {title}, Description : {desc_processed}, Body Content : {preprocessed}"

        print(f"[PREDICT] Article ID: {article_id}")
        async with LLM_SEMAPHORE:
            inference = await MODEL.predict(llm_query, False, False)

        predict = inference.parsed

        if predict is None:
            # LLM returned unparseable output after 3 internal retries.
            # Route to article_rejected so the queue stays healthy.
            print(f"[REJECT] {article_id} — LLM extraction failed (None after retries)")
            return _build_rejection(
                data,
                reason=REASON_LLM_FAILED,
                quality_score=1,
            ), llm_query, inference

        # --- Post-LLM gate: quality score below threshold → reject terminally. ---
        if predict.quality_score < QUALITY_REJECT_THRESHOLD:
            print(f"[REJECT] {article_id} — low quality score={predict.quality_score}")
            return _build_rejection(
                data,
                reason=f"{REASON_LOW_QUALITY_PREFIX}_{predict.quality_score}",
                quality_score=predict.quality_score,
            ), llm_query, inference

        # Accepted. Build the row for the article table.
        final_content = preprocessed  # empty string for video-only posts
        korean_chars = sum(1 for c in final_content if '\uac00' <= c <= '\ud7a3' or '\u3131' <= c <= '\u318e')
        total_alpha = sum(1 for c in final_content if c.isalpha())
        lang = "ko" if total_alpha > 0 and korean_chars / total_alpha >= 0.3 else "en"

        values = (
            article_id,
            blog_id,
            url,
            title,
            data.get("thumbnail"),
            desc_processed,
            "\t".join(predict.keywords) if predict.keywords else None,
            predict.focusing.value,
            final_content,
            len(final_content),
            lang,
            predict.quality_score,
            predict.difficulty,
            predict.content_type.value if predict.content_type else None,
            predict.summary,
            PROMPT_VERSION,
            created_at,
            updated_at,
            published_at,
        )

        return (values, article_id, predict.quality_score), llm_query, inference

    except Exception as e:
        category, severity = classify_exception(e, context="article_processing")
        error = ProcessingError(
            article_id=article_id,
            category=category,
            severity=severity,
            message=str(e),
            original_exception=e
        )
        print(f"[ERROR] {error}")
        return error


async def process_embedding(row: dict, embedder: TextEmbeddings) -> dict:
    """개별 row에 대해 임베딩 생성"""
    async with EMBEDDING_SEMAPHORE:
        print(f"[Ollama Embedding] Embedding {row['article_id']}...")
        embedding = await embedder(
            title=row["title"],
            description=row.get("description", ""),
            keywords=row["keywords"],
            content=row["content"],
            dimensions=int(os.getenv("EMBEDDING_SIZE"))
        )
        # Enrich payload with metadata for downstream ranking/filtering
        published_at = row.get("published_at")
        pub_epoch = 0.0
        if published_at is not None:
            try:
                pub_epoch = float(published_at.timestamp()) if hasattr(published_at, 'timestamp') else float(published_at)
            except (TypeError, ValueError):
                pub_epoch = 0.0

        return {
            "id": row["article_id"],
            "vector": embedding,
            "payload": {
                "article_id": row["article_id"],
                "category": row.get("category_id"),
                "published_at": pub_epoch,
                "quality_score": row.get("quality_score"),
                "difficulty": row.get("difficulty"),
                "content_type": row.get("content_type"),
                "prompt_version": row.get("prompt_version"),
                "blog_id": row.get("blog_id"),
                "content_length": row.get("content_length"),
                "lang": row.get("lang"),
            }
        }

def lambda_handler(event=None, context=None):
    return asyncio.run(main_async())


async def main_async():
    logger.info("Harvest post started", stage="init")
    conn = Connection()

    async def delete_from_queue(ids):
        if not ids:
            return
        q = text("DELETE FROM article_queue WHERE article_id IN :ids").bindparams(
            bindparam("ids", expanding=True)
        )
        await asyncio.to_thread(conn.session_execute, q, {"ids": ids})

    code_metadata = get_metadata()
    sql_metadata = conn.execute(
        get_metadata(sql=True)
    )['model_key'][0]

    # LLM Model | Embedding Model | Metadata Update
    if code_metadata != sql_metadata:
        print("[LLM Config] Updating...")
        update_query, update_params, insert_query = update_model_config_query()
        conn.session_execute(update_query, update_params)
        conn.session_execute(insert_query)

    print("[FETCH] Getting articles from queue...")
    queued = conn.execute(QUEUE_QUERY)
    print(f"[FETCH DONE] {len(queued)} articles fetched.")

    if queued is None or len(queued) == 0:
        return HTTPResponse(HTTPStatus.OK, "Article Queue Empty").get_response()

    # Inference logger (Phase 1 평가 인프라)
    inf_logger = None
    if ENABLE_INFERENCE_LOG:
        inf_logger = InferenceLogger(
            model_key=LLM_CONFIG.model,
            model_version=LLM_CONFIG.model_version,
            temperature=LLM_CONFIG.temperature,
        )

    try:
        # 1단계: 모든 article 병렬 처리 (전처리 + LLM)
        tasks = [process_article(row) for row in queued.to_dict(orient="records")]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        insert_rows = []
        successful_ids = []          # article_ids headed for the article table
        rejected_articles = []       # RejectedArticle instances headed for article_rejected
        processing_errors = []

        for raw_result in results:
            if isinstance(raw_result, Exception):
                print(f"[ERROR] Task exception: {raw_result}")
                continue
            if isinstance(raw_result, ProcessingError):
                processing_errors.append(raw_result)
                if raw_result.is_retryable:
                    print(f"[RETRY-LATER] {raw_result.article_id} will be retried (transient error)")
                else:
                    print(f"[SKIP] {raw_result.article_id} skipped (permanent error)")
                continue

            # process_article은 항상 (result, llm_query, inference) 튜플 반환
            result, llm_query, inference = raw_result

            # 추론 로깅 (LLM 호출이 있었던 경우만)
            if inf_logger and inference is not None:
                if isinstance(result, RejectedArticle):
                    outcome = f"rejected_{result.reject_reason}"
                else:
                    outcome = "accepted"
                inf_logger.log(
                    article_id=result.article_id if isinstance(result, RejectedArticle) else result[1],
                    input_text=llm_query,
                    result=inference,
                    outcome=outcome,
                )

            if isinstance(result, RejectedArticle):
                rejected_articles.append(result)
                continue
            if result:
                values, article_id, quality_score = result
                insert_rows.append((values, quality_score))
                successful_ids.append(article_id)

        # --- 2단계: 거부 아티클을 article_rejected로 이관 ---
        if rejected_articles:
            print(f"[REJECTED] Moving {len(rejected_articles)} articles to article_rejected")
            rejected_dicts = [asdict(r) for r in rejected_articles]
            await asyncio.to_thread(conn.session_execute, REJECTED_INSERT_QUERY, rejected_dicts)

            rejected_ids = [r.article_id for r in rejected_articles]
            await delete_from_queue(rejected_ids)

        # 승인된 아티클이 하나도 없으면 여기서 종료 (queue는 이미 정리됨)
        if not insert_rows:
            if inf_logger:
                await inf_logger.flush(conn)
            await asyncio.to_thread(conn.close)
            return HTTPResponse(HTTPStatus.OK, "No accepted articles this batch").get_response()

        # --- 3단계: 임베딩 및 벡터 DB 저장 (승인된 것만) ---
        print(f"[INSERT] Processing {len(insert_rows)} records...")
        insert_dicts = []
        for values, quality_score in insert_rows:
            d = dict(zip(COLUMN_NAMES, values))
            insert_dicts.append(d)

        print(f"[Ollama Embedding & Qdrant] Process Starting...")
        embedder = TextEmbeddings()
        store = QdrantVectorStore(
            collection_name="bite-vectordb",
            vector_dim=int(os.getenv("EMBEDDING_SIZE")),
        )

        embedding_tasks = [
            process_embedding(row, embedder)
            for row in insert_dicts
        ]
        embedding_results = await asyncio.gather(*embedding_tasks, return_exceptions=True)

        qdrant_points = []
        failed_ids = []
        for i, result in enumerate(embedding_results):
            if isinstance(result, Exception):
                failed_ids.append(insert_dicts[i]['article_id'])
                print(f"[Embedding Error] Article ID: {insert_dicts[i]['article_id']} - {result}")
            else:
                qdrant_points.append(result)

        if not qdrant_points:
            await asyncio.to_thread(conn.close)
            return HTTPResponse(HTTPStatus.INTERNAL_SERVER_ERROR, "All embeddings failed").get_response()

        try:
            print(f"[Qdrant] Batch upserting {len(qdrant_points)} points...")
            await asyncio.to_thread(store.upsert_points, qdrant_points)
        except Exception as e:
            error_msg = f"[Qdrant Error] Batch upsert failed: {e}"
            print(error_msg)
            await asyncio.to_thread(conn.close)
            return HTTPResponse(HTTPStatus.INTERNAL_SERVER_ERROR, error_msg).get_response()

        # 임베딩 실패한 건 article 테이블에도 안 넣고 queue에 유지 → 다음 실행에서 재처리
        if failed_ids:
            print(f"[RETAIN] Keeping {len(failed_ids)} failed articles in queue for retry: {failed_ids}")
            insert_dicts = [d for d in insert_dicts if d['article_id'] not in failed_ids]
            successful_ids = [aid for aid in successful_ids if aid not in failed_ids]

        if insert_dicts:
            await asyncio.to_thread(conn.session_execute, INSERT_QUERY, insert_dicts)

        # 승인된 아티클을 queue에서 제거
        if successful_ids:
            print(f"[DELETE] Removing {len(successful_ids)} successfully processed articles from queue...")
            await delete_from_queue(successful_ids)

    except Exception as e:
        print(f"[FATAL ERROR] {str(e)}")
        if inf_logger:
            await inf_logger.flush(conn)
        await asyncio.to_thread(conn.close)
        return HTTPResponse(HTTPStatus.INTERNAL_SERVER_ERROR, str(e)).get_response()

    if inf_logger:
        await inf_logger.flush(conn)
    await asyncio.to_thread(conn.close)
    logger.info(
        "Harvest post completed",
        stage="done",
        processed=len(successful_ids),
        rejected=len(rejected_articles),
    )
    return HTTPResponse(HTTPStatus.CREATED).get_response()


async def run_continuous():
    """queue가 빌 때까지 연속 처리"""
    batch_num = 0
    total_processed = 0

    while True:
        batch_num += 1
        print(f"\n{'='*60}")
        print(f"[BATCH {batch_num}] Starting... (total processed so far: {total_processed})")
        print(f"{'='*60}")

        result = await main_async()
        status_code = result.get('statusCode', 500)

        if status_code == 200:  # Queue empty
            print(f"\n[DONE] Queue is empty. Total processed: {total_processed}")
            break
        elif status_code == 201:  # Success
            batch_size = int(os.getenv('LIMIT', 10))
            total_processed += batch_size
            print(f"[BATCH {batch_num}] Completed. Running total: {total_processed}")
        else:
            print(f"[BATCH {batch_num}] Error: {result}. Continuing to next batch...")

    return total_processed


def wait_for_vllm():
    """vLLM 서버 준비 대기"""
    import requests
    import time
    vllm_url = os.getenv('VLLM_BASE_URL', 'http://localhost:8000/v1')
    health_url = vllm_url.replace('/v1', '/health')
    max_wait = 600
    waited = 0
    while waited < max_wait:
        try:
            r = requests.get(health_url, timeout=5)
            if r.status_code == 200:
                print(f"[READY] vLLM server ready (waited {waited}s)")
                return True
        except Exception:
            pass
        time.sleep(5)
        waited += 5
        if waited % 30 == 0:
            print(f"[WAIT] vLLM not ready yet... ({waited}s)")
    print(f"[ERROR] vLLM not ready after {max_wait}s")
    return False


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Harvest Post - Local GPU Runner")
    parser.add_argument("--shutdown", action="store_true", help="Shutdown after processing")
    parser.add_argument("--continuous", action="store_true", help="Process until queue is empty")
    args = parser.parse_args()

    if not wait_for_vllm():
        sys.exit(1)

    if args.continuous:
        total = asyncio.run(run_continuous())
        print(f"\n[FINAL] Processed {total} articles total.")
    else:
        result = lambda_handler()
        print(f"[RESULT] {result}")

    if args.shutdown:
        print("[SHUTDOWN] Shutting down GPU server...")
        subprocess.run(["sudo", "shutdown", "-h", "now"])