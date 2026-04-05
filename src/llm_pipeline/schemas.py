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
    focusing: Category = Field(description="One of the predefined categories.")
    # NB: min_items=1 (not 3) is intentional — small LLMs sometimes return
    # an empty or 1-2 item list. The `pad_keywords` validator backfills
    # "N/A" up to 3 so downstream storage and the article schema stay
    # consistent. This replaces the previous behaviour of hard-failing
    # validation and leaving the row stuck in article_queue forever.
    keywords: List[str] = Field(
        min_items=1,
        max_items=3,
        description="Up to 3 keywords. Fewer values are backfilled with 'N/A'.",
    )
    quality_score: int = Field(
        ge=1,
        le=5,
        description=(
            "Quality rating from 1 (unusable/promo/empty) to 5 (deep technical post). "
            "Articles scoring below the rejection threshold are moved to article_rejected."
        ),
    )

    @validator('keywords', pre=True)
    def pad_keywords(cls, v):
        """Normalize keyword list: drop empties, dedupe, pad to exactly 3."""
        if v is None:
            return ["N/A", "N/A", "N/A"]
        if isinstance(v, str):
            v = [v]
        if not isinstance(v, list):
            return ["N/A", "N/A", "N/A"]
        cleaned = []
        seen = set()
        for item in v:
            if not isinstance(item, str):
                continue
            stripped = item.strip()
            if not stripped or stripped in seen:
                continue
            seen.add(stripped)
            cleaned.append(stripped)
            if len(cleaned) == 3:
                break
        while len(cleaned) < 3:
            cleaned.append("N/A")
        return cleaned

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

    @validator('quality_score', pre=True)
    def coerce_quality_score(cls, v):
        if isinstance(v, bool):
            raise ValueError("quality_score must be an integer, got bool")
        if isinstance(v, int):
            return v
        if isinstance(v, float):
            return int(round(v))
        if isinstance(v, str):
            stripped = v.strip()
            if stripped.isdigit():
                return int(stripped)
        raise ValueError(f"Invalid quality_score: {v!r}")
