"""영어 summary를 한국어로 재생성하는 backfill.

summary만 업데이트 (category, quality_score 등은 유지).
LLM에 전체 분류를 다시 시키되 summary만 DB에 반영.

Usage:
    cd src && DB_HOST=127.0.0.1 VLLM_BASE_URL=http://192.168.219.101:8000/v1 \
        python3 ../scripts/backfill_summary_ko.py
"""
import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from db_conn import Connection
from llm_pipeline.model import LangChainModel
from sqlalchemy import text

UPDATE_SQL = text("UPDATE article SET summary = :summary WHERE article_id = :article_id")


def build_llm_query(title, description, content):
    title = title or ""
    description = description or ""
    content = (content or "")[:6000]
    return f"Title : {title}, Description : {description}, Body Content : {content}"


async def classify_one(model, article_id, llm_query, semaphore):
    async with semaphore:
        inference = await model.predict(llm_query, show_prompt=False, show_output=False)
    if inference.parsed and inference.parsed.summary:
        return {"article_id": article_id, "summary": inference.parsed.summary, "success": True}
    return {"article_id": article_id, "success": False}


async def main():
    conn = Connection()
    model = LangChainModel()
    semaphore = asyncio.Semaphore(16)

    df = conn.execute("""
        SELECT article_id, title, description, content FROM article
        ORDER BY published_at DESC
    """)
    total = len(df)
    print(f"[BACKFILL-KO] {total} articles to re-summarize")

    rows = df.to_dict(orient="records")
    batch_size = 50
    updated = 0
    failed = 0
    t_start = time.time()

    for batch_start in range(0, total, batch_size):
        batch = rows[batch_start:batch_start + batch_size]
        batch_num = batch_start // batch_size + 1
        print(f"\n[BATCH {batch_num}] {batch_start+1}-{min(batch_start+batch_size, total)}/{total}")

        tasks = [
            classify_one(model, r["article_id"],
                         build_llm_query(r["title"], r["description"], r["content"]),
                         semaphore)
            for r in batch
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        update_rows = []
        for r in results:
            if isinstance(r, Exception):
                failed += 1
                continue
            if not r["success"]:
                failed += 1
                continue
            update_rows.append({"article_id": r["article_id"], "summary": r["summary"]})

        if update_rows:
            await asyncio.to_thread(conn.session_execute, UPDATE_SQL, update_rows)

        updated += len(update_rows)
        elapsed = time.time() - t_start
        rate = updated / elapsed if elapsed > 0 else 0
        remaining = total - batch_start - batch_size
        eta = remaining / rate if rate > 0 else 0
        print(f"  Updated: {len(update_rows)}, Total: {updated}/{total}, Rate: {rate:.1f}/s, ETA: {eta:.0f}s")

    elapsed = time.time() - t_start
    print(f"\n[DONE] {updated} updated, {failed} failed, {elapsed:.0f}s")
    conn.close()


if __name__ == "__main__":
    asyncio.run(main())
