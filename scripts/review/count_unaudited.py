"""Print the count of unaudited rejected rows to stdout.

Used by run.sh to decide whether to spin up the judge container this cycle.
Keep output short (just the integer) so the shell can capture it cleanly.

Exit non-zero on DB error so `|| echo "0"` in run.sh can treat failures as
"don't run judge phase" rather than mis-interpreting a traceback.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from db_conn import Connection


QUERY = """
    SELECT COUNT(*) AS n
    FROM article_rejected ar
    WHERE ar.reject_reason != 'empty_title'
      AND NOT EXISTS (
          SELECT 1 FROM article_review_queue rq
          WHERE rq.article_id = ar.article_id
      )
"""


def main() -> int:
    try:
        conn = Connection()
        df = conn.execute(QUERY)
        n = int(df["n"].iloc[0]) if df is not None and len(df) > 0 else 0
        conn.close()
    except Exception as e:
        print(f"[count_unaudited ERROR] {e}", file=sys.stderr)
        return 2
    print(n)
    return 0


if __name__ == "__main__":
    sys.exit(main())
