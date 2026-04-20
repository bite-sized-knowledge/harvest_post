"""
One-time backfill: enrich existing Qdrant points with additional payload fields
(published_at, quality_score, blog_id, content_length, lang).

Usage:
    python scripts/backfill_qdrant_payload.py

Environment variables:
    DB_HOST, DB_USER, DB_PASSWORD, DB_NAME, DB_PORT (or RDS_ variants)
    QDRANT_ENDPOINT (or QDRANT_HOST + QDRANT_PORT)
    QDRANT_API_KEY (optional)
"""

import os
import ssl
import uuid
import pymysql
from qdrant_client import QdrantClient

COLLECTION = "bite-vectordb"
BATCH_SIZE = 100


def get_qdrant() -> QdrantClient:
    url = os.getenv("QDRANT_ENDPOINT")
    if not url:
        host = os.getenv("QDRANT_HOST", "localhost")
        port = os.getenv("QDRANT_PORT", "6333")
        url = f"{host}:{port}"
    return QdrantClient(url=url, api_key=os.getenv("QDRANT_API_KEY"))


def get_mysql():
    ssl_ctx = ssl.create_default_context()
    ssl_ctx.check_hostname = False
    ssl_ctx.verify_mode = ssl.CERT_NONE

    return pymysql.connect(
        host=os.getenv("DB_HOST") or os.getenv("RDS_HOST"),
        port=int(os.getenv("DB_PORT") or os.getenv("RDS_PORT", "3306")),
        user=os.getenv("DB_USER") or os.getenv("RDS_USER"),
        password=os.getenv("DB_PASSWORD") or os.getenv("RDS_PASSWORD"),
        database=os.getenv("DB_NAME") or os.getenv("RDS_DATABASE"),
        ssl=ssl_ctx,
        cursorclass=pymysql.cursors.DictCursor,
    )


def main():
    qdrant = get_qdrant()
    conn = get_mysql()
    cur = conn.cursor()

    cur.execute("""
        SELECT article_id, blog_id, content_length, lang,
               UNIX_TIMESTAMP(published_at) AS pub_epoch
        FROM article
        WHERE article_id IS NOT NULL
    """)
    articles = {row["article_id"]: row for row in cur.fetchall()}
    print(f"Loaded {len(articles)} articles from MySQL")

    updated = 0
    next_offset = None

    while True:
        points, next_offset = qdrant.scroll(
            collection_name=COLLECTION,
            limit=BATCH_SIZE,
            offset=next_offset,
            with_payload=True,
            with_vectors=False,
        )

        if not points:
            break

        # Group points by payload for batch set_payload
        payload_to_ids = {}
        for p in points:
            aid = p.payload.get("article_id") if p.payload else None
            if not aid or aid not in articles:
                continue

            row = articles[aid]
            new_payload = (
                float(row["pub_epoch"]) if row["pub_epoch"] else 0.0,
                row["blog_id"],
                row["content_length"],
                row["lang"],
            )

            needs_update = (
                p.payload.get("published_at") != new_payload[0]
                or p.payload.get("blog_id") != new_payload[1]
                or p.payload.get("content_length") != new_payload[2]
                or p.payload.get("lang") != new_payload[3]
            )
            if needs_update:
                key = new_payload
                if key not in payload_to_ids:
                    payload_to_ids[key] = []
                payload_to_ids[key].append(p.id)

        for (pub, bid, clen, lang), pids in payload_to_ids.items():
            qdrant.set_payload(
                collection_name=COLLECTION,
                payload={"published_at": pub, "blog_id": bid, "content_length": clen, "lang": lang},
                points=pids,
            )
            updated += len(pids)

        print(f"  Processed batch, updated {updated} so far...")

        if next_offset is None:
            break

    cur.close()
    conn.close()
    print(f"Done. Updated {updated} points in Qdrant.")


if __name__ == "__main__":
    main()
