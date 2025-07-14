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
        OPENAI_LLM_MODEL = "gpt-4.1-nano-2025-04-14"

        # Load prompt config
        with open('prompt.yml') as f:
            self.prompt_data = yaml.safe_load(f)

        self.prompt_generator = PromptGenerator(self.prompt_data)

        # LLM clients
        self.model = ChatOpenAI(
                model_name=OPENAI_LLM_MODEL,
            temperature=0
        )

        self.back_up = ChatBedrockConverse(
            model_id=AWS_LLM_MODEL,
            region_name="ap-northeast-2",
            temperature=0,
        )

    def _safe_parser(self):
        def _inner(msg):
            content = msg.content if hasattr(msg, "content") else msg
            # print("[DEBUG] LLM raw output:\n", content)
            return self._try_parse(content)
        return RunnableLambda(_inner)

    def extract_json(self, text: str) -> dict:
        try:
            match = re.search(r"\{.*\}", text, re.DOTALL)
            if not match:
                raise ValueError("No JSON object found in text.")
            json_str = match.group()
            # print("[DEBUG] Extracted JSON string:\n", json_str)
            return json.loads(json_str)
        except json.JSONDecodeError as je:
            print(f"[extract_json] JSON decode error: {je}")
            raise
        except Exception as e:
            print(f"[extract_json] Unexpected error: {e}")
            raise

    def _try_parse(self, output: str) -> TopicClassification:
        try:
            parsed_dict = self.extract_json(output)
            return self.prompt_generator.parser.pydantic_object(**parsed_dict)
        except Exception as e:
            print("[PARSER ERROR]", e)
            if hasattr(e, "errors"):
                for err in e.errors():
                    print("Field error:", err)
            raise

    def predict(self, text: str, show_prompt: bool = True) -> TopicClassification:
        input_data = {"content": text}

        if show_prompt:
            print("[DEBUG] Main Prompt Preview:\n")
            print(self.prompt_generator.preview_prompt(text, level="main"))
            print("=" * 50)

        chains = [
            ("OpenAI", self.prompt_generator.prompt | self.model | self._safe_parser()),
            ("AWS Bedrock", self.prompt_generator.retry | self.back_up | self._safe_parser()),
        ]

        for name, chain in chains:
            try:
                print(f"[INFO] Trying {name} chain...")
                return chain.invoke(input_data)
            except Exception as e:
                print(f"[WARNING] {name} chain failed: {e}")

        raise RuntimeError("All chains failed")