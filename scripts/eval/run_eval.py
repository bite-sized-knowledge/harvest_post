"""Golden dataset 대상 오프라인 모델 평가.

Usage:
    cd src && python ../scripts/eval/run_eval.py \
        --model-key "qwen3.5:9b" \
        --model-version "QuantTrio/Qwen3.5-9B-AWQ" \
        --vllm-url http://localhost:8000/v1

평가 결과는 llm_eval_result 테이블에 저장되고,
개별 추론 로그는 llm_inference_log에 is_shadow=TRUE로 기록됨.
"""
import argparse
import asyncio
import json
import os
import sys
import uuid

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from db_conn import Connection
from config import LLM_CONFIG, VLLM_BASE_URL
from llm_pipeline.model import LangChainModel, InferenceResult
from llm_pipeline.inference_logger import InferenceLogger
from sqlalchemy import text

from metrics_classification import compute_classification_metrics, format_confusion_matrix
from metrics_quality import compute_quality_metrics
from metrics_keywords import compute_keyword_metrics


CATEGORY_NAMES = {
    1: "Frontend", 2: "Backend", 3: "Mobile", 4: "AI/ML",
    5: "Database", 6: "Security", 7: "Design", 8: "PM",
    9: "DevOps", 10: "HW/IoT", 11: "QA/Test", 12: "Culture", 13: "ETC",
}


def load_golden(conn: Connection) -> list[dict]:
    """llm_eval_golden 테이블에서 전체 golden dataset 로드."""
    df = conn.execute("SELECT * FROM llm_eval_golden")
    if df is None or len(df) == 0:
        return []
    return df.to_dict(orient="records")


async def evaluate(
    model: LangChainModel,
    golden: list[dict],
    inf_logger: InferenceLogger,
) -> list[dict]:
    """Golden dataset 각 항목에 대해 모델 예측 실행."""
    results = []
    for i, entry in enumerate(golden):
        aid = entry["article_id"]
        frozen = entry["frozen_input"]
        print(f"  [{i+1}/{len(golden)}] {aid}")

        inference = await model.predict(frozen, show_prompt=False, show_output=False)

        outcome = "eval_success" if inference.parse_success else "eval_parse_fail"
        inf_logger.log(
            article_id=aid,
            input_text=frozen,
            result=inference,
            outcome=outcome,
        )

        model_cat = inference.parsed.focusing.value if inference.parsed else None
        model_kw = inference.parsed.keywords if inference.parsed else []
        model_qs = inference.parsed.quality_score if inference.parsed else None

        results.append({
            "article_id": aid,
            "human_category": entry["human_category"],
            "human_keywords": entry["human_keywords"].split("\t") if entry["human_keywords"] else [],
            "human_quality": entry["human_quality"],
            "model_category": model_cat,
            "model_keywords": model_kw,
            "model_quality": model_qs,
            "parse_success": inference.parse_success,
            "latency_ms": inference.latency_ms,
            "input_tokens": inference.input_tokens,
            "output_tokens": inference.output_tokens,
        })

    return results


def compute_all_metrics(results: list[dict]) -> dict:
    """모든 메트릭 집계."""
    # 파싱 성공한 것만 필터
    valid = [r for r in results if r["parse_success"] and r["model_category"] is not None]
    total = len(results)
    parse_success_rate = len(valid) / total if total > 0 else 0.0

    metrics = {
        "total": total,
        "valid": len(valid),
        "parse_success_rate": round(parse_success_rate, 4),
    }

    if not valid:
        return metrics

    # Classification
    human_cats = [r["human_category"] for r in valid]
    model_cats = [r["model_category"] for r in valid]
    cls_metrics = compute_classification_metrics(human_cats, model_cats)
    metrics["category_accuracy"] = cls_metrics["accuracy"]
    metrics["category_macro_f1"] = cls_metrics["macro_f1"]
    metrics["category_per_class"] = cls_metrics["per_class"]
    metrics["category_confusion"] = cls_metrics["confusion_matrix"]

    # Quality
    human_qs = [r["human_quality"] for r in valid]
    model_qs = [r["model_quality"] for r in valid]
    qs_metrics = compute_quality_metrics(human_qs, model_qs)
    metrics["quality_mae"] = qs_metrics["mae"]
    metrics["quality_spearman"] = qs_metrics["spearman"]
    metrics["quality_exact_match"] = qs_metrics["exact_match"]
    metrics["quality_within_1"] = qs_metrics["within_1"]
    metrics["quality_distribution"] = qs_metrics["distribution"]
    metrics["quality_confusion"] = qs_metrics["confusion_matrix"]

    # Keywords
    human_kws = [r["human_keywords"] for r in valid]
    model_kws = [r["model_keywords"] for r in valid]
    kw_metrics = compute_keyword_metrics(human_kws, model_kws)
    metrics["keyword_avg_jaccard"] = kw_metrics["avg_jaccard"]
    metrics["keyword_overlap_2_rate"] = kw_metrics["overlap_2_rate"]

    # Latency
    latencies = [r["latency_ms"] for r in results if r["latency_ms"] is not None]
    metrics["avg_latency_ms"] = round(sum(latencies) / len(latencies)) if latencies else None

    return metrics


def save_eval_result(conn: Connection, eval_run_id: str, model_key: str, model_version: str,
                     metrics: dict, per_article: list[dict]):
    """llm_eval_result 테이블에 결과 저장."""
    sql = text("""
        INSERT INTO llm_eval_result (
            eval_run_id, model_key, model_version, golden_count,
            category_accuracy, category_macro_f1,
            quality_mae, quality_spearman, quality_within_1,
            keyword_avg_relevance, parse_success_rate, avg_latency_ms,
            per_article_json
        ) VALUES (
            :eval_run_id, :model_key, :model_version, :golden_count,
            :category_accuracy, :category_macro_f1,
            :quality_mae, :quality_spearman, :quality_within_1,
            :keyword_avg_relevance, :parse_success_rate, :avg_latency_ms,
            :per_article_json
        )
    """)

    # per_article에서 non-serializable 제거
    safe_per_article = []
    for r in per_article:
        safe = {k: v for k, v in r.items()}
        safe_per_article.append(safe)

    conn.session_execute(sql, {
        "eval_run_id": eval_run_id,
        "model_key": model_key,
        "model_version": model_version,
        "golden_count": metrics.get("total", 0),
        "category_accuracy": metrics.get("category_accuracy"),
        "category_macro_f1": metrics.get("category_macro_f1"),
        "quality_mae": metrics.get("quality_mae"),
        "quality_spearman": metrics.get("quality_spearman"),
        "quality_within_1": metrics.get("quality_within_1"),
        "keyword_avg_relevance": metrics.get("keyword_avg_jaccard"),
        "parse_success_rate": metrics.get("parse_success_rate"),
        "avg_latency_ms": metrics.get("avg_latency_ms"),
        "per_article_json": json.dumps(safe_per_article, ensure_ascii=False),
    })


def print_report(metrics: dict, model_key: str, eval_run_id: str):
    """결과를 터미널에 출력."""
    print(f"\n{'='*60}")
    print(f"  Eval Report: {model_key}")
    print(f"  Run ID: {eval_run_id}")
    print(f"{'='*60}")
    print(f"  Golden samples    : {metrics.get('total', 0)}")
    print(f"  Parse success     : {metrics.get('parse_success_rate', 0):.1%}")
    print(f"  Avg latency       : {metrics.get('avg_latency_ms', 'N/A')} ms")
    print()
    print("  [Classification]")
    print(f"    Accuracy        : {metrics.get('category_accuracy', 'N/A')}")
    print(f"    Macro F1        : {metrics.get('category_macro_f1', 'N/A')}")
    print()
    print("  [Quality Score]")
    print(f"    MAE             : {metrics.get('quality_mae', 'N/A')}")
    print(f"    Spearman        : {metrics.get('quality_spearman', 'N/A')}")
    print(f"    Exact match     : {metrics.get('quality_exact_match', 'N/A')}")
    print(f"    Within +-1      : {metrics.get('quality_within_1', 'N/A')}")
    if metrics.get("quality_distribution"):
        dist = metrics["quality_distribution"]
        print(f"    Distribution H  : {dist.get('human', {})}")
        print(f"    Distribution M  : {dist.get('model', {})}")
    print()
    print("  [Keywords]")
    print(f"    Avg Jaccard     : {metrics.get('keyword_avg_jaccard', 'N/A')}")
    print(f"    Overlap>=2 rate : {metrics.get('keyword_overlap_2_rate', 'N/A')}")

    if metrics.get("category_confusion"):
        print()
        print("  [Confusion Matrix - Category]")
        labels = [CATEGORY_NAMES.get(i, str(i))[:6] for i in range(1, 14)]
        print(format_confusion_matrix(metrics["category_confusion"], labels))

    print(f"\n{'='*60}")


async def main_async(args):
    # vLLM URL override
    if args.vllm_url:
        os.environ["VLLM_BASE_URL"] = args.vllm_url

    conn = Connection()
    eval_run_id = f"eval-{uuid.uuid4().hex[:12]}"

    print(f"[EVAL] Loading golden dataset...")
    golden = load_golden(conn)
    if not golden:
        print("[ERROR] Golden dataset is empty. Run golden_dataset_seed.py + golden_dataset_import.py first.")
        conn.close()
        return

    print(f"[EVAL] {len(golden)} golden samples loaded")
    print(f"[EVAL] Model: {args.model_key}, Run: {eval_run_id}")

    model = LangChainModel()
    inf_logger = InferenceLogger(
        model_key=args.model_key,
        model_version=args.model_version,
        temperature=LLM_CONFIG.temperature,
        is_shadow=True,
        eval_run_id=eval_run_id,
    )

    print(f"[EVAL] Running predictions...")
    results = await evaluate(model, golden, inf_logger)

    # Inference logs 저장
    await inf_logger.flush(conn)

    # 메트릭 계산
    metrics = compute_all_metrics(results)

    # DB에 결과 저장
    save_eval_result(conn, eval_run_id, args.model_key, args.model_version, metrics, results)

    # 터미널 출력
    print_report(metrics, args.model_key, eval_run_id)

    conn.close()
    return eval_run_id


def main():
    parser = argparse.ArgumentParser(description="Run offline LLM evaluation on golden dataset")
    parser.add_argument("--model-key", required=True, help='Model identifier, e.g. "qwen3.5:9b"')
    parser.add_argument("--model-version", default=None, help='Full model path, e.g. "QuantTrio/Qwen3.5-9B-AWQ"')
    parser.add_argument("--vllm-url", default=None, help="vLLM base URL override")
    args = parser.parse_args()

    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
