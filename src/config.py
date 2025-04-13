import os
from enum import Enum

# ---------- Table Configuration ----------
ARTICLE_TABLE = os.getenv('ARTICLE_TABLE')
if not ARTICLE_TABLE:
    raise ValueError("Environment variable 'ARTICLE_TABLE' is not set.")

COLUMN_NAMES = [
    'article_id', 'blog_id', 'url', 'title', 'thumbnail',
    'description', 'keywords', 'category_id', 'content', 'content_length',
    'lang','created_at', 'updated_at', 'published_at'
]

def build_insert_query(table_name: str, column_names: list) -> str:
    columns_str = ", ".join(column_names)
    placeholders = ", ".join(["%s"] * len(column_names))
    return f"INSERT IGNORE INTO {table_name} ({columns_str}) VALUES ({placeholders})"

INSERT_QUERY = build_insert_query(ARTICLE_TABLE, COLUMN_NAMES)

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