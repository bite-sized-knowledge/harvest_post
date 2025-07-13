from pydantic import BaseModel, Field, validator
from enum import Enum
from typing import List


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


class TopicClassification(BaseModel):
    content: str = Field(description="Cleaned main content of the article.")
    focusing: Category = Field(description="One of the predefined categories.")
    keywords: List[str] = Field(
        min_items=3,
        max_items=3,
        description="Exactly 3 keywords."
    )
    lang: str = Field(
        pattern="^(ko|en)$",
        description="Primary language of the content."
    )

    @validator('focusing', pre=True)
    def map_string_to_enum(cls, v):
        mapping = {
            'Frontend': Category.FRONTEND,
            'Backend': Category.BACKEND,
            'Mobile Engineering': Category.MOBILE_ENGINEERING,
            'AI / ML': Category.AI_ML,
            'Database': Category.DATABASE,
            'Security / Network': Category.SECURITY_NETWORK,
            'Design': Category.DESIGN,
            'Product Manager': Category.PRODUCT_MANAGER,
            'DevOps / Infra': Category.DEVOPS_INFRA,
            'Hardware / IoT': Category.HARDWARE_IOT,
            'QA / Test Engineer': Category.QA_TEST_ENGINEER,
            'Culture': Category.CULTURE,
            'etc': Category.ETC,
        }
        if isinstance(v, Category):
            return v
        if isinstance(v, int):
            return Category(v)
        if isinstance(v, str) and v in mapping:
            return mapping[v]
        raise ValueError(f"Invalid focusing value: {v}")