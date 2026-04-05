-- article_rejected: rows filtered out by pre-LLM gates or by LLM quality scoring.
-- Schema mirrors article_queue (the point at which rejection happens) plus
-- audit columns (quality_score, reject_reason, rejected_at).
-- Retained so we can monitor false positives and tune the LLM prompt.

CREATE TABLE IF NOT EXISTS article_rejected (
    article_id     CHAR(27)       NOT NULL PRIMARY KEY,
    blog_id        BIGINT         NULL,
    url            VARCHAR(500)   NULL,
    title          VARCHAR(255)   NULL,
    thumbnail      VARCHAR(500)   NULL,
    description    VARCHAR(1000)  NULL,
    content        LONGTEXT       NULL,
    content_length BIGINT         NULL,
    lang           VARCHAR(10)    NULL,
    published_at   TIMESTAMP      NULL,

    quality_score  TINYINT        NULL,
    reject_reason  VARCHAR(100)   NOT NULL,
    rejected_at    TIMESTAMP      NOT NULL DEFAULT CURRENT_TIMESTAMP,

    KEY idx_rejected_blog (blog_id),
    KEY idx_rejected_reason (reject_reason),
    KEY idx_rejected_at (rejected_at)
);
