import re

from pydantic import BaseModel, Field, validator
from enum import Enum
from typing import List, Optional

MAX_KEYWORDS = 3

# Compiled regex for summary cleaning (used in validator + backfill scripts)
_CJK_RE = re.compile(r'[\u4e00-\u9fff\u3400-\u4dbf\U00020000-\U0002a6df]')
_MULTI_WS_RE = re.compile(r'\s{2,}')
_EOJEOL_RE = re.compile(r'[다요함됨임]\.*')


def clean_summary(text: str) -> str:
    """Summary 후처리: CJK 제거, 80자 truncate with sentence ending."""
    cleaned = _CJK_RE.sub('', text.strip())
    cleaned = _MULTI_WS_RE.sub(' ', cleaned).strip()
    if len(cleaned) <= 80:
        return cleaned
    truncated = cleaned[:80]
    last_end = -1
    for m in _EOJEOL_RE.finditer(truncated):
        last_end = m.end()
    if last_end > 20:
        return truncated[:last_end]
    last_space = truncated.rfind(' ')
    if last_space > 20:
        return truncated[:last_space] + '.'
    return truncated + '.'


class ContentType(str, Enum):
    TUTORIAL = "tutorial"
    DEEP_DIVE = "deep-dive"
    POSTMORTEM = "postmortem"
    CASE_STUDY = "case-study"
    ANNOUNCEMENT = "announcement"
    OPINION = "opinion"


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
        max_items=MAX_KEYWORDS,
        description=f"Up to {MAX_KEYWORDS} keywords. Fewer values are backfilled with 'N/A'.",
    )
    quality_score: int = Field(
        ge=1,
        le=5,
        description=(
            "Quality rating from 1 (unusable/promo/empty) to 5 (deep technical post). "
            "Articles scoring below the rejection threshold are moved to article_rejected."
        ),
    )
    difficulty: int = Field(
        ge=1,
        le=3,
        description="1=beginner, 2=intermediate, 3=advanced",
    )
    content_type: ContentType = Field(
        description="Article format type",
    )
    summary: str = Field(
        max_length=100,
        description="80자 이내 한국어 한 문장 요약. 종결어미로 끝낼 것.",
    )

    @validator('keywords', pre=True)
    def pad_keywords(cls, v):
        """Normalize keyword list: drop empties, dedupe, pad to exactly MAX_KEYWORDS."""
        if v is None:
            return ["N/A"] * MAX_KEYWORDS
        if isinstance(v, str):
            v = [v]
        if not isinstance(v, list):
            return ["N/A"] * MAX_KEYWORDS
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
            if len(cleaned) == MAX_KEYWORDS:
                break
        while len(cleaned) < MAX_KEYWORDS:
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

    @validator('difficulty', pre=True)
    def coerce_difficulty(cls, v):
        if isinstance(v, int):
            return max(1, min(3, v))
        if isinstance(v, float):
            return max(1, min(3, int(round(v))))
        if isinstance(v, str) and v.strip().isdigit():
            return max(1, min(3, int(v.strip())))
        return 2  # default to intermediate

    @validator('content_type', pre=True)
    def coerce_content_type(cls, v):
        if isinstance(v, ContentType):
            return v
        if isinstance(v, str):
            v_lower = v.strip().lower().replace(" ", "-").replace("_", "-")
            for ct in ContentType:
                if ct.value == v_lower:
                    return ct
            # aliases only (exact enum values already handled above)
            mapping = {
                "how-to": ContentType.TUTORIAL,
                "guide": ContentType.TUTORIAL,
                "deepdive": ContentType.DEEP_DIVE,
                "analysis": ContentType.DEEP_DIVE,
                "post-mortem": ContentType.POSTMORTEM,
                "incident": ContentType.POSTMORTEM,
                "casestudy": ContentType.CASE_STUDY,
                "release": ContentType.ANNOUNCEMENT,
                "essay": ContentType.OPINION,
            }
            if v_lower in mapping:
                return mapping[v_lower]
        return ContentType.DEEP_DIVE  # default

    @validator('summary', pre=True)
    def coerce_summary(cls, v):
        if not v or not isinstance(v, str):
            return "N/A"
        return clean_summary(v)
