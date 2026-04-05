import yaml
import re
import json
import asyncio
import sys
import os

# config 모듈 경로 추가
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import LLM_CONFIG, VLLM_BASE_URL

from langchain_openai import ChatOpenAI
from langchain_core.runnables import RunnableLambda
from .prompt_generator import PromptGenerator
from .schemas import TopicClassification


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

    async def _invoke_with_retry(self, chain, input_data: dict) -> TopicClassification:
        """Exponential backoff으로 재시도 (실패 시 None 반환 → queue에 유지)"""
        last_exception = None

        for attempt in range(self.MAX_RETRIES):
            try:
                return await chain.ainvoke(input_data)
            except Exception as e:
                last_exception = e
                delay = min(
                    self.RETRY_DELAY_BASE * (2 ** attempt),
                    self.RETRY_DELAY_MAX
                )
                print(f"[RETRY] Attempt {attempt + 1}/{self.MAX_RETRIES} failed: {e}")

                if attempt < self.MAX_RETRIES - 1:
                    print(f"[RETRY] Waiting {delay:.1f}s before retry...")
                    await asyncio.sleep(delay)

        print(f"[ERROR] All {self.MAX_RETRIES} attempts failed. Last error: {last_exception}")
        return None

    async def predict(self, text: str, show_prompt: bool = False, show_output: bool = True) -> TopicClassification:
        input_data = {"content": text}

        if show_prompt:
            print("[DEBUG] Main Prompt Preview:\n")
            print(self.prompt_generator.preview_prompt(text, level="main"))
            print("=" * 50)

        chain = self.prompt_generator.prompt | self.model | self._safe_parser()

        result = await self._invoke_with_retry(chain, input_data)

        if result:
            if show_output:
                print(result)
            return result

        print("[ERROR] LLM chain failed after retries")
        return None
