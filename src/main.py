import asyncio
import os
from http import HTTPStatus
from db_conn import Connection
from embedder import TextEmbeddings
from qdrant_config import QdrantVectorStore
from config import INSERT_QUERY, QUEUE_QUERY, COLUMN_NAMES
from response import HTTPResponse
from llm_pipeline import LangChainModel
from preprocessing import BlogPostProcessor, parse_article_text_from_url
from sqlalchemy.sql import text, bindparam


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

    published_at = data.get('published_at')
    created_at = data.get('created_at')
    updated_at = data.get('updated_at')

    print(f"[START] Processing article: {article_id}")

    try:
        text = await asyncio.to_thread(parse_article_text_from_url, url)
        if not text.strip():
            print(f"[SKIP] Article ID: {article_id} - Empty content after parsing")
            return None  # 본문이 없으면 처리하지 않음

        print(f"[PREPROCESS] Article ID: {article_id}")
        preprocessed = await asyncio.to_thread(PREPROCESSOR.process, text)
        desc_processed = await asyncio.to_thread(PREPROCESSOR.process, description)

        print(f"[PREDICT] Article ID: {article_id}")
        query = f"Title : {title}, Description : {desc_processed}, Body Content : {preprocessed}"
        predict = await asyncio.to_thread(
            MODEL.predict, # predict 함수 비동기
            query, # predict에 들어갈 Query 문
            False, # verbose option : show prompt
            False # verbose option : show llm output 

        )
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
        return None  # 에러 발생 시 처리 제외

def lambda_handler(event, context):
    return asyncio.run(lambda_handler_async())

async def lambda_handler_async():
    print("[LAMBDA START] Connecting to DB...")
    conn = Connection()

    print("[FETCH] Getting articles from queue...")
    queued = conn.execute(QUEUE_QUERY)
    print(f"[FETCH DONE] {len(queued)} articles fetched.")

    if queued is None or len(queued) == 0:
        return HTTPResponse(HTTPStatus.OK, "Article Queue Empty").get_response()

    try:
        tasks = [process_article(row) for row in queued.to_dict(orient="records")]
        results = await asyncio.gather(*tasks)

        insert_rows = []
        successful_ids = []

        for result in results:
            if result:
                values, article_id = result
                insert_rows.append(values)
                successful_ids.append(article_id)

        # ORM 기반 batch insert
        if insert_rows:
            print(f"[INSERT] Inserting {len(insert_rows)} records...")
            insert_dicts = [
                dict(zip(COLUMN_NAMES, row)) for row in insert_rows
            ]

            print(f"[AWS Bedrock & Qdrant] Process Starting...")
            embedder = TextEmbeddings()
            store = QdrantVectorStore(
                collection_name="bite-vectordb",
                vector_dim=int(os.getenv("VECTOR_DIM")),
            )

            TASK = ["AWS Bedrock", "Qdrant"]
            for row in insert_dicts:
                error_idx = 0
                try:
                    print(f"[AWS Bedrock] Embedding {row["article_id"]}...")

                    embedding = await embedder(
                        title=row["title"],
                        description=row.get("description", ""),
                        keywords=row["keywords"],
                        content=row["content"],
                        dimensions=int(os.getenv("VECTOR_DIM"))
                    )

                    error_idx += 1
                    print(f"[Qdrant] Storing {row["article_id"]} into Vector DB...")
                    store.upsert_points([{
                        "id" : row["article_id"],
                        "vector" : embedding,
                        "payload" : {
                            "article_id" : row["article_id"],
                            "category" : row["category_id"]
                        }
                    }])

                except Exception as e:
                    print(f"[{TASK[error_idx]} Error] Article ID : {row['article_id']} - {e}")
                    return None

            await asyncio.to_thread(conn.session_execute, INSERT_QUERY, insert_dicts)



        # 성공한 article_id만 삭제
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
    print("[LAMBDA DONE] All tasks completed and DB connection closed.")
    return HTTPResponse(HTTPStatus.CREATED).get_response()