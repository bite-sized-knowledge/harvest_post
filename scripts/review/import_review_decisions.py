"""Import a reviewed JSONL (from export_review_batch.py) and apply decisions.

Side effects:
  1. Updates article_review_queue.review_status per human_decision:
        'fp'     → 'human_confirmed_fp'      (will be picked up by recover)
        'reject' → 'human_confirmed_reject'
        'skip'   → 'skipped'
  2. For fp/reject decisions with non-empty human_category+human_quality,
     upserts into llm_eval_golden with notes='from_review_queue_YYYYMMDD'.
     This is the feedback loop — confirmed borderline decisions directly
     improve future eval runs.

Usage:
    python scripts/review/import_review_decisions.py --input data/review_batch_20260416.jsonl
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from db_conn import Connection
from preprocessing import BlogPostProcessor, parse_article_text_from_html
from sqlalchemy import text

PREPROCESSOR = BlogPostProcessor()


DECISION_TO_STATUS = {
    "fp": "human_confirmed_fp",
    "reject": "human_confirmed_reject",
    "skip": "skipped",
}


UPDATE_STATUS_SQL = text("""
    UPDATE article_review_queue
    SET review_status = :review_status,
        notes = CONCAT(IFNULL(notes, ''), :note)
    WHERE id = :review_id
""")


GOLDEN_UPSERT_SQL = text("""
    INSERT INTO llm_eval_golden
        (article_id, human_category, human_keywords, human_quality, frozen_input, notes)
    VALUES
        (:article_id, :human_category, :human_keywords, :human_quality, :frozen_input, :notes)
    ON DUPLICATE KEY UPDATE
        human_category = VALUES(human_category),
        human_keywords = VALUES(human_keywords),
        human_quality  = VALUES(human_quality),
        frozen_input   = VALUES(frozen_input),
        notes          = VALUES(notes)
""")


FETCH_CONTENT_SQL = """
    SELECT ar.article_id, ar.title, ar.description, ar.content
    FROM article_rejected ar
    WHERE ar.article_id IN ({placeholders})
"""


def build_frozen_input(title: str, description: str, content: str) -> str:
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


def main():
    parser = argparse.ArgumentParser(description="Apply human review decisions from JSONL")
    parser.add_argument("--input", "-i", required=True, help="Reviewed JSONL path")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    with open(args.input, "r", encoding="utf-8") as f:
        entries = [json.loads(line) for line in f if line.strip()]

    print(f"[IMPORT] Read {len(entries)} entries from {args.input}")

    status_updates: list[dict] = []
    golden_candidates: list[dict] = []
    skipped_empty = 0

    note_tag = f" [human_review {datetime.utcnow():%Y-%m-%d %H:%M}]"

    # Collect article_ids we'll need to hydrate for golden upsert
    fetch_ids: list[str] = []

    for e in entries:
        decision = (e.get("human_decision") or "").strip().lower()
        if not decision:
            skipped_empty += 1
            continue
        if decision not in DECISION_TO_STATUS:
            print(f"[WARN] review_id={e.get('review_id')}: unknown decision '{decision}' — skipped")
            continue

        status_updates.append({
            "review_id": int(e["review_id"]),
            "review_status": DECISION_TO_STATUS[decision],
            "note": note_tag,
        })

        # Only fp/reject with non-empty human labels feed the golden set.
        # 'skip' means "I have no opinion", so don't contaminate golden.
        if decision in ("fp", "reject"):
            hc = e.get("human_category") or e.get("judge_category")
            hq = e.get("human_quality") or e.get("judge_quality")
            if hc and hq:
                fetch_ids.append(e["article_id"])
                golden_candidates.append({
                    "article_id": e["article_id"],
                    "human_category": int(hc),
                    "human_quality": int(hq),
                    "human_keywords": e.get("human_keywords") or "N/A\tN/A\tN/A",
                    "decision": decision,
                })

    print(f"[IMPORT] {len(status_updates)} decisions, {len(golden_candidates)} golden candidates, "
          f"{skipped_empty} entries had empty human_decision (left pending)")

    if args.dry_run:
        for u in status_updates[:10]:
            print(f"  [DRY] review_id={u['review_id']} → {u['review_status']}")
        return

    conn = Connection()

    # --- 1. Apply status updates ---
    for u in status_updates:
        conn.session_execute(UPDATE_STATUS_SQL, u)

    # --- 2. Hydrate article content + build frozen_input for golden upsert ---
    if golden_candidates:
        placeholders = ",".join(f"'{aid}'" for aid in fetch_ids)
        fetch_sql = FETCH_CONTENT_SQL.format(placeholders=placeholders)
        content_df = conn.execute(fetch_sql)
        content_map = {}
        if content_df is not None:
            for r in content_df.to_dict(orient="records"):
                content_map[r["article_id"]] = r

        golden_rows = []
        date_tag = datetime.utcnow().strftime("%Y%m%d")
        for g in golden_candidates:
            src = content_map.get(g["article_id"])
            if not src:
                print(f"[WARN] {g['article_id']} content not found in article_rejected — skipping golden upsert")
                continue
            frozen_input = build_frozen_input(
                title=src.get("title") or "",
                description=src.get("description") or "",
                content=src.get("content") or "",
            )
            golden_rows.append({
                "article_id": g["article_id"],
                "human_category": g["human_category"],
                "human_keywords": g["human_keywords"],
                "human_quality": g["human_quality"],
                "frozen_input": frozen_input,
                "notes": f"from_review_queue_{date_tag}_{g['decision']}",
            })

        if golden_rows:
            conn.session_execute(GOLDEN_UPSERT_SQL, golden_rows)
            print(f"[IMPORT] Upserted {len(golden_rows)} rows into llm_eval_golden")

    conn.close()
    print("[DONE]")


if __name__ == "__main__":
    main()
