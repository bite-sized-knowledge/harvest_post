import asyncio
import os
from http import HTTPStatus
from db_conn import Connection
from embedder import TextEmbeddings
from qdrant_config import QdrantVectorStore
from config import INSERT_QUERY, QUEUE_QUERY, COLUMN_NAMES, get_metadata, update_model_config_query
from response import HTTPResponse
from llm_pipeline import LangChainModel
from preprocessing import BlogPostProcessor, parse_article_text_from_html
from sqlalchemy.sql import text, bindparam

# Concurrency limits
LLM_SEMAPHORE = asyncio.Semaphore(5)  # OpenAI API rate limit
EMBEDDING_SEMAPHORE = asyncio.Semaphore(10)  # Bedrock rate limit

# Call Preprocessor
PREPROCESSOR = BlogPostProcessor()

# Call Model
MODEL = LangChainModel()


async def process_article(data):
    """비동기로 개별 데이터를 처리하는 함수"""
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

    try:
        parsed_text = await asyncio.to_thread(parse_article_text_from_html, content)
        if not parsed_text.strip():
            print(f"[SKIP] Article ID: {article_id} - Empty content after parsing")
            return None

        print(f"[PREPROCESS] Article ID: {article_id}")
        # 병렬로 본문과 description 전처리
        preprocessed, desc_processed = await asyncio.gather(
            asyncio.to_thread(PREPROCESSOR.process, parsed_text),
            asyncio.to_thread(PREPROCESSOR.process, description)
        )

        print(f"[PREDICT] Article ID: {article_id}")
        query = f"Title : {title}, Description : {desc_processed}, Body Content : {preprocessed}"

        # LLM 호출에 세마포어 적용 (async native)
        async with LLM_SEMAPHORE:
            predict = await MODEL.predict(query, False, False)

        if predict is None:
            return None

        content = predict.content

        values = (
            article_id,
            blog_id,
            url,
            data.get("title"),
            data.get("thumbnail"),
            desc_processed,
            "\t".join(predict.keywords) if predict.keywords else None,
            predict.focusing.value,
            content,
            len(content),
            predict.lang,
            published_at,
            created_at,
            updated_at,
        )

        return values, article_id

    except Exception as e:
        print(f"[ERROR] Article ID: {article_id} - {str(e)}")
        return None


async def process_embedding(row: dict, embedder: TextEmbeddings) -> dict:
    """개별 row에 대해 임베딩 생성"""
    async with EMBEDDING_SEMAPHORE:
        print(f"[AWS Bedrock] Embedding {row['article_id']}...")
        embedding = await embedder(
            title=row["title"],
            description=row.get("description", ""),
            keywords=row["keywords"],
            content=row["content"],
            dimensions=int(os.getenv("EMBEDDING_SIZE"))
        )
        return {
            "id": row["article_id"],
            "vector": embedding,
            "payload": {
                "article_id": row["article_id"],
                "category": row["category_id"]
            }
        }

def lambda_handler(event, context):
    return asyncio.run(lambda_handler_async())


async def lambda_handler_async():
    print("[LAMBDA START] Connecting to DB...")
    conn = Connection()

    code_metadata = get_metadata()
    sql_metadata = conn.execute(
        get_metadata(sql=True)
    )['model_key'][0]

    # LLM Model | Embedding Model | Metadata Update
    if code_metadata != sql_metadata:
        print("[LLM Config] Updating...")
        update_query, insert_query = update_model_config_query()
        conn._raw_execute(update_query)
        conn._raw_execute(insert_query)

    print("[FETCH] Getting articles from queue...")
    queued = conn.execute(QUEUE_QUERY)
    print(f"[FETCH DONE] {len(queued)} articles fetched.")

    if queued is None or len(queued) == 0:
        return HTTPResponse(HTTPStatus.OK, "Article Queue Empty").get_response()

    try:
        # 1단계: 모든 article 병렬 처리 (전처리 + LLM)
        tasks = [process_article(row) for row in queued.to_dict(orient="records")]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        insert_rows = []
        successful_ids = []

        for result in results:
            if isinstance(result, Exception):
                print(f"[ERROR] Task exception: {result}")
                continue
            if result:
                values, article_id = result
                insert_rows.append(values)
                successful_ids.append(article_id)

        # 모든 article 처리 실패 시
        if not insert_rows:
            await asyncio.to_thread(conn.close)
            return HTTPResponse(HTTPStatus.INTERNAL_SERVER_ERROR, "All articles failed to process").get_response()

        # 2단계: 임베딩 및 벡터 DB 저장
        print(f"[INSERT] Processing {len(insert_rows)} records...")
        insert_dicts = [
            dict(zip(COLUMN_NAMES, row)) for row in insert_rows
        ]

        print(f"[AWS Bedrock & Qdrant] Process Starting...")
        embedder = TextEmbeddings()
        store = QdrantVectorStore(
            collection_name="bite-vectordb",
            vector_dim=int(os.getenv("EMBEDDING_SIZE")),
        )

        # 모든 임베딩을 병렬로 생성
        embedding_tasks = [
            process_embedding(row, embedder)
            for row in insert_dicts
        ]
        embedding_results = await asyncio.gather(*embedding_tasks, return_exceptions=True)

        # 성공한 임베딩만 필터링
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

        # Qdrant 배치 upsert (한 번에 모든 포인트 저장)
        try:
            print(f"[Qdrant] Batch upserting {len(qdrant_points)} points...")
            await asyncio.to_thread(store.upsert_points, qdrant_points)
        except Exception as e:
            error_msg = f"[Qdrant Error] Batch upsert failed: {e}"
            print(error_msg)
            await asyncio.to_thread(conn.close)
            return HTTPResponse(HTTPStatus.INTERNAL_SERVER_ERROR, error_msg).get_response()

        # 실패한 article 제외하고 DB insert
        if failed_ids:
            insert_dicts = [d for d in insert_dicts if d['article_id'] not in failed_ids]
            successful_ids = [aid for aid in successful_ids if aid not in failed_ids]

        if insert_dicts:
            await asyncio.to_thread(conn.session_execute, INSERT_QUERY, insert_dicts)

        # 성공한 article_id만 큐에서 삭제
        if successful_ids:
            print(f"[DELETE] Removing {len(successful_ids)} articles from queue...")
            delete_query = text("DELETE FROM article_queue WHERE article_id IN :ids").bindparams(
                bindparam("ids", expanding=True)
            )
            await asyncio.to_thread(conn.session_execute, delete_query, {"ids": successful_ids})

    except Exception as e:
        print(f"[FATAL ERROR] {str(e)}")
        await asyncio.to_thread(conn.close)
        return HTTPResponse(HTTPStatus.INTERNAL_SERVER_ERROR, str(e)).get_response()

    await asyncio.to_thread(conn.close)
    print(f"[LAMBDA DONE] {len(successful_ids)} articles processed successfully.")
    return HTTPResponse(HTTPStatus.CREATED).get_response()