"""Pipeline observability — job_run + article_failed (dead-letter).

job_run is an audit log of every harvest_post / audit_rejected / recover_rejected
invocation. article_failed is the dead-letter for permanent failures
(LLM/embedding/db_insert) so we can see *why* something dropped instead of
silently losing it like the print()-and-drop pattern did.

Both tables created in infra/mysql/init/012_pipeline_observability.sql.
"""
from __future__ import annotations

import json
import os
import socket
import time
import traceback as tb_mod
from enum import Enum
from typing import Any, Optional, Union

from sqlalchemy.sql import text

from logger import logger


class JobStatus(str, Enum):
    """job_run.status 값. 모니터의 lib/pipelineConsts.ts와 값 일치."""
    RUNNING = "running"
    SUCCESS = "success"
    PARTIAL = "partial"
    FAILED = "failed"


class Stage(str, Enum):
    """article_failed.stage 값 (dead-letter 분류). errors.ErrorCategory 값과 의도적 호환."""
    LLM = "llm"
    EMBEDDING = "embedding"
    QDRANT = "qdrant"
    PROCESS_ARTICLE = "process_article"
    PARSING = "parsing"
    DATABASE = "database"
    VALIDATION = "validation"
    UNKNOWN = "unknown"


StageLike = Union[Stage, str]


_PAYLOAD_SKIP_KEYS = frozenset({"content", "description"})


_INSERT_JOB_SQL = text("""
    INSERT INTO job_run (job_name, started_at, status, host)
    VALUES (:job_name, CURRENT_TIMESTAMP(3), :status, :host)
""")

_UPDATE_JOB_SQL = text("""
    UPDATE job_run
       SET finished_at      = CURRENT_TIMESTAMP(3),
           duration_ms      = :duration_ms,
           status           = :status,
           queued_count     = :queued_count,
           processed_count  = :processed_count,
           rejected_count   = :rejected_count,
           failed_count     = :failed_count,
           recovered_count  = :recovered_count,
           stage_breakdown  = :stage_breakdown,
           error_summary    = :error_summary
     WHERE id = :id
""")

_INSERT_FAILED_SQL = text("""
    INSERT INTO article_failed
        (article_id, blog_id, url, title, job_id, stage,
         error_category, error_severity, error_class,
         error_message, traceback, payload)
    VALUES
        (:article_id, :blog_id, :url, :title, :job_id, :stage,
         :error_category, :error_severity, :error_class,
         :error_message, :traceback, :payload)
""")


class JobRun:
    """Context manager for a single pipeline run.

    Usage:
        with JobRun(conn, "harvest_post") as job:
            job.set_queued(N)
            ...
            job.inc("processed", k)
            job.add_failure(stage="embedding", article_id="...", exc=e, queue_row=row)
    """

    _COUNTER_FIELDS = ("queued", "processed", "rejected", "failed", "recovered")

    def __init__(self, conn, job_name: str):
        self.conn = conn
        self.job_name = job_name
        self.id: Optional[int] = None
        self._start_t = 0.0
        self.counters: dict[str, int] = {f: 0 for f in self._COUNTER_FIELDS}
        self.stage: dict[str, int] = {}
        self.error_summary: Optional[str] = None

    @property
    def queued(self) -> int: return self.counters["queued"]
    @property
    def processed(self) -> int: return self.counters["processed"]
    @property
    def rejected(self) -> int: return self.counters["rejected"]
    @property
    def failed(self) -> int: return self.counters["failed"]
    @property
    def recovered(self) -> int: return self.counters["recovered"]

    # ----- lifecycle -----
    def __enter__(self) -> "JobRun":
        self._start_t = time.monotonic()
        host = socket.gethostname()
        try:
            session = self.conn.SessionLocal()
            try:
                result = session.execute(_INSERT_JOB_SQL, {
                    "job_name": self.job_name,
                    "host": host,
                    "status": JobStatus.RUNNING.value,
                })
                self.id = result.lastrowid
                session.commit()
            finally:
                session.close()
            logger.info("Job started", job=self.job_name, job_id=self.id, host=host)
        except Exception as e:
            logger.error("job_run insert failed (continuing without tracking)",
                         job=self.job_name, error=str(e))
            self.id = None
        return self

    def __exit__(self, exc_type, exc, tb):
        duration_ms = int((time.monotonic() - self._start_t) * 1000)
        if exc is not None:
            status = JobStatus.FAILED.value
            self.error_summary = f"{type(exc).__name__}: {exc}"
        elif self.failed > 0 and self.processed == 0:
            status = JobStatus.FAILED.value
        elif self.failed > 0:
            status = JobStatus.PARTIAL.value
        else:
            status = JobStatus.SUCCESS.value

        logger.info(
            "Job finished",
            job=self.job_name,
            job_id=self.id,
            status=status,
            duration_ms=duration_ms,
            queued=self.queued,
            processed=self.processed,
            rejected=self.rejected,
            failed=self.failed,
            recovered=self.recovered,
            stage_breakdown=self.stage,
        )

        if self.id is None:
            return False  # don't suppress

        try:
            self.conn.session_execute(_UPDATE_JOB_SQL, {
                "id": self.id,
                "duration_ms": duration_ms,
                "status": status,
                "queued_count": self.queued,
                "processed_count": self.processed,
                "rejected_count": self.rejected,
                "failed_count": self.failed,
                "recovered_count": self.recovered,
                "stage_breakdown": json.dumps(self.stage, ensure_ascii=False) if self.stage else None,
                "error_summary": self.error_summary,
            })
        except Exception as e:
            logger.error("job_run update failed", job_id=self.id, error=str(e))
        return False

    def set_queued(self, n: int) -> None:
        self.counters["queued"] = int(n)

    def inc(self, field: str, n: int = 1) -> None:
        if field not in self._COUNTER_FIELDS:
            raise ValueError(f"unknown counter: {field}")
        self.counters[field] += int(n)

    def bump_stage(self, stage: str, n: int = 1) -> None:
        self.stage[stage] = self.stage.get(stage, 0) + n

    # ----- dead-letter -----
    def add_failure(
        self,
        *,
        stage: StageLike,
        article_id: Optional[str] = None,
        blog_id: Optional[int] = None,
        url: Optional[str] = None,
        title: Optional[str] = None,
        exc: Optional[BaseException] = None,
        error_category: Optional[str] = None,
        error_severity: Optional[str] = None,
        error_message: Optional[str] = None,
        queue_row: Optional[dict[str, Any]] = None,
    ) -> None:
        """Insert a row into article_failed and bump counters.

        `exc` and explicit `error_*` fields are both supported; explicit fields win.
        `queue_row` is JSON-serialized into payload (LONGTEXT fields stripped).
        """
        stage_str = stage.value if isinstance(stage, Stage) else str(stage)
        self.counters["failed"] += 1
        self.bump_stage(f"{stage_str}_failed", 1)

        error_class = type(exc).__name__ if exc is not None else None
        tb_str = "".join(tb_mod.format_exception(type(exc), exc, exc.__traceback__)) if exc is not None else None
        message = error_message or (str(exc) if exc is not None else None)

        payload_json: Optional[str] = None
        if queue_row is not None:
            # content/description can be LONGTEXT (~MBs); replay-able fields only.
            slim = {k: v for k, v in queue_row.items() if k not in _PAYLOAD_SKIP_KEYS}
            try:
                payload_json = json.dumps(slim, ensure_ascii=False, default=str)
            except Exception:
                payload_json = json.dumps({"_serialize_error": True, "keys": list(slim.keys())})

        try:
            self.conn.session_execute(_INSERT_FAILED_SQL, {
                "article_id": article_id,
                "blog_id": blog_id,
                "url": url,
                "title": title,
                "job_id": self.id,
                "stage": stage_str,
                "error_category": error_category,
                "error_severity": error_severity,
                "error_class": error_class,
                "error_message": (message or "")[:65000] or None,
                "traceback": (tb_str or "")[:65000] or None,
                "payload": payload_json,
            })
            logger.error(
                "Article failed (dead-letter)",
                stage=stage,
                article_id=article_id,
                error_class=error_class,
                error_message=message,
                job_id=self.id,
            )
        except Exception as e:
            # Don't let dead-letter write itself crash the pipeline.
            logger.error(
                "article_failed insert failed — original error preserved in logs only",
                stage=stage,
                article_id=article_id,
                original_error=str(message),
                dlq_error=str(e),
            )


def update_queue_status(
    conn,
    article_id: str,
    *,
    status: str,
    error: Optional[str] = None,
) -> None:
    """Mark an article_queue row's transient status.

    'queued' (default) → 'processing' → 'transient_failed' (left in queue for retry).
    Permanent failures DELETE from queue and write to article_failed instead;
    they don't go through this function.
    """
    sql = text("""
        UPDATE article_queue
           SET status = :status,
               last_error = :error,
               last_attempted_at = CURRENT_TIMESTAMP,
               attempt_count = attempt_count + 1
         WHERE article_id = :article_id
    """)
    try:
        conn.session_execute(sql, {
            "article_id": article_id,
            "status": status,
            "error": (error or "")[:65000] or None,
        })
    except Exception as e:
        logger.error("article_queue status update failed",
                     article_id=article_id, error=str(e))


def delete_from_queue_sync(conn, ids: list[str]) -> None:
    """Permanent failure path: drop from queue (after dead-letter insert)."""
    if not ids:
        return
    from sqlalchemy.sql import bindparam
    stmt = text("DELETE FROM article_queue WHERE article_id IN :ids").bindparams(
        bindparam("ids", expanding=True)
    )
    conn.session_execute(stmt, {"ids": ids})
