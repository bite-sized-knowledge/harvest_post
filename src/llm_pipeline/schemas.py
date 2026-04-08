from pydantic import BaseModel, Field, validator
from enum import Enum
from typing import List, Optional

MAX_KEYWORDS = 3


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
            # fuzzy match
            mapping = {
                "tutorial": ContentType.TUTORIAL,
                "how-to": ContentType.TUTORIAL,
                "guide": ContentType.TUTORIAL,
                "deep-dive": ContentType.DEEP_DIVE,
                "deepdive": ContentType.DEEP_DIVE,
                "analysis": ContentType.DEEP_DIVE,
                "postmortem": ContentType.POSTMORTEM,
                "post-mortem": ContentType.POSTMORTEM,
                "incident": ContentType.POSTMORTEM,
                "case-study": ContentType.CASE_STUDY,
                "casestudy": ContentType.CASE_STUDY,
                "announcement": ContentType.ANNOUNCEMENT,
                "release": ContentType.ANNOUNCEMENT,
                "opinion": ContentType.OPINION,
                "essay": ContentType.OPINION,
            }
            if v_lower in mapping:
                return mapping[v_lower]
        return ContentType.DEEP_DIVE  # default

    @validator('summary', pre=True)
    def coerce_summary(cls, v):
        if not v or not isinstance(v, str):
            return "N/A"
        import re
        cleaned = v.strip()
        # CJK Unified Ideographs (한자/중국어) 제거
        cleaned = re.sub(r'[\u4e00-\u9fff\u3400-\u4dbf\U00020000-\U0002a6df]', '', cleaned)
        cleaned = re.sub(r'\s{2,}', ' ', cleaned).strip()
        if len(cleaned) <= 80:
            return cleaned
        # 80자 이내에서 마지막 온전한 문장(종결어미/마침표)으로 자르기
        truncated = cleaned[:80]
        # 종결 위치 찾기: 다/다./합니다/한다/이다/있다/했다/된다/etc + optional period
        last_end = -1
        for m in re.finditer(r'[다요함됨임]\.*', truncated):
            last_end = m.end()
        if last_end > 20:
            return truncated[:last_end]
        # 종결어미 못 찾으면 마지막 공백에서 자르고 마침표 추가
        last_space = truncated.rfind(' ')
        if last_space > 20:
            return truncated[:last_space] + '.'
        return truncated + '.'
