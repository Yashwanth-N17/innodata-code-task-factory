"""
Feature 4: Hidden Acceptance Test Synthesizer.
Generates an isolated language-native test suite (`tests/test_feature_patch.*`) that strictly
enforces the fail-to-pass guarantee (fails on unmodified repo, passes with reference solution applied).
"""

import os
import re
from typing import Optional
from .config import call_llm


class TestAgent:
    def __init__(self, prompt_template_path: Optional[str] = None, language_adapter=None):
        self.language_adapter = language_adapter
        if not prompt_template_path:
            prompt_template_path = os.path.join(
                os.path.dirname(__file__), "..", "prompts", "test_prompt.txt"
            )
        with open(prompt_template_path, "r", encoding="utf-8") as f:
            self.system_prompt = f.read()

    def _lang_instructions(self) -> tuple:
        if not self.language_adapter:
            return (
                "Python",
                "pytest",
                "Write a comprehensive pytest test file that thoroughly tests all acceptance criteria and edge cases.\n"
                "Return ONLY valid Python pytest code.",
            )
        ctx = self.language_adapter.get_prompt_context()
        return (
            ctx["language"],
            ctx["test_framework"],
            f"{ctx['test_rules']}\nReturn ONLY valid {ctx['language']} test code.",
        )

    def generate_tests(self, codebase_context: str, instruction_md: str) -> str:
        """Generate language-native test cases verifying the specification."""
        lang, framework, rules = self._lang_instructions()
        user_prompt = f"""Target Language: {lang} (Test Framework: {framework})

Codebase Context:
{codebase_context}

Task Specification (instruction.md):
{instruction_md}

{rules}
The tests MUST fail against the current unpatched codebase, and pass once implemented.
"""
        raw_output = call_llm(
            prompt=user_prompt,
            system_prompt=self.system_prompt,
            language=getattr(self.language_adapter, "language_id", None),
        )
        return self._clean_test_output(raw_output)

    def repair_tests(
        self,
        codebase_context: str,
        instruction_md: str,
        previous_tests: str,
        error_message: str,
    ) -> str:
        """Self-Correction Feedback Loop: Fix test suite syntax errors, compile errors, or invalid imports."""
        lang, framework, _ = self._lang_instructions()
        repair_prompt = f"""Target Language: {lang} (Test Framework: {framework})

Codebase Context:
{codebase_context}

Task Specification:
{instruction_md}

Previous Test File:
{previous_tests}

The test execution failed with the following error:
{error_message}

Please provide a corrected {lang} test file. Ensure imports/includes and assertions match the codebase accurately.
Return ONLY valid {lang} test code.
"""
        raw_output = call_llm(
            prompt=repair_prompt,
            system_prompt=self.system_prompt,
            language=getattr(self.language_adapter, "language_id", None),
        )
        return self._clean_test_output(raw_output)

    @staticmethod
    def _clean_test_output(raw_output: str) -> str:
        cleaned = raw_output.strip()
        cleaned = re.sub(r"^```[a-zA-Z0-9_+-]*\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
        return (cleaned.strip() + "\n") if cleaned.strip() else ""
