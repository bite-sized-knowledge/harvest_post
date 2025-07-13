from langchain_core.prompts import PromptTemplate
from langchain_core.output_parsers import PydanticOutputParser
from .schemas import TopicClassification

TEMPLATE = "{system_message}\n\n{instruction}\n\nContent:\n{content}"

class PromptGenerator:
    def __init__(self, prompt_data: dict):
        self.prompt_data = prompt_data
        self.parser = PydanticOutputParser(pydantic_object=TopicClassification)
        self.format_instructions = self.parser.get_format_instructions().replace("```json", "").replace("```", "")
        self.instruction_text = self._build_instruction_text()
        self.system_message = prompt_data.get("system_message", "")

        self.prompt = self._build_prompt_template()
        self.retry = self._build_retry_template()
        self.restrict = self._build_restrict_template()

    def _build_instruction_text(self) -> str:
        g = self.prompt_data["tasks"]
        return "\n".join([
            "Your task is to extract structured information from a noisy HTML article.",
            "Strictly follow the schema and instructions. Think step-by-step and do not hallucinate.",
            "",
            "1. Content Extraction:",
            f"- {g['content']['instruction']}",
            f"- Exclude: {', '.join(g['content']['exclude'])}",
            "",
            "2. Topic Classification:",
            f"- {g['focusing']['instruction']}",
            f"- Categories: {' | '.join(g['focusing']['categories'])}",
            f"- Fallback: {g['focusing']['fallback'][0]}",
            "",
            "3. Keywords:",
            f"- {g['keywords']['instruction']}",
            f"- Exclude: {', '.join(g['keywords']['exclusions'])}",
            "",
            "4. Language Detection:",
            f"- {g['lang']['instruction']}",
            f"- Options: {', '.join(g['lang']['options'])}",
            "",
            "Respond in strict JSON format matching this schema:",
            self.format_instructions
        ])

    def _make_template(self, instruction_prefix: str = "") -> PromptTemplate:
        full_instruction = f"{instruction_prefix.strip()}\n\n{self.instruction_text}" if instruction_prefix else self.instruction_text
        return PromptTemplate(
            input_variables=["content"],
            template=TEMPLATE,
            partial_variables={
                "instruction": full_instruction,
                "system_message": self.system_message
            }
        )

    def _build_prompt_template(self) -> PromptTemplate:
        return self._make_template()

    def _build_retry_template(self) -> PromptTemplate:
        return self._make_template("Previous output was invalid. Retry using the correct JSON schema and field constraints.")

    def preview_prompt(self, content: str, level: str = "main") -> str:
        template_map = {
            "main": self.prompt,
            "retry": self.retry
        }
        if level not in template_map:
            raise ValueError("level must be one of: 'main', 'retry'")
        return template_map[level].format_prompt(content=content).text