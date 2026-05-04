import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()

# ---------- Table Configuration ----------
ARTICLE_TABLE = os.getenv('ARTICLE_TABLE')
QUEUED_TABLE = "article_queue"
REJECTED_TABLE = "article_rejected"
if not ARTICLE_TABLE:
    raise ValueError("Environment variable 'ARTICLE_TABLE' is not set.")

# Articles with LLM quality_score strictly below this threshold are moved to
# article_rejected instead of being inserted into article.
QUALITY_REJECT_THRESHOLD = int(os.getenv('QUALITY_REJECT_THRESHOLD', 3))


# ---------- Inference Server Configuration ----------
# Chat LLM: vLLM with Qwen3.5-9B-AWQ on GPU (OpenAI-compatible /v1 API).
# Embedding: in-process sentence-transformers on CPU (no server needed).
VLLM_BASE_URL = os.getenv('VLLM_BASE_URL', 'http://vllm-chat:8000/v1')

# ---------- Inference Logging ----------
ENABLE_INFERENCE_LOG = os.getenv('ENABLE_INFERENCE_LOG', '0') == '1'

# ---------- Prompt Version ----------
# v1: original (category only), v2: +category descriptions, v3: +difficulty/content_type/summary/few-shot
PROMPT_VERSION = "v3"


# ---------- LLM Configuration (Single Source of Truth) ----------
@dataclass(frozen=True)
class LLMConfig:
    """LLM 관련 설정 - 모든 LLM 설정은 여기서 관리"""
    model: str = "qwen3.5:9b"
    model_version: str = "QuantTrio/Qwen3.5-9B-AWQ"
    temperature: float = 0.1
    max_retries: int = 3


@dataclass(frozen=True)
class EmbeddingConfig:
    """임베딩 관련 설정"""
    model: str = "Qwen/Qwen3-Embedding-0.6B"
    size: int = 1024
    chunk_size: int = 5000


# 전역 설정 인스턴스
LLM_CONFIG = LLMConfig()
EMBEDDING_CONFIG = EmbeddingConfig()

# backwards-compat alias
LLM_MODEL = LLM_CONFIG.model
EMBEDDING_MODEL = EMBEDDING_CONFIG.model
EMBEDDING_SIZE = EMBEDDING_CONFIG.size
CHUNK_SIZE = EMBEDDING_CONFIG.chunk_size

# backwards-compat alias (consumed by main.py and embedder.py via os.getenv)
os.environ['EMBEDDING_SIZE'] = str(EMBEDDING_SIZE)
os.environ['CHUNK_SIZE'] = str(CHUNK_SIZE)

COLUMN_NAMES = [
    'article_id', 'blog_id', 'url', 'title', 'thumbnail',
    'description', 'keywords', 'category_id', 'content', 'content_length',
    'lang', 'quality_score', 'difficulty', 'content_type', 'summary', 'prompt_version',
    'created_at', 'updated_at', 'published_at'
]

UPSERT_COLUMNS = [
    'keywords', 'category_id', 'content', 'content_length', 'lang',
    'quality_score', 'difficulty', 'content_type', 'summary', 'prompt_version', 'updated_at'
]

def build_upsert_query(table_name: str, column_names: list, upsert_columns: list) -> str:
    columns_str = ", ".join(column_names)
    placeholders = ", ".join([f":{col}" for col in column_names])

    # UPDATE 구문 생성: primary_key 제외한 컬럼만 업데이트
    update_expr = ", ".join([f"{col}=VALUES({col})" for col in upsert_columns])

    return (
        f"INSERT INTO {table_name} ({columns_str}) "
        f"VALUES ({placeholders}) "
        f"ON DUPLICATE KEY UPDATE {update_expr}"
    )

def get_metadata(sql=False) -> str:
    if not sql:
        return f"{LLM_MODEL}#{EMBEDDING_MODEL}#{EMBEDDING_SIZE}#{CHUNK_SIZE}"

    return f"""
    SELECT
        CONCAT(llm_model, '#', embedding_model, '#', embedding_size, '#', chunk_size) AS model_key
    FROM llm_config_metadata;
    """

def update_model_config_query():
    """파라미터화된 쿼리로 SQL Injection 방어"""
    update_sql = """
    UPDATE llm_config_metadata
    SET
        llm_model = :llm_model,
        embedding_model = :embedding_model,
        embedding_size = :embedding_size,
        chunk_size = :chunk_size
    """

    update_params = {
        'llm_model': LLM_MODEL,
        'embedding_model': EMBEDDING_MODEL,
        'embedding_size': EMBEDDING_SIZE,
        'chunk_size': CHUNK_SIZE,
    }

    # INSERT 쿼리는 파라미터가 필요 없음 (SELECT from article)
    insert_sql = """
    INSERT IGNORE INTO article_queue (
        article_id,
        blog_id,
        url,
        title,
        thumbnail,
        description,
        category_id,
        keywords,
        content,
        content_length,
        lang,
        like_count,
        share_count,
        bookmark_count,
        created_at,
        updated_at,
        published_at
    )
    SELECT
        article_id,
        blog_id,
        url,
        title,
        thumbnail,
        description,
        category_id,
        keywords,
        NULL,
        content_length,
        lang,
        like_count,
        share_count,
        bookmark_count,
        created_at,
        updated_at,
        published_at
    FROM article
    """

    return update_sql, update_params, insert_sql

def build_get_queue_query(table_name: str) -> str:
    query = f"""
        SELECT
            article_id,
            blog_id,
            url,
            title,
            thumbnail,
            description,
            content,
            published_at,
            created_at,
            updated_at
        FROM
            {table_name}
        ORDER BY RAND()
        LIMIT {os.getenv('LIMIT', 10000)}
        """
        # LIMIT 기본값 10000: 사실상 큐 전체. 과거 Lambda 비용 제한 때문에 10이었으나
        # 지금은 홈서버 GPU(vLLM) 환경이라 동시성/비용 제약 없음. 한 사이클에 큐 전부
        # 갈아넣어 article_queue 적체를 방지. concurrency는 main.py의
        # LLM_SEMAPHORE(=16) / EMBEDDING_SEMAPHORE(=4)로 여전히 보호됨.
        # 비정상적으로 큰 큐에서 메모리/타임아웃이 문제되면 ENV LIMIT으로 일시 제한.

    # if os.getenv('ENVIRONMENT') == "dev":
    #     query = f"""
    #     SELECT
    #         article_id,
    #         blog_id,
    #         url,
    #         title,
    #         thumbnail,
    #         description,
    #         content,
    #         published_at,
    #         created_at,
    #         updated_at
    #     FROM 
    #         {table_name}
    #     WHERE
    #         article_id = 'aWSdeAb7SUtm4a0i4XKDHYERbSN'
    #     """
    return query

QUEUE_QUERY = build_get_queue_query(QUEUED_TABLE)
INSERT_QUERY = build_upsert_query(ARTICLE_TABLE, COLUMN_NAMES, UPSERT_COLUMNS)

REJECTED_COLUMNS = [
    'article_id', 'blog_id', 'url', 'title', 'thumbnail',
    'description', 'content', 'content_length', 'lang', 'published_at',
    'quality_score', 'reject_reason',
]

# INSERT IGNORE → ON DUPLICATE KEY UPDATE 로 변경.
# 같은 article_id 가 이미 article_rejected 에 있을 때 IGNORE 는 silent skip 하므로,
# 같은 글이 무한히 큐 → reject → silent skip → 큐 → ... 루프를 돌아도 row 갱신이
# 없어 모니터/로그상 흔적이 남지 않는다 (2026-05-01 ~ 05-04 60시간 사고 원인).
# rejected_at 와 reject_reason / quality_score 를 매번 갱신해 재발 시 즉시 보이게.
REJECTED_INSERT_QUERY = (
    f"INSERT INTO {REJECTED_TABLE} "
    f"({', '.join(REJECTED_COLUMNS)}) "
    f"VALUES ({', '.join([f':{c}' for c in REJECTED_COLUMNS])}) "
    f"ON DUPLICATE KEY UPDATE "
    f"rejected_at = CURRENT_TIMESTAMP, "
    f"reject_reason = VALUES(reject_reason), "
    f"quality_score = VALUES(quality_score)"
)
