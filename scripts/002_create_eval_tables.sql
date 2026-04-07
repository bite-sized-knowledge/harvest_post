-- LLM 평가 인프라 테이블
-- 실행: mysql -u <user> -p <database> < scripts/002_create_eval_tables.sql

-- 1. article 테이블에 quality_score 컬럼 추가 (Qdrant에만 있던 것을 MySQL에도 저장)
-- MySQL 8.0에서는 IF NOT EXISTS 미지원. 이미 존재하면 수동으로 이 줄 스킵.
ALTER TABLE article ADD COLUMN quality_score TINYINT NULL AFTER lang;

-- 2. 추론 로그: 모든 LLM 호출 기록
CREATE TABLE IF NOT EXISTS llm_inference_log (
    id              BIGINT AUTO_INCREMENT PRIMARY KEY,
    article_id      CHAR(27) NOT NULL,
    model_key       VARCHAR(100) NOT NULL,
    model_version   VARCHAR(200) NULL,
    temperature     FLOAT NOT NULL DEFAULT 0.1,
    input_text      LONGTEXT NULL,
    raw_output      TEXT NULL,
    parsed_output   JSON NULL,
    parse_success   BOOLEAN NOT NULL DEFAULT TRUE,
    category_id     TINYINT NULL,
    keywords        VARCHAR(255) NULL,
    quality_score   TINYINT NULL,
    latency_ms      INT NULL,
    input_tokens    INT NULL,
    output_tokens   INT NULL,
    retry_count     TINYINT NOT NULL DEFAULT 0,
    outcome         VARCHAR(30) NOT NULL,
    is_shadow       BOOLEAN NOT NULL DEFAULT FALSE,
    eval_run_id     VARCHAR(64) NULL,
    created_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_article (article_id),
    INDEX idx_model (model_key),
    INDEX idx_created (created_at),
    INDEX idx_eval_run (eval_run_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 3. Golden dataset: 사람이 라벨링한 평가용 아티클
CREATE TABLE IF NOT EXISTS llm_eval_golden (
    id              BIGINT AUTO_INCREMENT PRIMARY KEY,
    article_id      CHAR(27) NOT NULL,
    human_category  TINYINT NOT NULL,
    human_keywords  VARCHAR(255) NOT NULL,
    human_quality   TINYINT NOT NULL,
    frozen_input    LONGTEXT NOT NULL,
    notes           TEXT NULL,
    created_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_article (article_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 4. 평가 결과: eval run별 집계 메트릭
CREATE TABLE IF NOT EXISTS llm_eval_result (
    id                      BIGINT AUTO_INCREMENT PRIMARY KEY,
    eval_run_id             VARCHAR(64) NOT NULL,
    model_key               VARCHAR(100) NOT NULL,
    model_version           VARCHAR(200) NULL,
    golden_count            INT NOT NULL,
    category_accuracy       FLOAT NULL,
    category_macro_f1       FLOAT NULL,
    quality_mae             FLOAT NULL,
    quality_spearman        FLOAT NULL,
    quality_within_1        FLOAT NULL,
    keyword_avg_relevance   FLOAT NULL,
    parse_success_rate      FLOAT NULL,
    avg_latency_ms          INT NULL,
    per_article_json        JSON NULL,
    created_at              TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uk_run (eval_run_id),
    INDEX idx_model (model_key),
    INDEX idx_created (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
