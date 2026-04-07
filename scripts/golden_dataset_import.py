"""리뷰 완료된 JSONL을 llm_eval_golden 테이블에 적재.

Usage:
    cd src && python ../scripts/golden_dataset_import.py --input ../data/golden_seed.jsonl
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from db_conn import Connection
from sqlalchemy import text

UPSERT_SQL = text("""
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


def main():
    parser = argparse.ArgumentParser(description="Import golden dataset from reviewed JSONL")
    parser.add_argument("--input", "-i", required=True, help="Reviewed JSONL file path")
    args = parser.parse_args()

    conn = Connection()

    rows = []
    skipped = 0
    with open(args.input, "r", encoding="utf-8") as f:
        for line_num, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            entry = json.loads(line)

            # 필수 필드 검증
            if not entry.get("human_category") or not entry.get("human_quality"):
                print(f"[SKIP] Line {line_num}: missing human_category or human_quality for {entry.get('article_id')}")
                skipped += 1
                continue

            rows.append({
                "article_id": entry["article_id"],
                "human_category": int(entry["human_category"]),
                "human_keywords": entry.get("human_keywords") or "N/A\tN/A\tN/A",
                "human_quality": int(entry["human_quality"]),
                "frozen_input": entry["frozen_input"],
                "notes": entry.get("notes") or None,
            })

    if not rows:
        print("[ERROR] No valid rows to import.")
        conn.close()
        return

    print(f"[IMPORT] Importing {len(rows)} golden entries (skipped {skipped})...")
    conn.session_execute(UPSERT_SQL, rows)
    print(f"[DONE] {len(rows)} entries imported into llm_eval_golden")

    conn.close()


if __name__ == "__main__":
    main()
