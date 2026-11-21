"""
Feature 4: Hidden Acceptance Test Synthesizer.
Generates an isolated pytest test suite (`test_patch.py`) that strictly enforces
the fail-to-pass guarantee (fails on unmodified repo, passes with reference solution applied).

Input: Codebase context + instruction.md specification.
Output: Valid Python pytest file content.
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
        return self._clean_test_output(raw_output)

    def repair_tests(
        self,
        codebase_context: str,
        instruction_md: str,
        previous_tests: str,
        error_message: str,
    ) -> str:
        """Self-Correction Feedback Loop: Fix test suite syntax errors or invalid imports."""
        repair_prompt = f"""Codebase Context:
{codebase_context}

Task Specification:
{instruction_md}

Previous Test File:
{previous_tests}

The test execution failed with the following error:
{error_message}

Please provide a corrected pytest test file. Ensure imports match the codebase accurately.
Return ONLY valid Python pytest code.
"""
        raw_output = call_llm(prompt=repair_prompt, system_prompt=self.system_prompt)
        return self._clean_test_output(raw_output)

    @staticmethod
    def _clean_test_output(raw_output: str) -> str:
        cleaned = raw_output.strip()
        if cleaned.startswith("```python"):
            cleaned = cleaned[len("```python"):].strip()
        elif cleaned.startswith("```"):
            cleaned = cleaned[len("```"):].strip()
        if cleaned.endswith("```"):
            cleaned = cleaned[:-len("```")].strip()
        return cleaned.strip()
