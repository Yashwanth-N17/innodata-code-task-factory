"""
Member 1: Specification Generator.
Uses LLM to generate `instruction.md` adhering strictly to "WHAT to build, never HOW".
"""

import os
from typing import Dict, Any, Optional
from .config import call_llm


class SpecGenerator:
    def __init__(self, prompt_template_path: Optional[str] = None):
        if not prompt_template_path:
            prompt_template_path = os.path.join(
                os.path.dirname(__file__), "..", "prompts", "spec_system_prompt.txt"
            )
        with open(prompt_template_path, "r", encoding="utf-8") as f:
            self.system_prompt = f.read()

    def generate(self, codebase_context: str, category: str = "new_feature") -> str:
        """Generate the specification markdown."""
        user_prompt = f"""Target Codebase Context:
{codebase_context}

Task Category Request: {category.upper()}
Please generate a realistic software engineering task specification for an AI coding agent.
Remember: Describe WHAT must be built and expected behavior/edge cases. Do NOT tell the agent HOW to implement it.
"""
        spec_content = call_llm(prompt=user_prompt, system_prompt=self.system_prompt)
        return spec_content.strip()
