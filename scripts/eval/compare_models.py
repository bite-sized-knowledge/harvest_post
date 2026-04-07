"""두 eval run을 비교하는 스크립트.

Usage:
    cd src && python ../scripts/eval/compare_models.py \
        --baseline eval-abc123 \
        --candidate eval-def456

    또는 최근 2개 eval run을 자동 비교:
    cd src && python ../scripts/eval/compare_models.py --latest
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from db_conn import Connection


METRICS = [
    ("category_accuracy",   "Category Accuracy",    "higher"),
    ("category_macro_f1",   "Category Macro F1",    "higher"),
    ("quality_mae",         "Quality MAE",          "lower"),
    ("quality_spearman",    "Quality Spearman",     "higher"),
    ("quality_within_1",    "Quality Within +-1",   "higher"),
    ("keyword_avg_relevance", "Keyword Relevance",  "higher"),
    ("parse_success_rate",  "Parse Success Rate",   "higher"),
    ("avg_latency_ms",      "Avg Latency (ms)",     "lower"),
]


def load_eval_result(conn: Connection, eval_run_id: str) -> dict | None:
    df = conn.execute(
        f"SELECT * FROM llm_eval_result WHERE eval_run_id = '{eval_run_id}'"
    )
    if df is None or len(df) == 0:
        return None
    return df.to_dict(orient="records")[0]


def load_latest_two(conn: Connection) -> tuple[dict, dict] | None:
    df = conn.execute(
        "SELECT * FROM llm_eval_result ORDER BY created_at DESC LIMIT 2"
    )
    if df is None or len(df) < 2:
        return None
    rows = df.to_dict(orient="records")
    return rows[1], rows[0]  # older=baseline, newer=candidate


def format_value(val, col: str) -> str:
    if val is None:
        return "N/A"
    if col == "avg_latency_ms":
        return str(int(val))
    if isinstance(val, float):
        return f"{val:.4f}"
    return str(val)


def format_delta(baseline_val, candidate_val, direction: str) -> str:
    if baseline_val is None or candidate_val is None:
        return ""
    delta = candidate_val - baseline_val
    if direction == "lower":
        indicator = "+" if delta < 0 else ("-" if delta > 0 else " ")
    else:
        indicator = "+" if delta > 0 else ("-" if delta < 0 else " ")

    # 실제 부호 표시
    sign = "+" if delta > 0 else ""
    if isinstance(delta, float):
        return f"{sign}{delta:.4f} {indicator}"
    return f"{sign}{int(delta)} {indicator}"


def print_comparison(baseline: dict, candidate: dict):
    b_model = f"{baseline['model_key']}"
    c_model = f"{candidate['model_key']}"

    print(f"\n{'='*70}")
    print(f"  Model Comparison")
    print(f"  Baseline  : {b_model} ({baseline['eval_run_id']})")
    print(f"  Candidate : {c_model} ({candidate['eval_run_id']})")
    print(f"  Golden set: {baseline['golden_count']} / {candidate['golden_count']} samples")
    print(f"{'='*70}")
    print()
    print(f"  {'Metric':<24} | {'Baseline':>10} | {'Candidate':>10} | {'Delta':>16}")
    print(f"  {'-'*24}-+-{'-'*10}-+-{'-'*10}-+-{'-'*16}")

    for col, label, direction in METRICS:
        b_val = baseline.get(col)
        c_val = candidate.get(col)
        b_str = format_value(b_val, col)
        c_str = format_value(c_val, col)
        d_str = format_delta(b_val, c_val, direction)
        print(f"  {label:<24} | {b_str:>10} | {c_str:>10} | {d_str:>16}")

    print(f"\n  (+) = improvement, (-) = regression")
    print(f"{'='*70}")


def main():
    parser = argparse.ArgumentParser(description="Compare two eval runs")
    parser.add_argument("--baseline", help="Baseline eval_run_id")
    parser.add_argument("--candidate", help="Candidate eval_run_id")
    parser.add_argument("--latest", action="store_true", help="Compare latest 2 eval runs")
    args = parser.parse_args()

    conn = Connection()

    if args.latest:
        pair = load_latest_two(conn)
        if not pair:
            print("[ERROR] Need at least 2 eval results. Run run_eval.py at least twice.")
            conn.close()
            return
        baseline, candidate = pair
    elif args.baseline and args.candidate:
        baseline = load_eval_result(conn, args.baseline)
        candidate = load_eval_result(conn, args.candidate)
        if not baseline:
            print(f"[ERROR] Baseline run not found: {args.baseline}")
            conn.close()
            return
        if not candidate:
            print(f"[ERROR] Candidate run not found: {args.candidate}")
            conn.close()
            return
    else:
        print("[ERROR] Provide --baseline + --candidate, or --latest")
        conn.close()
        return

    print_comparison(baseline, candidate)
    conn.close()


if __name__ == "__main__":
    main()
