"""Error classification for better debugging and retry decisions."""
from enum import Enum
from dataclasses import dataclass
from typing import Optional


class ErrorSeverity(Enum):
    """에러 심각도 - 재시도 가능 여부 결정"""
    TRANSIENT = "transient"    # 재시도 가능 (네트워크, rate limit)
    PERMANENT = "permanent"    # 재시도 불가 (잘못된 데이터)


class ErrorCategory(Enum):
    """에러 카테고리 - 디버깅용"""
    PARSING = "parsing"
    LLM = "llm"
    EMBEDDING = "embedding"
    DATABASE = "database"
    VALIDATION = "validation"
    UNKNOWN = "unknown"


@dataclass
class ProcessingError:
    """처리 중 발생한 에러 정보"""
    article_id: str
    category: ErrorCategory
    severity: ErrorSeverity
    message: str
    original_exception: Optional[Exception] = None

    @property
    def is_retryable(self) -> bool:
        return self.severity == ErrorSeverity.TRANSIENT

    def __str__(self) -> str:
        return f"[{self.category.value}/{self.severity.value}] {self.article_id}: {self.message}"


def classify_exception(e: Exception, context: str = "") -> tuple[ErrorCategory, ErrorSeverity]:
    """예외를 카테고리와 심각도로 분류"""
    error_type = type(e).__name__.lower()
    error_msg = str(e).lower()

    # 네트워크/연결 오류 -> 재시도 가능
    if any(kw in error_type for kw in ['connection', 'timeout', 'network']):
        return ErrorCategory.LLM if 'llm' in context else ErrorCategory.DATABASE, ErrorSeverity.TRANSIENT

    # Rate limit -> 재시도 가능
    if 'ratelimit' in error_type or 'rate' in error_msg or '429' in error_msg:
        return ErrorCategory.LLM, ErrorSeverity.TRANSIENT

    # 파싱/검증 에러 -> 영구적 (데이터 문제)
    if any(kw in error_type for kw in ['parse', 'json', 'validation', 'value']):
        return ErrorCategory.PARSING, ErrorSeverity.PERMANENT

    # 인증 에러 -> 영구적
    if any(kw in error_type for kw in ['auth', 'credential', 'permission']):
        return ErrorCategory.UNKNOWN, ErrorSeverity.PERMANENT

    # 기본: 재시도 허용
    return ErrorCategory.UNKNOWN, ErrorSeverity.TRANSIENT
