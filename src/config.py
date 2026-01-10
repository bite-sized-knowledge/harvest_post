import os

# ---------- Table Configuration ----------
ARTICLE_TABLE = os.getenv('ARTICLE_TABLE')
QUEUED_TABLE = "article_queue"
if not ARTICLE_TABLE:
    raise ValueError("Environment variable 'ARTICLE_TABLE' is not set.")


# ---------- LLM Metadata Configuration ----------

LLM_MODEL = "gpt-5-nano"
EMBEDDING_MODEL = "titan-embed-text-v2"
EMBEDDING_SIZE = 512
CHUNK_SIZE=5000

os.environ['EMBEDDING_SIZE'] = str(EMBEDDING_SIZE)
os.environ['CHUNK_SIZE'] = str(CHUNK_SIZE)

COLUMN_NAMES = [
    'article_id', 'blog_id', 'url', 'title', 'thumbnail',
    'description', 'keywords', 'category_id', 'content', 'content_length',
    'lang','created_at', 'updated_at', 'published_at'
]

UPSERT_COLUMNS = [
    'keywords', 'category_id', 'content', 'content_length', 'lang', 'updated_at'
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
        LIMIT {os.getenv('LIMIT', 10)} 
        """

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
