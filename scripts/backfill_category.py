"""기존 article의 카테고리를 개선된 프롬프트로 재분류.

article 테이블의 content는 이미 전처리된 상태이므로
title + description + content로 llm_query를 구성하여 LLM 재추론.

Usage:
    cd src && DB_HOST=127.0.0.1 VLLM_BASE_URL=http://192.168.219.101:8000/v1 \
        python3 ../scripts/backfill_category.py [--dry-run] [--limit 100]
"""
import argparse
import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from db_conn import Connection
from llm_pipeline.model import LangChainModel
from config import LLM_CONFIG, ENABLE_INFERENCE_LOG
from llm_pipeline.inference_logger import InferenceLogger
from sqlalchemy import text

UPDATE_SQL = text("""
    UPDATE article
    SET category_id = :category_id,
        quality_score = :quality_score,
        keywords = :keywords,
        difficulty = :difficulty,
        content_type = :content_type,
        summary = :summary
    WHERE article_id = :article_id
""")


def build_llm_query(title: str, description: str, content: str) -> str:
    """article 테이블의 데이터로 llm_query 구성."""
    title = title or ""
    description = description or ""
    content = content or ""
    if len(content) > 6000:
        content = content[:6000]
    return f"Title : {title}, Description : {description}, Body Content : {content}"


async def classify_one(model: LangChainModel, article_id: str, llm_query: str,
                        semaphore: asyncio.Semaphore) -> dict:
    async with semaphore:
        inference = await model.predict(llm_query, show_prompt=False, show_output=False)

    if inference.parsed:
        return {
            "article_id": article_id,
            "category_id": inference.parsed.focusing.value,
            "quality_score": inference.parsed.quality_score,
            "keywords": "\t".join(inference.parsed.keywords),
            "difficulty": inference.parsed.difficulty,
            "content_type": inference.parsed.content_type.value if inference.parsed.content_type else None,
            "summary": inference.parsed.summary,
            "success": True,
            "inference": inference,
        }
    return {
        "article_id": article_id,
        "success": False,
        "inference": inference,
    }


async def main_async(args):
    conn = Connection()
    model = LangChainModel()
    semaphore = asyncio.Semaphore(16)

    inf_logger = None
    if ENABLE_INFERENCE_LOG:
        inf_logger = InferenceLogger(
            model_key=LLM_CONFIG.model,
            model_version=LLM_CONFIG.model_version,
            temperature=LLM_CONFIG.temperature,
        )

    # 전체 article 로드
    limit_clause = f"LIMIT {args.limit}" if args.limit else ""
    query = f"""
        SELECT article_id, title, description, content, category_id, quality_score
        FROM article
        ORDER BY published_at DESC
        {limit_clause}
    """
    df = conn.execute(query)
    total = len(df)
    print(f"[BACKFILL] {total} articles to reclassify")

    rows = df.to_dict(orient="records")

    # 배치 처리
    batch_size = 50
    updated = 0
    failed = 0
    changed_cat = 0
    t_start = time.time()

    for batch_start in range(0, total, batch_size):
        batch = rows[batch_start:batch_start + batch_size]
        batch_num = batch_start // batch_size + 1
        print(f"\n[BATCH {batch_num}] {batch_start+1}-{min(batch_start+batch_size, total)}/{total}")

        tasks = []
        for row in batch:
            llm_query = build_llm_query(
                row.get("title", ""),
                row.get("description", ""),
                row.get("content", ""),
            )
            tasks.append(classify_one(model, row["article_id"], llm_query, semaphore))

        results = await asyncio.gather(*tasks, return_exceptions=True)

        update_rows = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                print(f"  [ERROR] {batch[i]['article_id']}: {result}")
                failed += 1
                continue
            if not result["success"]:
                print(f"  [FAIL] {result['article_id']}: parse failed")
                failed += 1
                continue

            old_cat = batch[i].get("category_id")
            new_cat = result["category_id"]
            if old_cat != new_cat:
                changed_cat += 1

            update_rows.append({
                "article_id": result["article_id"],
                "category_id": result["category_id"],
                "quality_score": result["quality_score"],
                "keywords": result["keywords"],
                "difficulty": result["difficulty"],
                "content_type": result["content_type"],
                "summary": result["summary"],
            })

            if inf_logger:
                inf_logger.log(
                    article_id=result["article_id"],
                    input_text="[backfill]",
                    result=result["inference"],
                    outcome="backfill",
                )

        if update_rows and not args.dry_run:
            await asyncio.to_thread(conn.session_execute, UPDATE_SQL, update_rows)

        updated += len(update_rows)
        elapsed = time.time() - t_start
        rate = updated / elapsed if elapsed > 0 else 0
        eta = (total - batch_start - batch_size) / rate if rate > 0 else 0
        print(f"  Updated: {len(update_rows)}, Changed cat: {changed_cat}, Rate: {rate:.1f}/s, ETA: {eta:.0f}s")

    # Flush inference logs
    if inf_logger:
        await inf_logger.flush(conn)

    elapsed = time.time() - t_start
    print(f"\n{'='*60}")
    print(f"[DONE] {updated} updated, {failed} failed, {changed_cat} category changed")
    print(f"Time: {elapsed:.0f}s ({updated/elapsed:.1f} articles/s)")
    if args.dry_run:
        print("[DRY-RUN] No actual DB updates were made")
    print(f"{'='*60}")

    conn.close()


def main():
    parser = argparse.ArgumentParser(description="Backfill article categories with improved prompt")
    parser.add_argument("--dry-run", action="store_true", help="Don't update DB, just show results")
    parser.add_argument("--limit", type=int, default=None, help="Limit number of articles to process")
    args = parser.parse_args()
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
