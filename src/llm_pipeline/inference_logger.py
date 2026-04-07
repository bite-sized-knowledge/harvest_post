import json
import asyncio
from typing import Optional

from sqlalchemy import text

from .model import InferenceResult


INSERT_LOG_SQL = text("""
    INSERT INTO llm_inference_log (
        article_id, model_key, model_version, temperature,
        input_text, raw_output, parsed_output, parse_success,
        category_id, keywords, quality_score,
        latency_ms, input_tokens, output_tokens, retry_count,
        outcome, is_shadow, eval_run_id
    ) VALUES (
        :article_id, :model_key, :model_version, :temperature,
        :input_text, :raw_output, :parsed_output, :parse_success,
        :category_id, :keywords, :quality_score,
        :latency_ms, :input_tokens, :output_tokens, :retry_count,
        :outcome, :is_shadow, :eval_run_id
    )
""")


class InferenceLogger:
    """LLM 추론 로그를 버퍼링했다가 배치 INSERT하는 로거.

    사용법:
        logger = InferenceLogger(model_key="qwen3.5:9b", ...)
        logger.log(article_id, input_text, result, outcome)
        ...
        await logger.flush(conn)  # 배치 끝에 한번 호출
    """

    def __init__(
        self,
        model_key: str,
        model_version: Optional[str] = None,
        temperature: float = 0.1,
        is_shadow: bool = False,
        eval_run_id: Optional[str] = None,
    ):
        self.model_key = model_key
        self.model_version = model_version
        self.temperature = temperature
        self.is_shadow = is_shadow
        self.eval_run_id = eval_run_id
        self._buffer: list[dict] = []

    def log(
        self,
        article_id: str,
        input_text: str,
        result: InferenceResult,
        outcome: str,
    ):
        parsed_output = None
        category_id = None
        keywords = None
        quality_score = None

        if result.parsed:
            parsed_output = json.dumps({
                "focusing": result.parsed.focusing.name,
                "keywords": result.parsed.keywords,
                "quality_score": result.parsed.quality_score,
            })
            category_id = result.parsed.focusing.value
            keywords = "\t".join(result.parsed.keywords)
            quality_score = result.parsed.quality_score

        self._buffer.append({
            "article_id": article_id,
            "model_key": self.model_key,
            "model_version": self.model_version,
            "temperature": self.temperature,
            "input_text": input_text,
            "raw_output": result.raw_output,
            "parsed_output": parsed_output,
            "parse_success": result.parse_success,
            "category_id": category_id,
            "keywords": keywords,
            "quality_score": quality_score,
            "latency_ms": result.latency_ms,
            "input_tokens": result.input_tokens,
            "output_tokens": result.output_tokens,
            "retry_count": result.retry_count,
            "outcome": outcome,
            "is_shadow": self.is_shadow,
            "eval_run_id": self.eval_run_id,
        })

    async def flush(self, conn):
        """버퍼의 모든 로그를 DB에 배치 INSERT."""
        if not self._buffer:
            return 0

        count = len(self._buffer)
        try:
            await asyncio.to_thread(conn.session_execute, INSERT_LOG_SQL, self._buffer)
            print(f"[INFERENCE_LOG] Flushed {count} inference logs")
        except Exception as e:
            print(f"[INFERENCE_LOG] Failed to flush {count} logs: {e}")
        finally:
            self._buffer.clear()
        return count
