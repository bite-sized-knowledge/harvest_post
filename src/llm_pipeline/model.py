import yaml
import re
import json
from langchain_openai import ChatOpenAI
from langchain_aws import ChatBedrockConverse
from langchain_core.runnables import RunnableLambda
from .prompt_generator import PromptGenerator
from .schemas import TopicClassification


class LangChainModel:
    def __init__(self):
        AWS_LLM_MODEL = "apac.amazon.nova-micro-v1:0"
        OPENAI_LLM_MODEL = "gpt-5-nano"

        with open('prompt.yml') as f:
            self.prompt_data = yaml.safe_load(f)

        self.prompt_generator = PromptGenerator(self.prompt_data)

        self.model = ChatOpenAI(
            model_name=OPENAI_LLM_MODEL,
            temperature=1
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
        # 스키마 키가 있고 실제 데이터 키가 없으면 스키마
        has_schema_keys = bool(set(obj.keys()) & schema_keys)
        has_data_keys = bool({'content', 'focusing', 'keywords', 'lang'} & set(obj.keys()))
        # 중첩된 스키마 확인 (Category 등)
        for v in obj.values():
            if isinstance(v, dict) and ('enum' in v or 'type' in v or '$ref' in v):
                return True
        return has_schema_keys and not has_data_keys

    def extract_json(self, text: str) -> dict:
        """텍스트에서 실제 데이터 JSON 추출 (스키마 정의 제외)"""
        # greedy 매칭으로 가장 큰 JSON 찾기
        match = re.search(r'\{.*\}', text, re.DOTALL)
        if not match:
            raise ValueError("No JSON object found in text.")

        json_str = match.group()

        # 여러 JSON이 연결된 경우 분리 시도
        try:
            parsed = json.loads(json_str)
            if not self._is_schema(parsed):
                return parsed
        except json.JSONDecodeError:
            pass

        # 스키마가 포함된 경우, 텍스트에서 실제 데이터 JSON 패턴 찾기
        # content, focusing, keywords, lang 키를 가진 JSON 찾기
        data_pattern = r'\{[^{}]*"content"\s*:[^{}]*"focusing"\s*:[^{}]*\}'
        data_match = re.search(data_pattern, text, re.DOTALL)
        if data_match:
            try:
                return json.loads(data_match.group())
            except json.JSONDecodeError:
                pass

        # 마지막 시도: trailing comma 수정
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

    async def predict(self, text: str, show_prompt: bool = False, show_output: bool = True) -> TopicClassification:
        input_data = {"content": text}

        if show_prompt:
            print("[DEBUG] Main Prompt Preview:\n")
            print(self.prompt_generator.preview_prompt(text, level="main"))
            print("=" * 50)

        chain = self.prompt_generator.prompt | self.model | self._safe_parser()

        try:
            print("[INFO] Trying OpenAI chain...")
            ret = await chain.ainvoke(input_data)
            if show_output:
                print(ret)
            return ret
        except Exception as e:
            print(f"[WARNING] OpenAI chain failed: {e}")

        print("[ERROR] All LLM Chains Failed")
        return None