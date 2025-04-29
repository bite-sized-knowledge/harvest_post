import os
from enum import Enum

# ---------- Table Configuration ----------
ARTICLE_TABLE = os.getenv('ARTICLE_TABLE')
QUEUED_TABLE = "article_queue"
if not ARTICLE_TABLE:
    raise ValueError("Environment variable 'ARTICLE_TABLE' is not set.")

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


def build_get_queue_query(table_name: str) -> str:
    query = f"""
        SELECT
            article_id,
            blog_id,
            url,
            title,
            thumbnail,
            description,
            published_at,
            created_at,
            updated_at
        FROM 
            {table_name}"""
    if os.getenv('ENVIRONMENT') == 'dev':
        LIMIT = os.getenv('LIMIT', 10)
        return f"{query} LIMIT {LIMIT};" 

    return query

QUEUE_QUERY = build_get_queue_query(QUEUED_TABLE)
INSERT_QUERY = build_upsert_query(ARTICLE_TABLE, COLUMN_NAMES, UPSERT_COLUMNS)

# ---------- Category Mapping ----------
class Category(Enum):
    FRONTEND = 1
    BACKEND = 2
    MOBILE_ENGINEERING = 3
    AI_ML = 4
    DATABASE = 5
    SECURITY_NETWORK = 6
    DESIGN = 7
    PRODUCT_MANAGER = 8
    DEVOPS_INFRA = 9
    HARDWARE_IOT = 10
    QA_TEST_ENGINEER = 11
    CULTURE = 12
    ETC = 13

CATEGORY_DICT = {
    'Frontend': Category.FRONTEND.value,
    'Backend': Category.BACKEND.value,
    'Mobile Engineering': Category.MOBILE_ENGINEERING.value,
    'AI / ML': Category.AI_ML.value,
    'Database': Category.DATABASE.value,
    'Security / Network': Category.SECURITY_NETWORK.value,
    'Design': Category.DESIGN.value,
    'Product Manager': Category.PRODUCT_MANAGER.value,
    'DevOps / Infra': Category.DEVOPS_INFRA.value,
    'Hardware / IoT': Category.HARDWARE_IOT.value,
    'QA / Test Engineer': Category.QA_TEST_ENGINEER.value,
    'Culture': Category.CULTURE.value,
    'etc': Category.ETC.value
}