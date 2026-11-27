
"""
Feature 4: Hidden Acceptance Test Synthesizer.

Generates an isolated language-native acceptance test suite that strictly
enforces the fail-to-pass guarantee (fails on unmodified repo, passes
with reference solution applied).

Input: Codebase context + instruction.md specification.
Output: Valid test source for the selected language adapter.
"""

import ast
import os
import re
from typing import Optional

from .config import call_llm


class TestAgent:
    __test__ = False

    def __init__(self, prompt_template_path: Optional[str] = None, language_adapter=None):
        self.language_adapter = language_adapter
        if not prompt_template_path:
            prompt_template_path = os.path.join(
                os.path.dirname(__file__), "..", "prompts", "test_prompt.txt"
            )

        with open(prompt_template_path, "r", encoding="utf-8") as f:
            self.system_prompt = f.read()

    def _language_context(self) -> str:
        if not self.language_adapter:
            return ""
        context = self.language_adapter.get_prompt_context()
        return (
            f"Target Language: {context['language']}\n"
            f"Test Framework: {context['test_framework']}\n"
            f"Test Requirements: {context['test_rules']}"
        )

    def _system_prompt(self) -> str:
        if not self.language_adapter or self.language_adapter.language_id == "python":
            return self.system_prompt
        context = self.language_adapter.get_prompt_context()
        return f"""You are a QA engineer creating hidden acceptance tests for a software task.
Write a comprehensive test suite in the target repository's native language and test framework.
{context['test_rules']}
Tests must fail against the unmodified repository and pass after the requested behavior is implemented.
Do not modify production code. Return only valid test source code without Markdown fences.
"""

    def _validate_generated_tests(self, code: str) -> None:
        if not self.language_adapter or self.language_adapter.language_id == "python":
            self._validate_test_code(code)
        elif not code or not code.strip():
            raise ValueError("TestAgent returned an empty response.")

    def generate_tests(
        self,
        codebase_context: str,
        instruction_md: str,
    ) -> str:
        """Generate native test cases that check the specification."""

        user_prompt = f"""Codebase Context:
{codebase_context}

Task Specification (instruction.md):
{instruction_md}

{self._language_context()}

The tests MUST fail against the current unpatched codebase, and pass once implemented.
"""

        raw_output = call_llm(
            prompt=user_prompt,
            system_prompt=self._system_prompt(),
        )

        # Clean Markdown fences.
        cleaned = self._clean_markdown_fences(raw_output)

        # Validate generated tests.
        self._validate_generated_tests(cleaned)

        return cleaned

    def repair_tests(
        self,
        codebase_context: str,
        instruction_md: str,
        rejected_tests: str,
        error_feedback: str,
        max_retries: int = 3,
    ) -> str:
        """
        Repair rejected test code using bounded retry attempts.

        Every retry uses the original codebase context and instructions,
        the rejected test code, and the latest error feedback.

        Python outputs receive syntax and test-definition validation on every
        retry; other language outputs are validated by their sandbox runner.

        Actual test execution remains the verifier's responsibility.
        """

        language_context = self._language_context()
        language_name = self.language_adapter.display_name if self.language_adapter else "Python"

        for attempt in range(max_retries):
            repair_prompt = f"""Codebase Context:
{codebase_context}

Task Specification (instruction.md):
{instruction_md}

{language_context}

The following {language_name} test code was previously rejected:

{rejected_tests}

Reason for rejection:
{error_feedback}

Please correct the test code so that it is valid {language_name} for the
repository's configured test framework.

Return ONLY the corrected test code.
"""

            raw_output = call_llm(
                prompt=repair_prompt,
                system_prompt=self._system_prompt(),
            )

            cleaned = self._clean_markdown_fences(raw_output)

            try:
                self._validate_generated_tests(cleaned)
                return cleaned

            except ValueError as exc:

                # Feed the actual validation error into the next retry.
                error_feedback = str(exc)

                if attempt == max_retries - 1:
                    raise ValueError(
                        f"TestAgent failed to produce valid {language_name} test code "
                        f"after {max_retries} repair attempts. "
                        f"Last error: {error_feedback}"
                    ) from exc

        raise ValueError(
            f"TestAgent failed to produce valid {language_name} test code "
            f"after {max_retries} repair attempts."
        )

    @staticmethod
    def _clean_markdown_fences(raw_output: str) -> str:
        """Remove surrounding Markdown code fences."""

        cleaned = raw_output.lstrip()

        # Match fully fenced code blocks.
        fence_pattern = r"^```(\w+)?\n(.*?)\n```\s*$"

        match = re.match(fence_pattern, cleaned, re.DOTALL)

        if match:
            return match.group(2)

        # Fallback for non-standard fence formatting.
        lines = cleaned.splitlines(keepends=True)

        start = 0
        end = len(lines)

        if start < end and lines[start].startswith("```"):
            start += 1

        if start < end and lines[end - 1].strip() == "```":
            end -= 1

        return "".join(lines[start:end])

    @staticmethod
    def _validate_test_code(code: str) -> None:
        """
        Validate generated Python test code.

        Checks:
        1. Output is non-empty.
        2. Python syntax is valid.
        3. Contains recognizable Python code.
        4. Contains pytest test functions or unittest TestCase classes.
        """

        if not code or not code.strip():
            raise ValueError(
                "TestAgent returned an empty response. "
                "The LLM did not produce any Python test code."
            )

        # Validate Python syntax.
        try:
            tree = ast.parse(code)

        except SyntaxError as exc:
            raise ValueError(
                f"TestAgent response is not valid Python syntax: {exc}"
            ) from exc

        # Detect Python code.
        has_import = re.search(
            r"^\s*(import|from)\b",
            code,
            re.MULTILINE,
        ) is not None

        has_function = re.search(
            r"^\s*def\s+\w+",
            code,
            re.MULTILINE,
        ) is not None

        has_class = re.search(
            r"^\s*class\s+\w+",
            code,
            re.MULTILINE,
        ) is not None

        if not has_import and not has_function and not has_class:
            raise ValueError(
                "TestAgent response does not appear to contain Python test code "
                "(no imports, function definitions, or class definitions found)."
            )

        # Validate test definitions using AST.
        has_test_function = any(
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name.startswith("test_")
            for node in ast.walk(tree)
        )

        has_test_class = any(
            isinstance(node, ast.ClassDef)
            and any(
                (
                    isinstance(base, ast.Name)
                    and base.id == "TestCase"
                )
                or (
                    isinstance(base, ast.Attribute)
                    and base.attr == "TestCase"
                )
                for base in node.bases
            )
            for node in ast.walk(tree)
        )

        if not has_test_function and not has_test_class:
            raise ValueError(
                "TestAgent response contains Python code but no recognizable "
                "test definitions (no 'test_' functions or TestCase subclasses)."
            )
