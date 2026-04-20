"""Re-judge articles in article_rejected with the local HyperCLOVA X judge.

Writes every verdict to article_review_queue and logs the raw inference to
llm_inference_log (is_shadow=TRUE, model_key='hyperclovax-judge') so the
existing eval infra can treat judge runs as a shadow model.

Auto-recover gate (conservative — HyperCLOVA is roughly the same capability
tier as Qwen 9B, so we lean on multiple signals instead of trusting a single
quality bump):
  - judge_quality >= 4
  - judge_confidence >= 0.75
  - disagreement >= 2  (i.e. judge thinks it's clearly better than reject threshold)
  - len(evidence_points) >= MIN_EVIDENCE_COUNT

If any condition fails but disagreement >= 1, the row lands in review_status='pending'
and goes to the human review JSONL (export_review_batch.py).

Usage:
    cd src && python ../scripts/review/audit_rejected.py \\
        --since 24h --limit 200 --time-budget 15m

    # Preflight
    cd src && python ../scripts/review/audit_rejected.py --limit 5 --dry-run
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import time
from dataclasses import dataclass
from typing import Optional

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from db_conn import Connection
from llm_pipeline.judge import (
    JUDGE_MODEL_KEY,
    JUDGE_MODEL_VERSION,
    JUDGE_TEMPERATURE,
    MIN_EVIDENCE_COUNT,
    JudgeInvocation,
    LocalJudge,
)
from llm_pipeline.inference_logger import InferenceLogger
from llm_pipeline.model import InferenceResult
from preprocessing import BlogPostProcessor, parse_article_text_from_html
from sqlalchemy import text

PREPROCESSOR = BlogPostProcessor()

# --- Auto-recover gate constants (see module docstring) ---
AUTO_QUALITY_MIN = 4
AUTO_CONFIDENCE_MIN = 0.75
AUTO_DISAGREEMENT_MIN = 2

# --- Priority weights for stratified sampling ---
# Lower number = higher priority. Kept in-query as a CASE expression so the
# DB does the ordering (simpler than fetching everything + sorting in Python).
REASON_PRIORITY = {
    "low_quality_2": 1,          # closest to threshold=3 → highest FP risk
    "llm_extraction_failed": 2,  # parse failure, content might actually be good
    "low_quality_1": 3,
    "empty_content": 4,
}

# Reasons we never audit (always correct).
SKIP_REASONS = {"empty_title"}


UPSERT_REVIEW_SQL = text("""
    INSERT INTO article_review_queue
        (article_id, original_quality, original_reason,
         judge_model, judge_quality, judge_category, judge_keywords,
         judge_summary, judge_difficulty, judge_content_type,
         judge_reasoning, judge_evidence, judge_confidence,
         disagreement, review_status, notes)
    VALUES
        (:article_id, :original_quality, :original_reason,
         :judge_model, :judge_quality, :judge_category, :judge_keywords,
         :judge_summary, :judge_difficulty, :judge_content_type,
         :judge_reasoning, :judge_evidence, :judge_confidence,
         :disagreement, :review_status, :notes)
    ON DUPLICATE KEY UPDATE
        original_quality = VALUES(original_quality),
        original_reason  = VALUES(original_reason),
        judge_model      = VALUES(judge_model),
        judge_quality    = VALUES(judge_quality),
        judge_category   = VALUES(judge_category),
        judge_keywords   = VALUES(judge_keywords),
        judge_summary    = VALUES(judge_summary),
        judge_difficulty = VALUES(judge_difficulty),
        judge_content_type = VALUES(judge_content_type),
        judge_reasoning  = VALUES(judge_reasoning),
        judge_evidence   = VALUES(judge_evidence),
        judge_confidence = VALUES(judge_confidence),
        disagreement    = VALUES(disagreement),
        review_status   = VALUES(review_status),
        notes           = VALUES(notes),
        audited_at      = CURRENT_TIMESTAMP
""")


SELECT_CANDIDATES_SQL = """
    SELECT
        ar.article_id, ar.blog_id, ar.url, ar.title, ar.description, ar.content,
        ar.quality_score AS original_quality,
        ar.reject_reason AS original_reason,
        ar.rejected_at
    FROM article_rejected ar
    WHERE ar.rejected_at > :since
      AND ar.reject_reason NOT IN :skip_reasons
      AND NOT EXISTS (
          SELECT 1 FROM article_review_queue rq
          WHERE rq.article_id = ar.article_id
      )
    ORDER BY
        CASE ar.reject_reason
            WHEN 'low_quality_2' THEN 1
            WHEN 'llm_extraction_failed' THEN 2
            WHEN 'low_quality_1' THEN 3
            WHEN 'empty_content' THEN 4
            ELSE 5
        END,
        ar.rejected_at DESC
    LIMIT :limit
"""


def parse_duration(spec: str) -> float:
    """'24h' / '30m' / '90s' → seconds. Bare number → seconds."""
    spec = spec.strip().lower()
    m = re.match(r"^(\d+(?:\.\d+)?)([smhd])?$", spec)
    if not m:
        raise argparse.ArgumentTypeError(f"Invalid duration: {spec}")
    val = float(m.group(1))
    unit = m.group(2) or "s"
    return val * {"s": 1, "m": 60, "h": 3600, "d": 86400}[unit]


def build_judge_body(title: str, description: str, content: str) -> tuple[str, str]:
    """Apply same preprocessing main.py uses, return (desc_processed, body_processed).

    Judge sees the same cleaned text the primary scorer would see — otherwise
    a difference could come from preprocessing, not from model disagreement.
    """
    parsed_text = parse_article_text_from_html(content) if content else ""
    desc_processed = PREPROCESSOR.process(description) if description else ""

    if not parsed_text.strip():
        # Video-centric / empty body — match main.py's fallback framing so
        # judge sees the same "scoring on title+description" prompt shape.
        return desc_processed, (
            "[NOTE: This is a video-centric or body-empty post. "
            "Judge quality from title and description.]"
        )

    body_processed = PREPROCESSOR.process(parsed_text)
    # Judge sees the same body, but capped tighter than the primary scorer
    # (6000 chars). The judge's system prompt is ~3500 tokens of rubric +
    # Q1/Q2/Q3 scaffolding, so body+prompt+output must fit in 8192 max ctx.
    # 4500 chars ≈ 3000 tokens leaves ~1700 for system+output JSON.
    if len(body_processed) > 4500:
        body_processed = body_processed[:4500]
    return desc_processed, body_processed


def compute_gate(
    judge_quality: Optional[int],
    judge_confidence: Optional[float],
    disagreement: Optional[int],
    evidence_count: int,
    judge_category: Optional[int] = None,
) -> str:
    """Return review_status per the auto-recover gate rules.

    Category sanity check (judge_category != 13/etc): the first real run
    showed Qwen2.5-14B dumping every auto_recovered into category=etc with
    marketing/announcement content. Forcing the judge to commit to a
    specific category for auto-recover filters most of those false-recoveries
    even when other gates accidentally pass. Articles the judge legitimately
    thinks are etc-but-valuable still go to human review (pending).
    """
    if judge_quality is None:
        return "skipped"

    CATEGORY_ETC = 13
    all_gates_pass = (
        judge_quality >= AUTO_QUALITY_MIN
        and (judge_confidence or 0) >= AUTO_CONFIDENCE_MIN
        and (disagreement or 0) >= AUTO_DISAGREEMENT_MIN
        and evidence_count >= MIN_EVIDENCE_COUNT
        and judge_category is not None
        and judge_category != CATEGORY_ETC
    )
    if all_gates_pass:
        return "auto_recovered"

    # Disagreement exists but not strong enough → human review
    if (disagreement or 0) >= 1:
        return "pending"

    # Judge agrees with original reject
    return "skipped"


@dataclass
class AuditSummary:
    attempted: int = 0
    parse_success: int = 0
    auto_recovered: int = 0
    pending: int = 0
    skipped: int = 0
    time_budget_hit: bool = False

    def as_dict(self) -> dict:
        return {
            "attempted": self.attempted,
            "parse_success": self.parse_success,
            "auto_recovered": self.auto_recovered,
            "pending": self.pending,
            "skipped": self.skipped,
            "time_budget_hit": self.time_budget_hit,
        }


async def run_audit(args) -> AuditSummary:
    conn = Connection()
    judge = LocalJudge()

    # Wait briefly for judge endpoint — run.sh starts the container with
    # --wait but its healthcheck may report healthy before the model is
    # fully loaded on very large models. Ping /v1/models before we start.
    await _wait_for_judge(judge)

    inf_logger = InferenceLogger(
        model_key=JUDGE_MODEL_KEY,
        model_version=JUDGE_MODEL_VERSION,
        temperature=JUDGE_TEMPERATURE,
        is_shadow=True,
    )

    # Inline the constants: SKIP_REASONS is a compile-time set, limit is
    # validated argparse int, since_sec comes from parse_duration on our own
    # input. No user-provided strings land in the query — safe from SQLi.
    skip_tuple = "(" + ",".join(f"'{r}'" for r in SKIP_REASONS) + ")"
    since_clause = (
        "1=1" if args.backfill
        else f"ar.rejected_at > DATE_SUB(NOW(), INTERVAL {int(parse_duration(args.since))} SECOND)"
    )
    query = (
        SELECT_CANDIDATES_SQL
        .replace("ar.rejected_at > :since", since_clause)
        .replace(":skip_reasons", skip_tuple)
        .replace(":limit", str(int(args.limit)))
    )

    df = conn.execute(query)
    rows = df.to_dict(orient="records") if df is not None else []
    print(f"[AUDIT] {len(rows)} candidates queued (since={args.since}, limit={args.limit})")
    if not rows:
        conn.close()
        return AuditSummary()

    summary = AuditSummary()
    budget_sec = parse_duration(args.time_budget)
    t_start = time.monotonic()

    insert_buffer: list[dict] = []

    for row in rows:
        elapsed = time.monotonic() - t_start
        if elapsed > budget_sec:
            summary.time_budget_hit = True
            print(f"[AUDIT] Time budget exceeded ({elapsed:.0f}s > {budget_sec:.0f}s) — stopping")
            break

        summary.attempted += 1
        article_id = row["article_id"]
        desc_processed, body_processed = build_judge_body(
            title=row.get("title") or "",
            description=row.get("description") or "",
            content=row.get("content") or "",
        )

        invocation: JudgeInvocation = await judge.judge(
            article_id=article_id,
            title=row.get("title") or "",
            description=desc_processed,
            body=body_processed,
            original_quality=int(row.get("original_quality") or 1),
            original_reason=row.get("original_reason") or "unknown",
        )

        parsed = invocation.parsed
        judge_quality = parsed.quality_score if parsed else None
        judge_category = parsed.focusing.value if parsed else None
        judge_keywords = "\t".join(parsed.keywords) if parsed else None
        judge_summary = parsed.summary if parsed else None
        judge_difficulty = parsed.difficulty if parsed else None
        judge_content_type = (
            parsed.content_type.value if parsed and parsed.content_type else None
        )
        judge_reasoning = parsed.reasoning if parsed else None
        evidence_list = parsed.evidence_points if parsed else []
        judge_evidence_json = json.dumps(evidence_list, ensure_ascii=False)
        judge_confidence = parsed.confidence if parsed else None

        disagreement: Optional[int] = None
        if judge_quality is not None:
            disagreement = int(judge_quality) - int(row.get("original_quality") or 0)

        review_status = compute_gate(
            judge_quality=judge_quality,
            judge_confidence=judge_confidence,
            disagreement=disagreement,
            evidence_count=len(evidence_list),
            judge_category=judge_category,
        )

        if review_status == "auto_recovered":
            summary.auto_recovered += 1
        elif review_status == "pending":
            summary.pending += 1
        else:
            summary.skipped += 1
        if parsed is not None:
            summary.parse_success += 1

        if args.verbose:
            print(
                f"  [{article_id}] orig={row.get('original_quality')}"
                f"/{row.get('original_reason')} → judge={judge_quality} "
                f"(conf={judge_confidence}, evidence={len(evidence_list)}) "
                f"→ {review_status}"
            )

        insert_buffer.append({
            "article_id": article_id,
            "original_quality": int(row.get("original_quality") or 0),
            "original_reason": row.get("original_reason") or "unknown",
            "judge_model": JUDGE_MODEL_KEY,
            "judge_quality": judge_quality,
            "judge_category": judge_category,
            "judge_keywords": judge_keywords,
            "judge_summary": judge_summary,
            "judge_difficulty": judge_difficulty,
            "judge_content_type": judge_content_type,
            "judge_reasoning": (judge_reasoning or "")[:16000] or None,
            "judge_evidence": judge_evidence_json,
            "judge_confidence": judge_confidence,
            "disagreement": disagreement,
            "review_status": review_status,
            "notes": None,
        })

        # Also capture the raw LLM call into llm_inference_log so eval infra
        # sees judge runs as a shadow model. Wrap JudgeInvocation → InferenceResult
        # duck-typed (InferenceLogger.log only reads attributes, doesn't type-check).
        shim = InferenceResult(
            parsed=parsed,  # JudgeResult has .focusing/.keywords/.quality_score/etc.
            raw_output=invocation.raw_output,
            latency_ms=invocation.latency_ms,
            input_tokens=invocation.input_tokens,
            output_tokens=invocation.output_tokens,
            retry_count=invocation.retry_count,
            parse_success=invocation.parse_success,
        )
        inf_logger.log(
            article_id=article_id,
            input_text=f"Title : {row.get('title') or ''}, Description : {desc_processed}, Body Content : {body_processed}",
            result=shim,
            outcome=f"audit_{review_status}",
        )

    if args.dry_run:
        print(f"[DRY-RUN] Would upsert {len(insert_buffer)} rows into article_review_queue")
    else:
        if insert_buffer:
            await asyncio.to_thread(conn.session_execute, UPSERT_REVIEW_SQL, insert_buffer)
            print(f"[AUDIT] Upserted {len(insert_buffer)} rows into article_review_queue")
        await inf_logger.flush(conn)

    conn.close()
    return summary


async def _wait_for_judge(judge: LocalJudge, timeout: int = 60) -> None:
    """Best-effort wait for the judge vLLM endpoint to be responsive."""
    import httpx
    health_url = judge.base_url.rstrip("/").replace("/v1", "") + "/health"
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                r = await client.get(health_url)
                if r.status_code == 200:
                    return
        except Exception:
            pass
        await asyncio.sleep(2)
    print(f"[JUDGE] WARN: /health not ready within {timeout}s — proceeding anyway")


def main():
    parser = argparse.ArgumentParser(description="Re-judge rejected articles with local judge")
    parser.add_argument("--since", default="24h", help="Look at rejected_at > now-SINCE (e.g. 24h, 7d)")
    parser.add_argument("--limit", type=int, default=200, help="Max candidates per run")
    parser.add_argument("--time-budget", default="15m", help="Hard wall-clock cap (e.g. 15m, 30m)")
    parser.add_argument("--backfill", action="store_true", help="Ignore --since, audit all unaudited")
    parser.add_argument("--dry-run", action="store_true", help="Don't write to DB")
    parser.add_argument("--verbose", "-v", action="store_true", help="Per-article output")
    args = parser.parse_args()

    summary = asyncio.run(run_audit(args))
    print(f"\n[AUDIT SUMMARY] {summary.as_dict()}")


if __name__ == "__main__":
    main()
