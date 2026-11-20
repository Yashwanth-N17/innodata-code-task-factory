"""
Member 2: Reference Coder Agent.
Generates the ground-truth solution implementation formatted as a unified Git diff (`solution.diff`).
"""

import os
from typing import Optional
from .config import call_llm


class CoderAgent:
    def __init__(self, prompt_template_path: Optional[str] = None):
        if not prompt_template_path:
            prompt_template_path = os.path.join(
                os.path.dirname(__file__), "..", "prompts", "coder_prompt.txt"
            )
        with open(prompt_template_path, "r", encoding="utf-8") as f:
            self.system_prompt = f.read()

    def generate_diff(self, codebase_context: str, instruction_md: str) -> str:
        """Generate unified git diff reference solution."""
        user_prompt = f"""Codebase Context:
{codebase_context}

Task Specification (instruction.md):
{instruction_md}

Please generate the reference implementation that satisfies this specification.
Return ONLY the unified git diff patch.
"""
        raw_output = call_llm(prompt=user_prompt, system_prompt=self.system_prompt)
        
        # Clean markdown code blocks if the LLM wrapped it in ```diff ... ```
        cleaned = raw_output.strip()
        if cleaned.startswith("```diff"):
            cleaned = cleaned[len("```diff"):].strip()
        elif cleaned.startswith("```"):
            cleaned = cleaned[len("```"):].strip()
        if cleaned.endswith("```"):
            cleaned = cleaned[:-len("```")].strip()

        return cleaned
