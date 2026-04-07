"""Golden dataset 생성 스크립트.

article + article_rejected 테이블에서 층화 표본을 추출하고,
현재 모델의 예측을 baseline으로 첨부한 JSONL 파일을 출력한다.
사람이 리뷰/수정 후 golden_dataset_import.py로 DB에 적재.

Usage:
    cd src && python ../scripts/golden_dataset_seed.py --output ../data/golden_seed.jsonl

    선택적으로 --run-model 플래그를 추가하면 현재 vLLM 모델로 예측을 실행:
    cd src && python ../scripts/golden_dataset_seed.py --output ../data/golden_seed.jsonl --run-model
"""
import argparse
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from db_conn import Connection
from preprocessing import BlogPostProcessor, parse_article_text_from_html

PREPROCESSOR = BlogPostProcessor()

# 카테고리별 표본 수
SAMPLES_PER_CATEGORY = 8
# article_rejected에서 borderline 표본 수
BORDERLINE_SAMPLES = 15


def build_frozen_input(title: str, description: str, content: str) -> str:
    """process_article과 동일한 로직으로 frozen_input 생성."""
    parsed_text = parse_article_text_from_html(content) if content else ""
    desc_processed = PREPROCESSOR.process(description) if description else ""

    if not parsed_text.strip():
        return (
            f"Title : {title}, Description : {desc_processed}, "
            f"Body Content : [NOTE: This is a video-centric post. "
            f"Body text is minimal because the main content is an embedded video. "
            f"Score quality based on the title and description.]"
        )

    preprocessed = PREPROCESSOR.process(parsed_text)
    if len(preprocessed) > 6000:
        preprocessed = preprocessed[:6000]
    return f"Title : {title}, Description : {desc_processed}, Body Content : {preprocessed}"


def sample_from_article(conn: Connection) -> list[dict]:
    """article 테이블에서 카테고리별 층화 표본 추출."""
    query = f"""
        SELECT article_id, blog_id, url, title, description, content,
               category_id, keywords, quality_score
        FROM (
            SELECT *,
                   ROW_NUMBER() OVER (PARTITION BY category_id ORDER BY RAND()) AS rn
            FROM article
            WHERE category_id IS NOT NULL
        ) ranked
        WHERE rn <= {SAMPLES_PER_CATEGORY}
        ORDER BY category_id, rn
    """
    df = conn.execute(query)
    return df.to_dict(orient="records") if df is not None and len(df) > 0 else []


def sample_from_rejected(conn: Connection) -> list[dict]:
    """article_rejected에서 borderline (quality_score=2) 표본 추출."""
    query = f"""
        SELECT article_id, blog_id, url, title, description, content,
               quality_score, reject_reason
        FROM article_rejected
        WHERE quality_score = 2
        ORDER BY RAND()
        LIMIT {BORDERLINE_SAMPLES}
    """
    df = conn.execute(query)
    return df.to_dict(orient="records") if df is not None and len(df) > 0 else []


async def run_model_predictions(rows: list[dict]) -> dict:
    """현재 vLLM 모델로 예측 실행 (선택적)."""
    from llm_pipeline import LangChainModel
    model = LangChainModel()
    predictions = {}
    for row in rows:
        aid = row["article_id"]
        frozen = row["frozen_input"]
        try:
            result = await model.predict(frozen, show_prompt=False, show_output=False)
            if result.parsed:
                predictions[aid] = {
                    "focusing": result.parsed.focusing.name,
                    "category_id": result.parsed.focusing.value,
                    "keywords": result.parsed.keywords,
                    "quality_score": result.parsed.quality_score,
                }
            else:
                predictions[aid] = None
        except Exception as e:
            print(f"[WARN] Prediction failed for {aid}: {e}")
            predictions[aid] = None
    return predictions


def main():
    parser = argparse.ArgumentParser(description="Golden dataset seed generator")
    parser.add_argument("--output", "-o", required=True, help="Output JSONL file path")
    parser.add_argument("--run-model", action="store_true", help="Run current model predictions as baseline")
    args = parser.parse_args()

    conn = Connection()

    print("[SAMPLE] Sampling from article table...")
    article_rows = sample_from_article(conn)
    print(f"  -> {len(article_rows)} articles sampled")

    print("[SAMPLE] Sampling borderline from article_rejected...")
    rejected_rows = sample_from_rejected(conn)
    print(f"  -> {len(rejected_rows)} rejected articles sampled")

    # frozen_input 생성
    all_rows = []
    for row in article_rows:
        frozen = build_frozen_input(
            title=row.get("title") or "",
            description=row.get("description") or "",
            content=row.get("content") or "",
        )
        all_rows.append({
            "article_id": row["article_id"],
            "url": row.get("url"),
            "title": row.get("title"),
            "source": "article",
            "frozen_input": frozen,
            "current_category_id": row.get("category_id"),
            "current_keywords": row.get("keywords"),
            "current_quality_score": row.get("quality_score"),
            # 사람이 채울 필드 (초기값은 현재 모델 예측)
            "human_category": row.get("category_id"),
            "human_keywords": row.get("keywords"),
            "human_quality": row.get("quality_score"),
            "notes": "",
        })

    for row in rejected_rows:
        frozen = build_frozen_input(
            title=row.get("title") or "",
            description=row.get("description") or "",
            content=row.get("content") or "",
        )
        all_rows.append({
            "article_id": row["article_id"],
            "url": row.get("url"),
            "title": row.get("title"),
            "source": "article_rejected",
            "frozen_input": frozen,
            "current_category_id": None,
            "current_keywords": None,
            "current_quality_score": row.get("quality_score"),
            "human_category": None,
            "human_keywords": None,
            "human_quality": row.get("quality_score"),
            "notes": f"rejected: {row.get('reject_reason', '')}",
        })

    # 선택적 모델 예측
    if args.run_model:
        print("[MODEL] Running current model predictions...")
        predictions = asyncio.run(run_model_predictions(all_rows))
        for row in all_rows:
            pred = predictions.get(row["article_id"])
            if pred:
                row["model_prediction"] = pred
                # rejected 아티클은 현재 카테고리 없으므로 모델 예측으로 채움
                if row["human_category"] is None:
                    row["human_category"] = pred["category_id"]
                if row["human_keywords"] is None:
                    row["human_keywords"] = "\t".join(pred["keywords"])

    # JSONL 출력
    os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        for row in all_rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    print(f"\n[DONE] {len(all_rows)} articles written to {args.output}")
    print("다음 단계:")
    print("  1. JSONL 파일을 열어 human_category, human_keywords, human_quality를 검토/수정")
    print("  2. python scripts/golden_dataset_import.py --input <file> 로 DB에 적재")

    conn.close()


if __name__ == "__main__":
    main()
