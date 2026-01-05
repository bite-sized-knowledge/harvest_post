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

        # Load prompt config
        with open('prompt.yml') as f:
            self.prompt_data = yaml.safe_load(f)

        self.prompt_generator = PromptGenerator(self.prompt_data)

        # LLM clients
        self.model = ChatOpenAI(
            model_name=OPENAI_LLM_MODEL,
            temperature=1  # GPT-5 models only support default temperature (1)
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