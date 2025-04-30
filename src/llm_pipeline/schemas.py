from pydantic import BaseModel, Field, validator, model_validator
from enum import Enum
from typing import List, Optional


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
    NA = 14


class TopicClassification(BaseModel):
    content: Optional[str] = Field(description="Preprocessed content of the text")
    focusing: Category = Field(description="Most relevant topic category (Enum)")
    keywords: Optional[List[str]] = Field(max_length=3, description="Three relative keywords extracted from the text")
    content_length: int = Field(description="Content length of the text excluding metadata")
    lang: str = Field(description="Language of the text")

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
            'N/A': Category.NA
        }
        if isinstance(v, Category):
            return v
        if isinstance(v, int):
            return Category(v)
        if v in mapping:
            return mapping[v]
        raise ValueError(f"Invalid focusing value: {v}")

    @model_validator(mode="after")
    def enforce_rules(cls, values):
        if values.content_length <= 50 or values.focusing == Category.NA:
            values.content = None
            values.keywords = None
            values.focusing = Category.NA
        return values