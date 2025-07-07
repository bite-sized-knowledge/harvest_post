import yaml
from langchain_openai import ChatOpenAI
from langchain_aws import ChatBedrockConverse
from langchain_core.runnables import Runnable
from .prompt_generator import PromptGenerator
from .schemas import TopicClassification

class LangChainModel:
    def __init__(self):

        # Read Prompt
        with open('prompt.yml') as f:
            self.prompt_data = yaml.safe_load(f)

        self.prompt_generator = PromptGenerator(self.prompt_data)

        self.model = ChatBedrockConverse(
            model_id="apac.amazon.nova-lite-v1:0",
            region_name="ap-northeast-2",
            temperature=1
        )
        self.back_up = ChatOpenAI(
            model_name=self.prompt_data['model'],
            temperature=1
        )

        self.restrict_back_up = ChatOpenAI(
            model_name=self.prompt_data['model'],
            temperature=0.0
        )

    def predict(self, text: str) -> TopicClassification:
        input_data = {
            "question": self.prompt_generator.question,
            "content": text
        }

        chains = [
            ("AWS Bedrock", self.prompt_generator.prompt | self.model | self.prompt_generator.parser),
            ("OpenAI", self.prompt_generator.retry | self.back_up | self.prompt_generator.parser),
            ("OpenAI Fallback", self.prompt_generator.restrict | self.restrict_back_up | self.prompt_generator.parser)
        ]

        for name, chain in chains:
            try:
                print(f"[INFO] Trying {name} chain...")
                return chain.invoke(input_data)
            except Exception as e:
                print(f"[WARNING] {name} chain failed: {e}")

        raise RuntimeError("All chains failed")
