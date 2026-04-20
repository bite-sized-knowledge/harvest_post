"""Export pending review_queue rows as JSONL for human inspection.

Mirrors the golden_dataset_seed.py file-based review pattern: a human opens
the JSONL, fills in `human_decision` (one of 'fp' | 'reject' | 'skip'),
optionally edits `human_notes`, then runs import_review_decisions.py.

Prioritizes rows where the judge disagrees (disagreement >= 1) but didn't
clear the auto-recover gate — that's where human judgment adds the most
signal.

Usage:
    cd src && python ../scripts/review/export_review_batch.py \\
        --output ../data/review_batch_$(date +%Y%m%d).jsonl --limit 50
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from db_conn import Connection


SELECT_SQL = """
    SELECT
        rq.id AS review_id,
        rq.article_id,
        rq.original_quality,
        rq.original_reason,
        rq.judge_quality,
        rq.judge_category,
        rq.judge_confidence,
        rq.judge_reasoning,
        rq.judge_evidence,
        rq.disagreement,
        rq.audited_at,
        ar.url, ar.title, ar.description, ar.content
    FROM article_review_queue rq
    JOIN article_rejected ar ON ar.article_id = rq.article_id
    WHERE rq.review_status = 'pending'
      AND rq.disagreement >= 1
    ORDER BY rq.disagreement DESC, rq.judge_confidence DESC, rq.audited_at ASC
    LIMIT {limit}
"""


def body_preview(content: str | None, max_chars: int = 2000) -> str:
    if not content:
        return ""
    return content[:max_chars]


def main():
    parser = argparse.ArgumentParser(description="Export pending review queue to JSONL")
    parser.add_argument("--output", "-o", required=True, help="Output JSONL path")
    parser.add_argument("--limit", type=int, default=50, help="Max rows to export")
    args = parser.parse_args()

    conn = Connection()
    df = conn.execute(SELECT_SQL.format(limit=int(args.limit)))
    rows = df.to_dict(orient="records") if df is not None else []
    conn.close()

    if not rows:
        print("[EXPORT] No pending rows with disagreement >= 1 found.")
        return

    os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        for r in rows:
            # Parse stored JSON string back to list for readability.
            try:
                evidence = json.loads(r.get("judge_evidence") or "[]")
            except (TypeError, json.JSONDecodeError):
                evidence = []

            entry = {
                "review_id": int(r["review_id"]),
                "article_id": r["article_id"],
                "url": r.get("url"),
                "title": r.get("title"),
                "original_quality": r.get("original_quality"),
                "original_reason": r.get("original_reason"),
                "judge_quality": r.get("judge_quality"),
                "judge_category": r.get("judge_category"),
                "judge_confidence": r.get("judge_confidence"),
                "judge_reasoning": r.get("judge_reasoning"),
                "judge_evidence": evidence,
                "disagreement": r.get("disagreement"),
                "description": r.get("description"),
                "body_preview": body_preview(r.get("content")),
                # Fields for the human to fill:
                "human_decision": "",   # one of "fp" | "reject" | "skip"
                "human_quality": None,  # optional — if set, replaces judge_quality on import
                "human_category": None, # optional — if set, replaces judge_category on import
                "human_notes": "",
            }
            f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")

    print(f"[EXPORT] {len(rows)} rows → {args.output}")
    print("다음 단계: JSONL 열어서 human_decision(fp|reject|skip) 채운 뒤")
    print(f"  python scripts/review/import_review_decisions.py --input {args.output}")


if __name__ == "__main__":
    main()
