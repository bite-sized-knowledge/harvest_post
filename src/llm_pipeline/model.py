import yaml
import re
import json
import asyncio
import time
import sys
import os
from dataclasses import dataclass, asdict
from typing import Optional

# config 모듈 경로 추가
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import LLM_CONFIG, VLLM_BASE_URL

from langchain_openai import ChatOpenAI
from langchain_core.runnables import RunnableLambda
from .prompt_generator import PromptGenerator
from .schemas import TopicClassification


@dataclass
class InferenceResult:
    """LLM 추론 결과 + 메타데이터"""
    parsed: Optional[TopicClassification]
    raw_output: Optional[str]
    latency_ms: int
    input_tokens: Optional[int]
    output_tokens: Optional[int]
    retry_count: int
    parse_success: bool


class LangChainModel:
    # Retry configuration from LLM_CONFIG
    MAX_RETRIES = LLM_CONFIG.max_retries
    RETRY_DELAY_BASE = 1.0  # seconds
    RETRY_DELAY_MAX = 10.0  # seconds

    def __init__(self):
        with open('prompt.yml') as f:
            self.prompt_data = yaml.safe_load(f)

        self.prompt_generator = PromptGenerator(self.prompt_data)

        # vLLM OpenAI-compatible API 사용
        self.model = ChatOpenAI(
            model=LLM_CONFIG.model,
            base_url=VLLM_BASE_URL,
            api_key="not-needed",
            temperature=LLM_CONFIG.temperature,
        )

    def _safe_parser(self):
        def _inner(msg):
            content = msg.content if hasattr(msg, "content") else msg
            return self._try_parse(content)
        return RunnableLambda(_inner)

    def _is_schema(self, obj: dict) -> bool:
        """JSON 객체가 스키마 정의인지 확인"""
        schema_keys = {'$defs', 'properties', 'enum', 'title', 'type', 'required', '$ref'}
        if not isinstance(obj, dict):
            return False
        has_schema_keys = bool(set(obj.keys()) & schema_keys)
        has_data_keys = bool({'content', 'focusing', 'keywords', 'lang'} & set(obj.keys()))
        for v in obj.values():
            if isinstance(v, dict) and ('enum' in v or 'type' in v or '$ref' in v):
                return True
        return has_schema_keys and not has_data_keys

    def extract_json(self, text: str) -> dict:
        """텍스트에서 실제 데이터 JSON 추출 (스키마 정의 제외)"""
        match = re.search(r'\{.*\}', text, re.DOTALL)
        if not match:
            raise ValueError("No JSON object found in text.")

        json_str = match.group()

        try:
            parsed = json.loads(json_str)
            if not self._is_schema(parsed):
                return parsed
        except json.JSONDecodeError:
            pass

        data_pattern = r'\{[^{}]*"content"\s*:[^{}]*"focusing"\s*:[^{}]*\}'
        data_match = re.search(data_pattern, text, re.DOTALL)
        if data_match:
            try:
                return json.loads(data_match.group())
            except json.JSONDecodeError:
                pass

        try:
            fixed = re.sub(r',\s*}', '}', json_str)
            fixed = re.sub(r',\s*]', ']', fixed)
            parsed = json.loads(fixed)
            if not self._is_schema(parsed):
                return parsed
        except json.JSONDecodeError:
            pass

        raise ValueError("No valid data JSON found (only schema definitions)")

    def _try_parse(self, output: str) -> TopicClassification:
        try:
            parsed_dict = self.extract_json(output)
            return self.prompt_generator.parser.pydantic_object(**parsed_dict)
        except json.JSONDecodeError as e:
            print(f"[PARSER ERROR] JSON decode failed: {e}")
            raise
        except Exception as e:
            print(f"[PARSER ERROR] {e}")
            if hasattr(e, "errors"):
                for err in e.errors():
                    print(f"  Field error: {err}")
            raise

    async def _invoke_with_retry(self, input_data: dict) -> InferenceResult:
        """Exponential backoff으로 재시도. raw_output, tokens, latency 등 메타데이터 포함 반환."""
        last_exception = None
        total_retry_count = 0
        # 전체 시간 측정 (retry 포함)
        t_start = time.perf_counter()

        # prompt chain (parser 제외)
        prompt_chain = self.prompt_generator.prompt | self.model

        raw_output = None
        input_tokens = None
        output_tokens = None

        for attempt in range(self.MAX_RETRIES):
            try:
                ai_message = await prompt_chain.ainvoke(input_data)

                # raw output 캡처
                raw_output = ai_message.content if hasattr(ai_message, "content") else str(ai_message)

                # token usage 캡처 (vLLM OpenAI-compatible response)
                usage = None
                if hasattr(ai_message, "response_metadata"):
                    usage = ai_message.response_metadata.get("token_usage")
                if usage:
                    input_tokens = usage.get("prompt_tokens")
                    output_tokens = usage.get("completion_tokens")

                # 파싱
                parsed = self._try_parse(raw_output)

                latency_ms = int((time.perf_counter() - t_start) * 1000)
                return InferenceResult(
                    parsed=parsed,
                    raw_output=raw_output,
                    latency_ms=latency_ms,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    retry_count=total_retry_count,
                    parse_success=True,
                )

            except Exception as e:
                last_exception = e
                total_retry_count += 1
                delay = min(
                    self.RETRY_DELAY_BASE * (2 ** attempt),
                    self.RETRY_DELAY_MAX
                )
                print(f"[RETRY] Attempt {attempt + 1}/{self.MAX_RETRIES} failed: {e}")

                if attempt < self.MAX_RETRIES - 1:
                    print(f"[RETRY] Waiting {delay:.1f}s before retry...")
                    await asyncio.sleep(delay)

        # 모든 재시도 실패
        latency_ms = int((time.perf_counter() - t_start) * 1000)
        print(f"[ERROR] All {self.MAX_RETRIES} attempts failed. Last error: {last_exception}")
        return InferenceResult(
            parsed=None,
            raw_output=raw_output,
            latency_ms=latency_ms,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            retry_count=total_retry_count,
            parse_success=False,
        )

    async def predict(self, text: str, show_prompt: bool = False, show_output: bool = True) -> InferenceResult:
        input_data = {"content": text}

        if show_prompt:
            print("[DEBUG] Main Prompt Preview:\n")
            print(self.prompt_generator.preview_prompt(text, level="main"))
            print("=" * 50)

        result = await self._invoke_with_retry(input_data)

        if result.parsed and show_output:
            print(result.parsed)

        return result
