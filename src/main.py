import asyncio
import os
from http import HTTPStatus
from db_conn import Connection
from config import INSERT_QUERY, QUEUE_QUERY, CATEGORY_DICT
from response import HTTPResponse
from llm_pipeline import LangChainModel
from preprocessing import BlogPostProcessor

# Call Preprocessor
PREPROCESSOR = BlogPostProcessor()

# Call Model
MODEL = LangChainModel()

async def process_article(data, conn):
    """비동기로 개별 데이터를 처리하는 함수"""
    article_id = data.get('article_id')
    blog_id = int(data.get("blog_id"))
    text = data.get("content")
    description = data.get("description")

    published_at = data.get('published_at')
    created_at = data.get('created_at')
    updated_at = data.get('updated_at')

    print(f"[START] Processing article: {article_id}")

    try:
        # 텍스트 전처리 및 모델 예측을 비동기로 실행
        print(f"[PREPROCESS] Article ID: {article_id}")
        preprocessed = await asyncio.to_thread(PREPROCESSOR.process, text)
        desc_processed = await asyncio.to_thread(PREPROCESSOR.process, description)


        print(f"[PREDICT] Article ID: {article_id}")
        predict = await asyncio.to_thread(MODEL.predict, preprocessed)
        values = (
            article_id,
            blog_id,
            data.get("url"),
            data.get("title"),
            data.get("thumbnail"),
            desc_processed,
            "\t".join(predict.keywords),
            CATEGORY_DICT.get(predict.focusing, "NULL"),
            preprocessed,
            predict.content_length,
            predict.lang,
            published_at,
            created_at,
            updated_at,
        )

        print(f"[DB INSERT] Article ID: {article_id}")
        await asyncio.to_thread(conn._raw_execute, INSERT_QUERY, values)

        print(f"[DELETE QUEUE] Removing article_id={article_id} from article_queue...")
        await asyncio.to_thread(
            conn._raw_execute,
            "DELETE FROM article_queue WHERE article_id = %s",
            (article_id,)
        )

        print(f"[DONE] Article ID: {article_id} inserted successfully.")

    except Exception as e:
        print(f"[ERROR] Article ID: {article_id} - {str(e)}")
        # 예외 발생 시 건너뛰기
        return

def lambda_handler(event, context):
    return asyncio.run(lambda_handler_async())

async def lambda_handler_async():
    """비동기 Lambda 핸들러"""
    print("[LAMBDA START] Connecting to DB...")
    conn = Connection()

    print("[FETCH] Getting articles from queue...")
    queued = conn.execute(QUEUE_QUERY)
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