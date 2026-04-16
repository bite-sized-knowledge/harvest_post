"""
Mirror production bite DB + Qdrant collection to the dev environment.

Invoked from run.sh after a successful harvest cycle. Safe to run multiple
times — MySQL upserts use ON DUPLICATE KEY UPDATE; Qdrant upserts overwrite
by id.

Credentials / hosts are read from environment variables (NO hardcoded
passwords). The GPU harvest-post.service provides DB_HOST / DB_USER /
DB_PASSWORD via Environment= directives.
"""

import json
import os
import ssl
import sys
from urllib.request import Request, urlopen

import pymysql


def sync_mysql(host: str, user: str, password: str) -> None:
    ssl_ctx = ssl.create_default_context()
    ssl_ctx.check_hostname = False
    ssl_ctx.verify_mode = ssl.CERT_NONE

    conn = pymysql.connect(host=host, port=3306, user=user, password=password, ssl=ssl_ctx)
    try:
        cur = conn.cursor()

        # article table — upsert (exclude STORED GENERATED columns)
        cur.execute("""
            INSERT INTO bite_dev.article
                (article_id, blog_id, url, title, thumbnail, description, keywords,
                 category_id, content, content_length, lang, like_count, share_count,
                 bookmark_count, published_at, created_at, updated_at)
            SELECT article_id, blog_id, url, title, thumbnail, description, keywords,
                   category_id, content, content_length, lang, like_count, share_count,
                   bookmark_count, published_at, created_at, updated_at
            FROM bite.article
            ON DUPLICATE KEY UPDATE
                keywords=VALUES(keywords), category_id=VALUES(category_id),
                content=VALUES(content), content_length=VALUES(content_length),
                lang=VALUES(lang), updated_at=VALUES(updated_at),
                description=VALUES(description), thumbnail=VALUES(thumbnail)
        """)

        # article_queue — snapshot replace
        cur.execute("DELETE FROM bite_dev.article_queue")
        cur.execute("INSERT INTO bite_dev.article_queue SELECT * FROM bite.article_queue")

        # article_rejected — snapshot replace so dev mirrors prod rejections
        cur.execute("DELETE FROM bite_dev.article_rejected")
        cur.execute("INSERT INTO bite_dev.article_rejected SELECT * FROM bite.article_rejected")

        # blog favicon — join update
        cur.execute("""
            UPDATE bite_dev.blog b
            JOIN bite.blog s ON b.blog_id = s.blog_id
            SET b.favicon = s.favicon
        """)

        # llm_config_metadata — upsert
        cur.execute("""
            INSERT INTO bite_dev.llm_config_metadata (llm_model, embedding_model, embedding_size, chunk_size)
            SELECT llm_model, embedding_model, embedding_size, chunk_size FROM bite.llm_config_metadata
            ON DUPLICATE KEY UPDATE
                llm_model=VALUES(llm_model), embedding_model=VALUES(embedding_model),
                embedding_size=VALUES(embedding_size), chunk_size=VALUES(chunk_size)
        """)

        conn.commit()
    finally:
        conn.close()
    print("[SYNC] MySQL bite → bite_dev done")


def sync_qdrant(host: str, prod_port: int, dev_port: int, collection: str) -> None:
    prod = f"http://{host}:{prod_port}"
    dev = f"http://{host}:{dev_port}"
    api_key = os.getenv("QDRANT_API_KEY", "")
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["api-key"] = api_key

    def _req(url: str, method: str = "GET", data=None) -> dict:
        req = Request(url, method=method, headers=headers)
        if data is not None:
            req.data = json.dumps(data).encode()
        return json.loads(urlopen(req).read())

    # ensure dev collection exists — if not, clone config from prod
    try:
        config = _req(f"{prod}/collections/{collection}")["result"]["config"]["params"]
        try:
            _req(f"{dev}/collections/{collection}")
        except Exception:
            _req(f"{dev}/collections/{collection}", "PUT", {"vectors": config["vectors"]})
    except Exception as e:
        print(f"[WARN] Qdrant collection setup: {e}")
        return

    # scroll + batch upsert
    offset = None
    total = 0
    while True:
        body = {"limit": 100, "with_payload": True, "with_vector": True}
        if offset:
            body["offset"] = offset

        resp = _req(f"{prod}/collections/{collection}/points/scroll", "POST", body)
        points = resp["result"]["points"]
        if not points:
            break

        upsert = [
            {"id": p["id"], "vector": p["vector"], "payload": p.get("payload", {})}
            for p in points
        ]
        _req(f"{dev}/collections/{collection}/points", "PUT", {"points": upsert})

        total += len(points)
        offset = resp["result"].get("next_page_offset")
        if offset is None:
            break

    print(f"[SYNC] Qdrant prod → dev done ({total} points)")


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
