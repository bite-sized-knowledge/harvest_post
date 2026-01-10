"""Structured logging for Lambda/CloudWatch compatibility."""
import json
import sys
from datetime import datetime
from typing import Any, Optional


class StructuredLogger:
    """JSON 구조화 로거 - CloudWatch 친화적"""

    def __init__(self, name: str = "harvest_post"):
        self.name = name

    def _log(self, level: str, message: str, **extra: Any) -> None:
        log_record = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "level": level,
            "logger": self.name,
            "message": message,
        }

        if extra:
            log_record["extra"] = extra

        print(json.dumps(log_record, ensure_ascii=False, default=str))

    def info(self, message: str, **extra: Any) -> None:
        self._log("INFO", message, **extra)

    def error(self, message: str, **extra: Any) -> None:
        self._log("ERROR", message, **extra)

    def warning(self, message: str, **extra: Any) -> None:
        self._log("WARNING", message, **extra)

    def debug(self, message: str, **extra: Any) -> None:
        self._log("DEBUG", message, **extra)


# 전역 로거 인스턴스
logger = StructuredLogger()
