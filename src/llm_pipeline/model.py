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
            temperature=1  # GPT-5 models only support default temperature (1)
        )

    def _safe_parser(self):
        def _inner(msg):
            content = msg.content if hasattr(msg, "content") else msg
            return self._try_parse(content)
        return RunnableLambda(_inner)

    def extract_json(self, text: str) -> dict:
        """텍스트에서 실제 데이터 JSON 추출 (스키마 정의 제외)"""
        # 모든 JSON 객체 후보 찾기
        pattern = r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}'
        matches = re.findall(pattern, text, re.DOTALL)

        if not matches:
            # fallback: greedy 매칭
            match = re.search(r'\{.*\}', text, re.DOTALL)
            if not match:
                raise ValueError("No JSON object found in text.")
            matches = [match.group()]

        for json_str in matches:
            try:
                parsed = json.loads(json_str)
                # 스키마 정의는 건너뜀 ($defs, properties, type 등이 있으면 스키마)
                if isinstance(parsed, dict):
                    if '$defs' in parsed or 'properties' in parsed or parsed.get('type') == 'object':
                        continue
                    # 필수 필드가 있는지 확인
                    if 'content' in parsed or 'focusing' in parsed or 'keywords' in parsed:
                        return parsed
            except json.JSONDecodeError:
                continue

        # 마지막 시도: trailing comma 수정 후 재시도
        for json_str in matches:
            try:
                fixed = re.sub(r',\s*}', '}', json_str)
                fixed = re.sub(r',\s*]', ']', fixed)
                parsed = json.loads(fixed)
                if isinstance(parsed, dict) and '$defs' not in parsed:
                    return parsed
            except json.JSONDecodeError:
                continue

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