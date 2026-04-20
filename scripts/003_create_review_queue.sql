-- article_review_queue: records every rejected article that's been re-judged
-- by an independent local LLM (HyperCLOVA X SEED Think) so we can recover
-- false positives from the Qwen-based rejection step.
--
-- Pairs with article_rejected (created in 001). One row per (article_id).
-- review_status drives the downstream recovery + human review flow:
--   pending                → queued for human inspection (judge disagreed but
--                             not strong enough for auto-recover)
--   auto_recovered         → passes the conservative auto-gate; next run of
--                             recover_rejected.py will move it back to article
--   human_confirmed_fp     → human reviewed and confirmed as false positive
--   human_confirmed_reject → human reviewed and confirmed original reject was right
--   skipped                → judge call failed / irrelevant reject reason
--
-- Watermark for "what's been audited?" = MAX(audited_at) on this table.

CREATE TABLE IF NOT EXISTS article_review_queue (
    id               BIGINT AUTO_INCREMENT PRIMARY KEY,
    -- Collation must match article_rejected.article_id (utf8mb4_0900_ai_ci)
    -- for JOIN/NOT EXISTS comparisons to work without COLLATE clauses.
    article_id       CHAR(27) CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci NOT NULL,

    -- Snapshot of the original rejection decision (denormalized from
    -- article_rejected so the audit trail survives if the source row is
    -- later deleted during recovery).
    original_quality TINYINT      NOT NULL,
    original_reason  VARCHAR(100) NOT NULL,

    -- Judge verdict
    judge_model      VARCHAR(100) NOT NULL,
    judge_quality    TINYINT      NULL,
    judge_category   TINYINT      NULL,
    judge_keywords   VARCHAR(255) NULL,
    judge_summary    VARCHAR(255) NULL,
    judge_difficulty TINYINT      NULL,
    judge_content_type VARCHAR(30) NULL,
    judge_reasoning  TEXT         NULL,
    judge_evidence   TEXT         NULL,  -- JSON array of concrete tech evidence points
    judge_confidence FLOAT        NULL,  -- judge self-reported 0~1

    -- Computed at audit time so queries like "strong disagreement" don't
    -- need to re-derive it (and NULL judge_quality naturally yields NULL).
    disagreement     TINYINT      NULL,

    review_status    ENUM('pending','auto_recovered','human_confirmed_fp',
                         'human_confirmed_reject','skipped')
                     NOT NULL DEFAULT 'pending',
    resolved_at      TIMESTAMP    NULL,
    notes            TEXT         NULL,

    audited_at       TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,

    UNIQUE KEY uk_article (article_id),
    KEY idx_review_status (review_status),
    KEY idx_disagreement (disagreement),
    KEY idx_audited_at (audited_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
