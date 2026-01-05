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

    def _find_json_bounds(self, text: str) -> tuple:
        """중첩된 괄호를 고려하여 첫 번째 완전한 JSON 객체의 시작/끝 인덱스 반환"""
        start = text.find('{')
        if start == -1:
            return -1, -1

        depth = 0
        in_string = False
        escape_next = False

        for i, char in enumerate(text[start:], start):
            if escape_next:
                escape_next = False
                continue

            if char == '\\' and in_string:
                escape_next = True
                continue

            if char == '"' and not escape_next:
                in_string = not in_string
                continue

            if in_string:
                continue

            if char == '{':
                depth += 1
            elif char == '}':
                depth -= 1
                if depth == 0:
                    return start, i + 1

        return -1, -1

    def extract_json(self, text: str) -> dict:
        """텍스트에서 첫 번째 완전한 JSON 객체 추출"""
        start, end = self._find_json_bounds(text)
        if start == -1:
            raise ValueError("No JSON object found in text.")

        json_str = text[start:end]

        try:
            return json.loads(json_str)
        except json.JSONDecodeError:
            json_str = re.sub(r',\s*}', '}', json_str)
            json_str = re.sub(r',\s*]', ']', json_str)
            return json.loads(json_str)

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

    def predict(self, text: str, show_prompt: bool = False, show_output: bool = True) -> TopicClassification:
        input_data = {"content": text}

        if show_prompt:
            print("[DEBUG] Main Prompt Preview:\n")
            print(self.prompt_generator.preview_prompt(text, level="main"))
            print("=" * 50)

        name, chain = ["OpenAI", self.prompt_generator.prompt | self.model | self._safe_parser()]

        try:
            print(f"[INFO] Trying {name} chain...")
            ret = chain.invoke(input_data)
            if show_output:
                print(ret)
            return ret
        except Exception as e:
            print(f"[WARNING] {name} chain failed: {e}")

        print("[ERROR] All LLM Chains Failed")
        return None