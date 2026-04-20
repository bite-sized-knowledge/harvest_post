"""Aggregate stats on the rejection + review pipeline.

Used for:
  - Daily markdown report (--format markdown, write to file / slack webhook)
  - Dashboard JSON endpoint (bite-monitor ingests via --format json)

Metrics (per configurable window):
  - total_rejected, by_reason
  - audited_count, disagreement_rate, estimated_fp_rate
  - auto_recovered, pending_human_review, golden_added_from_review
  - judge throughput (from llm_inference_log is_shadow rows)

Usage:
    python scripts/review/rejection_stats.py --window-days 7 --format markdown
    python scripts/review/rejection_stats.py --window-days 30 --format json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from db_conn import Connection


def query_stats(conn: Connection, window_days: int) -> dict:
    window = int(window_days)

    total_rejected = int(conn.execute(f"""
        SELECT COUNT(*) AS n FROM article_rejected
        WHERE rejected_at > DATE_SUB(NOW(), INTERVAL {window} DAY)
    """)["n"].iloc[0])

    by_reason_df = conn.execute(f"""
        SELECT reject_reason, COUNT(*) AS n FROM article_rejected
        WHERE rejected_at > DATE_SUB(NOW(), INTERVAL {window} DAY)
        GROUP BY reject_reason
        ORDER BY n DESC
    """)
    by_reason = {r["reject_reason"]: int(r["n"]) for r in by_reason_df.to_dict(orient="records")} \
        if by_reason_df is not None else {}

    audited_df = conn.execute(f"""
        SELECT
            COUNT(*) AS total_audited,
            SUM(CASE WHEN disagreement >= 1 THEN 1 ELSE 0 END) AS disagreed,
            SUM(CASE WHEN review_status = 'auto_recovered' THEN 1 ELSE 0 END) AS auto_recovered,
            SUM(CASE WHEN review_status = 'pending' THEN 1 ELSE 0 END) AS pending,
            SUM(CASE WHEN review_status = 'human_confirmed_fp' THEN 1 ELSE 0 END) AS confirmed_fp,
            SUM(CASE WHEN review_status = 'human_confirmed_reject' THEN 1 ELSE 0 END) AS confirmed_reject,
            SUM(CASE WHEN review_status = 'skipped' THEN 1 ELSE 0 END) AS skipped
        FROM article_review_queue
        WHERE audited_at > DATE_SUB(NOW(), INTERVAL {window} DAY)
    """)
    a = audited_df.iloc[0].to_dict() if audited_df is not None and len(audited_df) > 0 else {}
    total_audited = int(a.get("total_audited") or 0)
    disagreed = int(a.get("disagreed") or 0)

    # "estimated FP rate" = auto_recovered + confirmed_fp over audited.
    # Conservative — doesn't count pending disagreements (those haven't been
    # confirmed yet, human may still say they were correctly rejected).
    est_fp_numerator = int(a.get("auto_recovered") or 0) + int(a.get("confirmed_fp") or 0)
    estimated_fp_rate = (est_fp_numerator / total_audited) if total_audited else 0.0
    disagreement_rate = (disagreed / total_audited) if total_audited else 0.0

    golden_added_df = conn.execute(f"""
        SELECT COUNT(*) AS n FROM llm_eval_golden
        WHERE notes LIKE 'from_review_queue_%%'
          AND updated_at > DATE_SUB(NOW(), INTERVAL {window} DAY)
    """)
    golden_added = int(golden_added_df["n"].iloc[0]) if golden_added_df is not None else 0

    # Judge throughput: how many shadow audit calls per minute on average?
    # (rough — uses total audit latency_ms / calls.)
    throughput_df = conn.execute(f"""
        SELECT
            COUNT(*) AS n,
            AVG(latency_ms) AS avg_latency_ms
        FROM llm_inference_log
        WHERE is_shadow = TRUE
          AND outcome LIKE 'audit_%%'
          AND created_at > DATE_SUB(NOW(), INTERVAL {window} DAY)
    """)
    avg_lat_ms = 0
    if throughput_df is not None and len(throughput_df) > 0:
        v = throughput_df.iloc[0].get("avg_latency_ms")
        if v is not None:
            avg_lat_ms = float(v)
    throughput_per_min = (60_000.0 / avg_lat_ms) if avg_lat_ms > 0 else 0.0

    # Most recent judge model used (should be stable but useful for sanity).
    last_model_df = conn.execute("""
        SELECT judge_model, MAX(audited_at) AS last_run
        FROM article_review_queue
        GROUP BY judge_model
        ORDER BY last_run DESC
        LIMIT 1
    """)
    last_judge_model = None
    last_run_at = None
    if last_model_df is not None and len(last_model_df) > 0:
        last_judge_model = last_model_df.iloc[0].get("judge_model")
        last_run_at = last_model_df.iloc[0].get("last_run")

    return {
        "window_days": window,
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "total_rejected": total_rejected,
        "by_reason": by_reason,
        "audited_count": total_audited,
        "disagreement_rate": round(disagreement_rate, 4),
        "estimated_fp_rate": round(estimated_fp_rate, 4),
        "auto_recovered": int(a.get("auto_recovered") or 0),
        "pending_human_review": int(a.get("pending") or 0),
        "confirmed_fp": int(a.get("confirmed_fp") or 0),
        "confirmed_reject": int(a.get("confirmed_reject") or 0),
        "skipped": int(a.get("skipped") or 0),
        "golden_added_from_review": golden_added,
        "judge_model": last_judge_model,
        "last_judge_run_at": str(last_run_at) if last_run_at is not None else None,
        "audit_avg_latency_ms": round(avg_lat_ms, 0),
        "audit_throughput_per_min": round(throughput_per_min, 1),
    }


def to_markdown(s: dict) -> str:
    lines = [
        f"# Rejection Review Report ({s['window_days']}-day window)",
        f"_generated_at: {s['generated_at']}_",
        "",
        "## Rejections",
        f"- **Total rejected**: {s['total_rejected']}",
        "- **By reason**:",
    ]
    for reason, n in s["by_reason"].items():
        lines.append(f"  - `{reason}`: {n}")
    lines += [
        "",
        "## Judge Audit",
        f"- **Judge model**: `{s['judge_model']}`",
        f"- **Last run**: {s['last_judge_run_at']}",
        f"- **Audited**: {s['audited_count']}",
        f"- **Disagreement rate**: {s['disagreement_rate']*100:.1f}%",
        f"- **Estimated FP rate**: {s['estimated_fp_rate']*100:.1f}%",
        f"- **Avg latency**: {s['audit_avg_latency_ms']} ms ({s['audit_throughput_per_min']}/min)",
        "",
        "## Review Queue",
        f"- **Auto-recovered**: {s['auto_recovered']}",
        f"- **Pending human review**: {s['pending_human_review']}",
        f"- **Human-confirmed FP**: {s['confirmed_fp']}",
        f"- **Human-confirmed reject**: {s['confirmed_reject']}",
        f"- **Skipped**: {s['skipped']}",
        "",
        "## Feedback Loop",
        f"- **Golden dataset rows added from review**: {s['golden_added_from_review']}",
    ]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Rejection + review pipeline stats")
    parser.add_argument("--window-days", type=int, default=7)
    parser.add_argument("--format", choices=("json", "markdown"), default="markdown")
    args = parser.parse_args()

    conn = Connection()
    try:
        stats = query_stats(conn, args.window_days)
    finally:
        conn.close()

    if args.format == "json":
        print(json.dumps(stats, ensure_ascii=False, indent=2, default=str))
    else:
        print(to_markdown(stats))


if __name__ == "__main__":
    main()
