"""
Mirror production bite DB + Qdrant collection to the dev environment.

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
  * Qdrant dev collection — points whose ids are absent from prod are
    DELETED so the vector store matches the MySQL `article` table.

Credentials are read from environment variables (DB_HOST, DB_USER,
DB_PASSWORD, QDRANT_API_KEY). NO hardcoded passwords.
"""

import json
import os
import ssl
import sys
from urllib.request import Request, urlopen

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


def _qdrant_req(url: str, headers: dict, method: str = "GET", data=None) -> dict:
    req = Request(url, method=method, headers=headers)
    if data is not None:
        req.data = json.dumps(data).encode()
    return json.loads(urlopen(req).read())


def _scroll_ids(base: str, headers: dict, collection: str, with_vector: bool) -> list:
    """Scroll every point in a collection; return list of (id, payload, vector_or_None)."""
    points = []
    offset = None
    while True:
        body = {"limit": 256, "with_payload": True, "with_vector": with_vector}
        if offset is not None:
            body["offset"] = offset
        resp = _qdrant_req(f"{base}/collections/{collection}/points/scroll", headers, "POST", body)
        batch = resp["result"]["points"]
        if not batch:
            break
        points.extend(batch)
        offset = resp["result"].get("next_page_offset")
        if offset is None:
            break
    return points


def sync_qdrant(host: str, prod_port: int, dev_port: int, collection: str) -> None:
    prod = f"http://{host}:{prod_port}"
    dev = f"http://{host}:{dev_port}"
    api_key = os.getenv("QDRANT_API_KEY", "")
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["api-key"] = api_key

    # ensure dev collection exists — if not, clone vectors config from prod
    try:
        config = _qdrant_req(f"{prod}/collections/{collection}", headers)["result"]["config"]["params"]
        try:
            _qdrant_req(f"{dev}/collections/{collection}", headers)
        except Exception:
            _qdrant_req(f"{dev}/collections/{collection}", headers, "PUT", {"vectors": config["vectors"]})
    except Exception as e:
        print(f"[WARN] Qdrant collection setup: {e}")
        return

    # Upsert prod → dev in batches
    offset = None
    total_upserted = 0
    prod_ids = set()
    while True:
        body = {"limit": 100, "with_payload": True, "with_vector": True}
        if offset is not None:
            body["offset"] = offset
        resp = _qdrant_req(f"{prod}/collections/{collection}/points/scroll", headers, "POST", body)
        batch = resp["result"]["points"]
        if not batch:
            break

        upsert = [{"id": p["id"], "vector": p["vector"], "payload": p.get("payload", {})} for p in batch]
        _qdrant_req(f"{dev}/collections/{collection}/points", headers, "PUT", {"points": upsert})
        prod_ids.update(p["id"] for p in batch)
        total_upserted += len(batch)

        offset = resp["result"].get("next_page_offset")
        if offset is None:
            break

    # Diff-delete: dev points whose id is absent from prod
    dev_points = _scroll_ids(dev, headers, collection, with_vector=False)
    dev_ids = {p["id"] for p in dev_points}
    to_delete = list(dev_ids - prod_ids)
    if to_delete:
        # Qdrant accepts up to ~100k ids per request, chunk conservatively.
        CHUNK = 1000
        for i in range(0, len(to_delete), CHUNK):
            _qdrant_req(
                f"{dev}/collections/{collection}/points/delete",
                headers,
                "POST",
                {"points": to_delete[i : i + CHUNK]},
            )
    print(
        f"[SYNC] Qdrant prod → dev done "
        f"(upserted {total_upserted}, deleted {len(to_delete)} stale dev-only points)"
    )


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

    qdrant_prod_port = int(os.getenv("QDRANT_PROD_PORT", "6333"))
    qdrant_dev_port = int(os.getenv("QDRANT_DEV_PORT", "6335"))
    try:
        sync_qdrant(host, qdrant_prod_port, qdrant_dev_port, "bite-vectordb")
    except Exception as e:
        print(f"[ERROR] Qdrant sync failed: {e}", file=sys.stderr)
        return 3

    return 0


if __name__ == "__main__":
    sys.exit(main())
