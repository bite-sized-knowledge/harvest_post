"""영어 summary를 한국어로 변환하는 backfill.

2단계 전략:
1. 전체 분류 프롬프트로 summary 생성 (모델이 영어 아티클에서 영어 summary를 줄 수 있음)
2. 영어 summary를 별도 경량 번역 프롬프트로 한국어 전환

Usage:
    cd src && DB_HOST=127.0.0.1 VLLM_BASE_URL=http://192.168.219.101:8000/v1 \
        python3 ../scripts/backfill_summary_ko.py
"""
import asyncio
import os
import re
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from db_conn import Connection
from config import LLM_CONFIG, VLLM_BASE_URL
from llm_pipeline.schemas import clean_summary, _CJK_RE
from langchain_openai import ChatOpenAI
from sqlalchemy import text

UPDATE_SQL = text("UPDATE article SET summary = :summary WHERE article_id = :article_id")

_KOREAN_RE = re.compile(r'[\uac00-\ud7a3\u3131-\u318e]')


async def translate_to_korean(llm: ChatOpenAI, eng_summary: str, semaphore) -> str:
    """영어 summary를 한국어 80자 이내로 번역."""
    prompt = f"다음 영문을 한국어 한 문장(80자 이내)으로 번역하라. 종결어미(~다/~한다)로 끝내라. 한자 사용 금지.\n\n{eng_summary}"
    async with semaphore:
        response = await llm.ainvoke(prompt)
    return clean_summary(response.content.strip())


async def main():
    conn = Connection()
    semaphore = asyncio.Semaphore(16)

    # 번역 전용 경량 LLM (전체 분류 불필요)
    llm = ChatOpenAI(
        model=LLM_CONFIG.model,
        base_url=VLLM_BASE_URL,
        api_key="not-needed",
        temperature=0.1,
    )

    df = conn.execute("""
        SELECT article_id, summary FROM article
        WHERE summary IS NULL OR summary = 'N/A' OR summary REGEXP '^[A-Za-z]'
        ORDER BY published_at DESC
    """)
    total = len(df)
    print(f"[TRANSLATE-KO] {total} summaries to translate")

    if total == 0:
        print("[DONE] Nothing to do")
        conn.close()
        return

    rows = df.to_dict(orient="records")
    batch_size = 50
    updated = 0
    failed = 0
    t_start = time.time()

    for batch_start in range(0, total, batch_size):
        batch = rows[batch_start:batch_start + batch_size]
        batch_num = batch_start // batch_size + 1
        print(f"\n[BATCH {batch_num}] {batch_start+1}-{min(batch_start+batch_size, total)}/{total}")

        tasks = []
        for r in batch:
            s = r.get("summary", "")
            if not s or s == "N/A":
                continue
            tasks.append((r["article_id"], translate_to_korean(llm, s, semaphore)))

        if not tasks:
            failed += len(batch)
            print(f"  Skipped (no summary to translate)")
            continue

        ids = [t[0] for t in tasks]
        results = await asyncio.gather(*[t[1] for t in tasks], return_exceptions=True)

        update_rows = []
        for aid, result in zip(ids, results):
            if isinstance(result, Exception):
                failed += 1
                continue
            if not _KOREAN_RE.search(result):
                failed += 1
                continue
            update_rows.append({"article_id": aid, "summary": result})

        if update_rows:
            await asyncio.to_thread(conn.session_execute, UPDATE_SQL, update_rows)

        updated += len(update_rows)
        elapsed = time.time() - t_start
        rate = updated / elapsed if elapsed > 0 else 0
        remaining = total - batch_start - batch_size
        eta = remaining / rate if rate > 0 else 0
        print(f"  Translated: {len(update_rows)}, Total: {updated}/{total}, Rate: {rate:.1f}/s, ETA: {eta:.0f}s")

    elapsed = time.time() - t_start
    print(f"\n[DONE] {updated} translated, {failed} failed, {elapsed:.0f}s")
    conn.close()


if __name__ == "__main__":
    asyncio.run(main())
