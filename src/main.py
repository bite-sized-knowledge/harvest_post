import asyncio
import os
from http import HTTPStatus
from db_conn import Connection
from config import INSERT_QUERY, CATEGORY_DICT
from response import HTTPResponse
from llm_pipeline import LangChainModel
from preprocessing import BlogPostProcessor

# Call Preprocessor
PREPROCESSOR = BlogPostProcessor()

# Call Model
MODEL = LangChainModel()

async def process_article(data, conn):
    """비동기로 개별 데이터를 처리하는 함수"""
    blog_id = int(data.get("blog_id"))
    text = data.get("content")

    print(f"[START] Processing article: {data.get('article_id')}")

    try:
        # 텍스트 전처리 및 모델 예측을 비동기로 실행
        print(f"[PREPROCESS] Article ID: {data.get('article_id')}")
        preprocessed = await asyncio.to_thread(PREPROCESSOR.process, text, blog_id)

        print(f"[PREDICT] Article ID: {data.get('article_id')}")
        predict = await asyncio.to_thread(MODEL.predict, preprocessed)

        values = (
            data.get("article_id"),
            blog_id,
            data.get("url"),
            data.get("title"),
            data.get("thumbnail"),
            data.get("description"),
            "\t".join(predict.keywords),
            CATEGORY_DICT.get(predict.focusing, "NULL"),
            preprocessed,
            predict.content_length,
            predict.lang,
            data.get("published_at"),
        )

        print(f"[DB INSERT] Article ID: {data.get('article_id')}")
        await asyncio.to_thread(conn._raw_execute, INSERT_QUERY, values)

        print(f"[DONE] Article ID: {data.get('article_id')} inserted successfully.")

    except Exception as e:
        print(f"[ERROR] Article ID: {data.get('article_id')} - {str(e)}")
        raise e

def lambda_handler(event, context):
    return asyncio.run(lambda_handler_async())

async def lambda_handler_async():
    """비동기 Lambda 핸들러"""
    print("[LAMBDA START] Connecting to DB...")
    conn = Connection()

    print("[FETCH] Getting articles from queue...")
    queued = conn.execute(
        """
        SELECT
            article_id,
            blog_id,
            url,
            title,
            thumbnail,
            description,
            content
        FROM 
            article_queue
        LIMIT 5;
        """
    )
    print(f"[FETCH DONE] {len(queued)} articles fetched.")

    try:
        tasks = [process_article(row, conn) for row in queued.to_dict(orient="records")]
        await asyncio.gather(*tasks)

    except Exception as e:
        await asyncio.to_thread(conn.close)
        print(f"[FATAL ERROR] {str(e)}")
        response = HTTPResponse(HTTPStatus.INTERNAL_SERVER_ERROR, str(e))
        return response.get_response()

    await asyncio.to_thread(conn.close)
    print("[LAMBDA DONE] All tasks completed and DB connection closed.")
    response = HTTPResponse(HTTPStatus.CREATED)
    return response.get_response()