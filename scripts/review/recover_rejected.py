"""Move articles confirmed as false-positive rejections back into `article`.

Reverse of scripts/backfill_category.py: takes rows from article_rejected that
the judge (or a human) flagged as good, rehydrates them into article with the
judge's labels, regenerates embeddings, and upserts into Qdrant.

CRITICAL: do NOT skip the Qdrant upsert. Without a vector, the recovered
article exists in MySQL but never appears in recommendations — a silent bug
worse than leaving it rejected.

Usage:
    cd src && python ../scripts/review/recover_rejected.py --dry-run
    cd src && python ../scripts/review/recover_rejected.py          # real run
    cd src && python ../scripts/review/recover_rejected.py --limit 50
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
from dataclasses import dataclass
from typing import Optional

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from db_conn import Connection
from embedder import TextEmbeddings
from qdrant_config import QdrantVectorStore
from config import (
    ARTICLE_TABLE,
    COLUMN_NAMES,
    INSERT_QUERY,
    PROMPT_VERSION,
    EMBEDDING_SIZE,
)
from sqlalchemy import bindparam, text


# Eligible review_status values for recovery
RECOVERABLE_STATUSES = ("auto_recovered", "human_confirmed_fp")


@dataclass
class RecoveryCounts:
    eligible: int = 0
    inserted: int = 0
    embedded: int = 0
    qdrant_upserted: int = 0
    failed: int = 0

    def as_dict(self) -> dict:
        return {
            "eligible": self.eligible,
            "inserted": self.inserted,
            "embedded": self.embedded,
            "qdrant_upserted": self.qdrant_upserted,
            "failed": self.failed,
        }


def build_article_row(r: dict, created_at, updated_at) -> dict:
    """Build an article-table row dict from the joined review_queue+rejected row.

    Keys match COLUMN_NAMES in config.py so INSERT_QUERY's bindparams line up.
    Missing judge fields (summary/difficulty/content_type) stay NULL —
    article table allows these, and a future harvest-post run can backfill
    via prompt_version mismatch detection (same mechanism as backfill_category.py).
    """
    return {
        "article_id": r["article_id"],
        "blog_id": int(r["blog_id"]) if r.get("blog_id") is not None else None,
        "url": r.get("url"),
        "title": r.get("title"),
        "thumbnail": r.get("thumbnail"),
        "description": r.get("description"),
        "keywords": r.get("judge_keywords"),
        "category_id": r.get("judge_category"),
        "content": r.get("content"),
        "content_length": r.get("content_length"),
        "lang": r.get("lang"),
        "quality_score": r.get("judge_quality"),
        "difficulty": r.get("judge_difficulty"),
        "content_type": r.get("judge_content_type"),
        "summary": r.get("judge_summary"),
        "prompt_version": PROMPT_VERSION,
        "created_at": created_at,
        "updated_at": updated_at,
        "published_at": r.get("published_at"),
    }


async def embed_row(row: dict, embedder: TextEmbeddings) -> Optional[dict]:
    try:
        embedding = await embedder(
            title=row["title"] or "",
            description=row.get("description") or "",
            keywords=row.get("keywords") or "",
            content=row.get("content") or "",
            dimensions=int(os.getenv("EMBEDDING_SIZE", EMBEDDING_SIZE)),
        )
    except Exception as e:
        print(f"[EMBED ERROR] {row['article_id']}: {e}")
        return None

    published_at = row.get("published_at")
    pub_epoch = 0.0
    if published_at is not None:
        try:
            pub_epoch = float(published_at.timestamp()) if hasattr(published_at, "timestamp") else float(published_at)
        except (TypeError, ValueError):
            pub_epoch = 0.0

    return {
        "id": row["article_id"],
        "vector": embedding,
        "payload": {
            "article_id": row["article_id"],
            "category": row.get("category_id"),
            "published_at": pub_epoch,
            "quality_score": row.get("quality_score"),
            "difficulty": row.get("difficulty"),
            "content_type": row.get("content_type"),
            "prompt_version": row.get("prompt_version"),
            "blog_id": row.get("blog_id"),
            "content_length": row.get("content_length"),
            "lang": row.get("lang"),
            "recovered_from_rejection": True,  # provenance for monitoring
        },
    }


async def run_recovery(args) -> RecoveryCounts:
    conn = Connection()
    counts = RecoveryCounts()

    # Inline the status tuple — small constant set, safe from SQLi.
    status_tuple = "(" + ",".join(f"'{s}'" for s in RECOVERABLE_STATUSES) + ")"
    select_sql = (
        "SELECT rq.id AS review_id, rq.article_id, rq.judge_category, "
        "rq.judge_keywords, rq.judge_quality, rq.judge_difficulty, "
        "rq.judge_content_type, rq.judge_summary, rq.review_status, "
        "ar.blog_id, ar.url, ar.title, ar.thumbnail, ar.description, "
        "ar.content, ar.content_length, ar.lang, ar.published_at "
        "FROM article_review_queue rq "
        "JOIN article_rejected ar ON ar.article_id = rq.article_id "
        f"WHERE rq.review_status IN {status_tuple} "
        "AND rq.resolved_at IS NULL "
        "ORDER BY rq.audited_at ASC "
        f"LIMIT {int(args.limit)}"
    )
    df = conn.execute(select_sql)
    rows = df.to_dict(orient="records") if df is not None else []
    counts.eligible = len(rows)
    print(f"[RECOVER] {len(rows)} rows eligible for recovery")
    if not rows:
        conn.close()
        return counts

    if args.dry_run:
        for r in rows[:20]:
            print(
                f"  [DRY] {r['article_id']}  status={r['review_status']}  "
                f"judge_q={r['judge_quality']}  cat={r['judge_category']}  "
                f"title={(r.get('title') or '')[:60]}"
            )
        if len(rows) > 20:
            print(f"  ... and {len(rows) - 20} more")
        conn.close()
        return counts

    # --- Build article rows ---
    now = None
    # Use DB NOW() so timestamps are consistent regardless of script clock.
    # Simplest: let the upsert leave created_at/updated_at as-is (they get
    # the DB default) — but INSERT_QUERY expects them as bind params. Pass
    # Python datetime.
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    article_rows = [build_article_row(r, created_at=now, updated_at=now) for r in rows]

    # --- Embed in parallel (CPU embedder, use modest concurrency) ---
    embedder = TextEmbeddings()
    store = QdrantVectorStore(
        collection_name="bite-vectordb",
        vector_dim=int(os.getenv("EMBEDDING_SIZE", EMBEDDING_SIZE)),
    )

    sem = asyncio.Semaphore(4)

    async def _bounded_embed(r):
        async with sem:
            return await embed_row(r, embedder)

    embed_tasks = [_bounded_embed(r) for r in article_rows]
    embed_results = await asyncio.gather(*embed_tasks)

    qdrant_points = []
    successful_rows = []
    successful_review_ids = []
    for i, point in enumerate(embed_results):
        if point is None:
            counts.failed += 1
            continue
        qdrant_points.append(point)
        successful_rows.append(article_rows[i])
        successful_review_ids.append(rows[i]["review_id"])
        counts.embedded += 1

    if not successful_rows:
        print("[RECOVER] No rows embedded successfully — nothing to recover")
        conn.close()
        return counts

    # --- Qdrant upsert FIRST, then MySQL insert. Order matters: ---
    # If qdrant fails, we don't want article rows without vectors (silent
    # broken recommendations). If MySQL fails after qdrant succeeds, we get
    # an orphan vector (harmless — next time we try again, upsert is idempotent).
    try:
        print(f"[QDRANT] Upserting {len(qdrant_points)} points...")
        await asyncio.to_thread(store.upsert_points, qdrant_points)
        counts.qdrant_upserted = len(qdrant_points)
    except Exception as e:
        print(f"[QDRANT ERROR] Batch upsert failed, aborting recovery: {e}")
        conn.close()
        return counts

    # --- Insert into article (upsert — in case of reprocessing idempotency) ---
    try:
        print(f"[MYSQL] Upserting {len(successful_rows)} rows into {ARTICLE_TABLE}...")
        await asyncio.to_thread(conn.session_execute, INSERT_QUERY, successful_rows)
        counts.inserted = len(successful_rows)
    except Exception as e:
        print(f"[MYSQL ERROR] article upsert failed: {e}")
        conn.close()
        return counts

    # --- Delete from article_rejected + mark review queue resolved ---
    recovered_ids = [r["article_id"] for r in successful_rows]
    delete_stmt = text(
        "DELETE FROM article_rejected WHERE article_id IN :ids"
    ).bindparams(bindparam("ids", expanding=True))
    try:
        await asyncio.to_thread(conn.session_execute, delete_stmt, {"ids": recovered_ids})
    except Exception as e:
        print(f"[CLEANUP WARN] failed to delete recovered rows from article_rejected: {e}")

    resolved_note = f" [recovered_at={time.strftime('%Y-%m-%d %H:%M:%S')}]"
    mark_stmt = text(
        "UPDATE article_review_queue "
        "SET resolved_at = CURRENT_TIMESTAMP, "
        "    notes = CONCAT(IFNULL(notes, ''), :note) "
        "WHERE id IN :review_ids"
    ).bindparams(bindparam("review_ids", expanding=True))
    try:
        await asyncio.to_thread(
            conn.session_execute,
            mark_stmt,
            {"review_ids": [int(i) for i in successful_review_ids], "note": resolved_note},
        )
    except Exception as e:
        print(f"[CLEANUP WARN] failed to mark review queue resolved: {e}")

    conn.close()
    return counts


def main():
    parser = argparse.ArgumentParser(description="Recover false-positive rejected articles")
    parser.add_argument("--limit", type=int, default=200, help="Max rows to recover per run")
    parser.add_argument("--dry-run", action="store_true", help="Show eligible rows without modifying DB")
    args = parser.parse_args()

    counts = asyncio.run(run_recovery(args))
    print(f"\n[RECOVER SUMMARY] {counts.as_dict()}")


if __name__ == "__main__":
    main()
