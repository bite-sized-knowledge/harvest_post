"""
Mirror production bite DB content tables to the dev environment.

Invoked from run.sh after a successful harvest cycle, and from the
daily 04:00 cron (`infra/scripts/sync-prod-to-dev.sh`). Safe to run
multiple times.

Scope: **item/content tables only** — article, article_queue,
article_rejected, blog, interest, llm_config_metadata. User activity
tables (member, member_interest, article_history, article_like,
article_bookmark, article_share, article_uninterest, blog_subscribe,
email_verify, oauth, user_article_engagement, user_events,
recommendation, member_last_seen_feed) are intentionally NOT synced —
dev keeps its own user state.

Deletion semantics:
  * bite_dev.article — rows that no longer exist in bite.article
    (typically moved to article_rejected by backfill) are DELETED. This
    is what keeps dev from accumulating stale bad articles.
  * bite_dev.article_queue / article_rejected — full snapshot replace.

Qdrant은 prod/dev가 단일 인스턴스/단일 컬렉션을 공유하므로 벡터 동기화는 불필요.
같은 article_id가 양쪽 환경에서 그대로 통한다.

Credentials are read from environment variables (DB_HOST, DB_USER,
DB_PASSWORD). NO hardcoded passwords.
"""

import os
import ssl
import sys

import pymysql


# Tables excluded because they are user activity / logs — dev keeps its own state.
# Kept here as documentation only; sync_mysql explicitly enumerates what it touches.
_USER_ACTIVITY_TABLES = frozenset({
    "article_bookmark", "article_history", "article_like", "article_share",
    "article_uninterest", "blog_subscribe", "email_verify", "member",
    "member_interest", "member_last_seen_feed", "oauth", "recommendation",
    "user_article_engagement", "user_events",
    "llm_eval_golden", "llm_eval_result", "llm_inference_log",
})


def _common_columns(cur, table: str) -> list:
    """Columns present in BOTH bite.<table> and bite_dev.<table>, excluding
    STORED/VIRTUAL GENERATED columns. Guards against schema drift between envs."""
    cur.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema=%s AND table_name=%s "
        "AND (extra IS NULL OR extra NOT LIKE '%%GENERATED%%')",
        ("bite", table),
    )
    prod = [r[0] for r in cur.fetchall()]
    cur.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema=%s AND table_name=%s "
        "AND (extra IS NULL OR extra NOT LIKE '%%GENERATED%%')",
        ("bite_dev", table),
    )
    dev = set(r[0] for r in cur.fetchall())
    # preserve prod column order (matters for SELECT)
    return [c for c in prod if c in dev]


def _upsert(cur, table: str, key_cols: list, skip_update: set = frozenset()) -> None:
    """INSERT ... SELECT ... ON DUPLICATE KEY UPDATE using the columns common
    to bite.<table> and bite_dev.<table>."""
    cols = _common_columns(cur, table)
    if not cols:
        raise RuntimeError(f"No common columns for table {table}")
    col_list = ", ".join(f"`{c}`" for c in cols)
    update_cols = [c for c in cols if c not in key_cols and c not in skip_update]
    update_list = ", ".join(f"`{c}`=VALUES(`{c}`)" for c in update_cols)
    sql = (
        f"INSERT INTO bite_dev.`{table}` ({col_list}) "
        f"SELECT {col_list} FROM bite.`{table}` "
        f"ON DUPLICATE KEY UPDATE {update_list}"
    )
    cur.execute(sql)


def sync_mysql(host: str, user: str, password: str) -> None:
    ssl_ctx = ssl.create_default_context()
    ssl_ctx.check_hostname = False
    ssl_ctx.verify_mode = ssl.CERT_NONE

    # MySQL requires a default schema for multi-table DELETE syntax, even when
    # every table is fully qualified. Bind to bite_dev (the write target).
    conn = pymysql.connect(
        host=host, port=3306, user=user, password=password,
        database="bite_dev", ssl=ssl_ctx,
    )
    try:
        cur = conn.cursor()

        # article — UPSERT then DELETE stale rows so dev mirrors prod.article exactly.
        _upsert(cur, "article", key_cols=["article_id"])
        cur.execute("""
            DELETE d FROM bite_dev.article d
            LEFT JOIN bite.article p ON d.article_id = p.article_id
            WHERE p.article_id IS NULL
        """)
        stale_removed = cur.rowcount

        # article_queue — snapshot replace
        cur.execute("DELETE FROM bite_dev.article_queue")
        cur.execute("INSERT INTO bite_dev.article_queue SELECT * FROM bite.article_queue")

        # article_rejected — snapshot replace so dev mirrors prod rejections
        cur.execute("DELETE FROM bite_dev.article_rejected")
        cur.execute("INSERT INTO bite_dev.article_rejected SELECT * FROM bite.article_rejected")

        # blog — full UPSERT (crawler config, favicons, new sources). No delete: FK from article.blog_id.
        _upsert(cur, "blog", key_cols=["blog_id"], skip_update={"created_at"})

        # interest — UPSERT (category reference data). No delete: FK from article.category_id, member_interest.
        _upsert(cur, "interest", key_cols=["interest_id"], skip_update={"created_at"})

        # llm_config_metadata — upsert
        _upsert(cur, "llm_config_metadata", key_cols=["llm_model"])

        conn.commit()
    finally:
        conn.close()
    print(f"[SYNC] MySQL bite → bite_dev done (stale article rows removed: {stale_removed})")


def main() -> int:
    host = os.getenv("DB_HOST") or "192.168.219.104"
    user = os.getenv("DB_USER")
    password = os.getenv("DB_PASSWORD")
    if not user or not password:
        print("[ERROR] DB_USER / DB_PASSWORD env vars are required", file=sys.stderr)
        return 1

    try:
        sync_mysql(host, user, password)
    except Exception as e:
        print(f"[ERROR] MySQL sync failed: {e}", file=sys.stderr)
        return 2

    return 0


if __name__ == "__main__":
    sys.exit(main())
