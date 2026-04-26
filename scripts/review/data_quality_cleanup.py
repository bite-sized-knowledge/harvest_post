"""Apply cleanups for the issues surfaced by data_quality_audit.py.

Three actions, each gated independently:

  --dedup-content     A3: collapse MD5-content duplicates, keep oldest article_id
                          per group. Also covers A2 (title dups are a subset).
  --resolve-f2        F2: delete from `article` rows that also exist in
                          `article_rejected`. The rejected verdict wins.
  --reject-bad-titles B1+B2+B3: move articles whose title equals their blog
                          title (or is one of the source-name tokens, or is
                          ≤ 5 chars) into `article_rejected` with
                          reject_reason='title_extraction_failed', then delete
                          from `article`. Their URLs are written to
                          docs/refetch_urls_<date>.txt so a follow-up harvester
                          run can re-discover them.

Defaults to --dry-run. Pass --execute to actually mutate the DB.

Usage:
    cd harvest_post
    # Preview every action
    python scripts/review/data_quality_cleanup.py \\
        --dedup-content --resolve-f2 --reject-bad-titles

    # Apply for real
    python scripts/review/data_quality_cleanup.py \\
        --dedup-content --resolve-f2 --reject-bad-titles --execute
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from db_conn import Connection  # noqa: E402

# Mirror of the audit script's tokens — keep in sync.
SOURCE_TOKENS = [
    "clova", "linkedin", "medium", "github", "naver", "kakao",
    "brunch", "tistory", "velog", "qiita", "dev.to", "substack",
    "youtube", "twitter", "x", "facebook", "instagram", "rss",
    "feed", "blog", "home", "main",
]

REFETCH_URLS_PATH = "docs/refetch_urls_{date}.txt"


def _q(conn, sql):
    return conn.execute(sql.replace("%", "%%"))


def _exec(conn, sql, params=None):
    """Run a write query via SessionLocal (transactional)."""
    conn.session_execute(sql.replace("%", "%%"), params)


# --------------------------------------------------------------------------
# A3 (covers A2): MD5-content dedup
# --------------------------------------------------------------------------

def dedup_content(conn: Connection, execute: bool) -> None:
    print("\n=== Action: dedup-content (A3, covers A2) ===")
    # For each MD5 group with >1 row, find article_ids to delete = all but the
    # one with the smallest created_at (ties broken by article_id ASC).
    victims_df = _q(conn, """
        WITH dups AS (
            SELECT article_id, MD5(content) AS md5, created_at,
                   ROW_NUMBER() OVER (
                       PARTITION BY MD5(content)
                       ORDER BY created_at ASC, article_id ASC
                   ) AS rn,
                   COUNT(*) OVER (PARTITION BY MD5(content)) AS group_size
            FROM article
            WHERE content IS NOT NULL AND content_length >= 500
        )
        SELECT article_id, md5, created_at FROM dups WHERE group_size > 1 AND rn > 1
    """)
    n = len(victims_df)
    print(f"  Found {n} duplicate rows to delete (kept oldest per content_md5).")
    if n == 0:
        return
    print(f"  Sample (first 5):")
    for row in victims_df.head(5).to_dict(orient="records"):
        print(f"    DELETE article_id={row['article_id']} created_at={row['created_at']} md5={row['md5']}")

    if not execute:
        print("  [dry-run] no rows deleted. Pass --execute to apply.")
        return

    ids = victims_df["article_id"].tolist()
    # Delete in chunks of 500 to keep the query manageable.
    deleted = 0
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        in_clause = ",".join("'" + x.replace("'", "''") + "'" for x in chunk)
        _exec(conn, f"DELETE FROM article WHERE article_id IN ({in_clause})")
        deleted += len(chunk)
    print(f"  [executed] deleted {deleted} rows from article.")


# --------------------------------------------------------------------------
# F2: article ↔ article_rejected double-state — rejected wins
# --------------------------------------------------------------------------

def resolve_f2(conn: Connection, execute: bool) -> None:
    print("\n=== Action: resolve-f2 (article also in article_rejected) ===")
    df = _q(conn, """
        SELECT a.article_id, a.title, r.reject_reason, r.rejected_at
        FROM article a JOIN article_rejected r ON a.article_id = r.article_id
        ORDER BY r.rejected_at DESC
    """)
    n = len(df)
    print(f"  Found {n} articles in both tables.")
    if n == 0:
        return
    print(f"  Sample (first 5):")
    for row in df.head(5).to_dict(orient="records"):
        print(f"    DELETE FROM article WHERE article_id={row['article_id']}  "
              f"(reject_reason={row['reject_reason']})")

    if not execute:
        print("  [dry-run] no rows deleted. Pass --execute to apply.")
        return

    ids = df["article_id"].tolist()
    in_clause = ",".join("'" + x.replace("'", "''") + "'" for x in ids)
    _exec(conn, f"DELETE FROM article WHERE article_id IN ({in_clause})")
    print(f"  [executed] deleted {n} rows from article.")


# --------------------------------------------------------------------------
# B1 + B2 + B3: title extraction failure
# --------------------------------------------------------------------------

def reject_bad_titles(conn: Connection, execute: bool) -> None:
    print("\n=== Action: reject-bad-titles (B1 + B2 + B3) ===")
    tokens_sql = ",".join(f"'{t}'" for t in SOURCE_TOKENS)
    df = _q(conn, f"""
        SELECT a.article_id, a.blog_id, a.url, a.title,
               b.title AS blog_title,
               CASE
                   WHEN LOWER(TRIM(a.title)) IN ({tokens_sql}) THEN 'B2_source_token'
                   WHEN LOWER(TRIM(a.title)) = LOWER(TRIM(b.title)) THEN 'B1_eq_blog'
                   WHEN CHAR_LENGTH(TRIM(a.title)) <= 5 THEN 'B3_too_short'
                   ELSE 'unknown'
               END AS rule
        FROM article a JOIN blog b ON a.blog_id = b.blog_id
        WHERE a.title IS NOT NULL
          AND (
            LOWER(TRIM(a.title)) IN ({tokens_sql})
            OR LOWER(TRIM(a.title)) = LOWER(TRIM(b.title))
            OR CHAR_LENGTH(TRIM(a.title)) <= 5
          )
        ORDER BY a.created_at DESC
    """)
    n = len(df)
    print(f"  Found {n} articles with broken titles.")
    if n == 0:
        return
    by_rule = df.groupby("rule").size().to_dict()
    print(f"  By rule: {by_rule}")
    print(f"  Sample (first 5):")
    for row in df.head(5).to_dict(orient="records"):
        print(f"    {row['rule']:20s} blog={row['blog_id']:5d} title={row['title']!r:30s} "
              f"url={row['url'][:60]}...")

    refetch_path = REFETCH_URLS_PATH.format(date=datetime.now().strftime("%Y%m%d"))
    if not execute:
        print(f"  [dry-run] no rows moved. Pass --execute to apply.")
        print(f"  [dry-run] would write {n} URLs to {refetch_path} for follow-up refetch.")
        return

    # Insert into article_rejected. Schema mirrors article_queue (subset).
    # reject_reason='title_extraction_failed' is a new sentinel — see
    # harvest_post/src/main.py reject_reason vocabulary.
    ids = df["article_id"].tolist()
    in_clause = ",".join("'" + x.replace("'", "''") + "'" for x in ids)

    _exec(conn, f"""
        INSERT INTO article_rejected (
            article_id, blog_id, url, title, thumbnail, description, content,
            content_length, lang, published_at, quality_score, reject_reason, rejected_at
        )
        SELECT
            article_id, blog_id, url, title, thumbnail, description, content,
            content_length, lang, published_at, NULL,
            'title_extraction_failed', NOW()
        FROM article WHERE article_id IN ({in_clause})
        ON DUPLICATE KEY UPDATE
            reject_reason = 'title_extraction_failed',
            rejected_at = NOW()
    """)
    _exec(conn, f"DELETE FROM article WHERE article_id IN ({in_clause})")

    # Write URLs to a refetch file for an operator to feed back through the
    # harvester later (since RSS feeds may no longer have these URLs).
    os.makedirs(os.path.dirname(refetch_path), exist_ok=True)
    with open(refetch_path, "w", encoding="utf-8") as f:
        for url in df["url"].tolist():
            f.write(url + "\n")
    print(f"  [executed] moved {n} rows article → article_rejected.")
    print(f"  [executed] wrote {n} URLs to {refetch_path} for follow-up refetch.")


# --------------------------------------------------------------------------
# Entry
# --------------------------------------------------------------------------

def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dedup-content", action="store_true",
                   help="A3 (covers A2): collapse MD5-content duplicates, keep oldest")
    p.add_argument("--resolve-f2", action="store_true",
                   help="F2: delete from article where also in article_rejected")
    p.add_argument("--reject-bad-titles", action="store_true",
                   help="B1+B2+B3: move title-broken articles to article_rejected")
    p.add_argument("--execute", action="store_true",
                   help="Actually run the mutations. Default is dry-run.")
    args = p.parse_args()

    if not (args.dedup_content or args.resolve_f2 or args.reject_bad_titles):
        p.error("pick at least one action: --dedup-content / --resolve-f2 / --reject-bad-titles")

    mode = "EXECUTE" if args.execute else "DRY-RUN"
    print(f"[{mode}] data_quality_cleanup.py @ {datetime.now().isoformat(timespec='seconds')}")

    conn = Connection()
    try:
        if args.dedup_content:
            dedup_content(conn, args.execute)
        if args.resolve_f2:
            resolve_f2(conn, args.execute)
        if args.reject_bad_titles:
            reject_bad_titles(conn, args.execute)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
