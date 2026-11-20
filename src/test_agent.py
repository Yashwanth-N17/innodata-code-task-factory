"""
Member 2: Test Author Agent.
Generates a rigorous pytest test suite (`test_patch.py`) that fails before
the feature is implemented and passes once the reference solution is applied.
"""

import os
from typing import Optional
from .config import call_llm


class TestAgent:
    def __init__(self, prompt_template_path: Optional[str] = None):
        if not prompt_template_path:
            prompt_template_path = os.path.join(
                os.path.dirname(__file__), "..", "prompts", "test_prompt.txt"
            )
        with open(prompt_template_path, "r", encoding="utf-8") as f:
            self.system_prompt = f.read()

    def generate_tests(self, codebase_context: str, instruction_md: str) -> str:
        """Generate pytest test cases testing the specification."""
        user_prompt = f"""Codebase Context:
{codebase_context}

Task Specification (instruction.md):
{instruction_md}

Write a comprehensive pytest test file that thoroughly tests all acceptance criteria and edge cases.
The tests MUST fail against the current unpatched codebase, and pass once implemented.
Return ONLY valid Python pytest code.
"""
        raw_output = call_llm(prompt=user_prompt, system_prompt=self.system_prompt)

        # Clean markdown code blocks if the LLM wrapped it in ```python ... ```
        cleaned = raw_output.strip()
        if cleaned.startswith("```python"):
            cleaned = cleaned[len("```python"):].strip()
        elif cleaned.startswith("```"):
            cleaned = cleaned[len("```"):].strip()
        if cleaned.endswith("```"):
            cleaned = cleaned[:-len("```")].strip()

        return cleaned
