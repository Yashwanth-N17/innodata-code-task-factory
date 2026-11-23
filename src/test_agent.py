
"""
Feature 4: Hidden Acceptance Test Synthesizer.

Generates an isolated pytest test suite (`test_patch.py`) that strictly
enforces the fail-to-pass guarantee (fails on unmodified repo, passes
with reference solution applied).

Input: Codebase context + instruction.md specification.
Output: Valid Python pytest file content.
"""

import ast
import os
import re
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

    def generate_tests(
        self,
        codebase_context: str,
        instruction_md: str,
    ) -> str:
        """Generate pytest test cases testing the specification."""

        user_prompt = f"""Codebase Context:
{codebase_context}

Task Specification (instruction.md):
{instruction_md}

Write a comprehensive pytest test file that thoroughly tests all acceptance criteria and edge cases.
The tests MUST fail against the current unpatched codebase, and pass once implemented.
Return ONLY valid Python pytest code.
"""

        raw_output = call_llm(
            prompt=user_prompt,
            system_prompt=self.system_prompt,
        )

        # Clean Markdown fences.
        cleaned = self._clean_markdown_fences(raw_output)

        # Validate generated tests.
        self._validate_test_code(cleaned)

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
        Repair rejected Python test code using bounded retry attempts.

        Every retry uses the original codebase context and instructions,
        the rejected test code, and the latest error feedback.

        Syntax validation and test-definition validation are performed
        on every repaired output.

        Actual test execution remains the verifier's responsibility.
        """

        for attempt in range(max_retries):

            repair_prompt = f"""Codebase Context:
{codebase_context}

Task Specification (instruction.md):
{instruction_md}

The following Python test code was previously rejected:

{rejected_tests}

Reason for rejection:
{error_feedback}

Please correct the test code so that it is valid Python with recognizable
test definitions (test_ functions or unittest.TestCase classes).

Return ONLY the corrected Python test code.
"""

            raw_output = call_llm(
                prompt=repair_prompt,
                system_prompt=self.system_prompt,
            )

            cleaned = self._clean_markdown_fences(raw_output)

            try:
                self._validate_test_code(cleaned)
                return cleaned

            except ValueError as exc:

                # Feed the actual validation error into the next retry.
                error_feedback = str(exc)

                if attempt == max_retries - 1:
                    raise ValueError(
                        f"TestAgent failed to produce valid Python test code "
                        f"after {max_retries} repair attempts. "
                        f"Last error: {error_feedback}"
                    ) from exc

        raise ValueError(
            f"TestAgent failed to produce valid Python test code "
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
