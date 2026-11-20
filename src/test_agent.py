"""
Member 2: Test Author Agent.
Generates a rigorous pytest test suite (`test_patch.py`) that fails before
the feature is implemented and passes once the reference solution is applied.
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

        # Strip surrounding Markdown code fences without touching the code itself.
        cleaned = self._clean_markdown_fences(raw_output)

        # Validate that the response looks like usable Python test code.
        self._validate_test_code(cleaned)

        return cleaned

    @staticmethod
    def _clean_markdown_fences(raw_output: str) -> str:
        """Remove surrounding Markdown code fences from the LLM response.

        Handles fences with or without a language label (e.g. ```python,
        ```py, ```). Only removes fences that wrap the entire response;
        the actual Python code is never modified.
        """
        cleaned = raw_output.lstrip()

        # Match a fully fenced block: ```lang ... ``` (content captured in group 2).
        fence_pattern = r"^```(\w+)?\n(.*?)\n```\s*$"
        match = re.match(fence_pattern, cleaned, re.DOTALL)
        if match:
            return match.group(2)

        # Fallback: strip a leading fence line and/or trailing fence line
        # when they appear independently (non-standard wrapping).
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
        """Validate that the response looks like usable Python test code.

        Raises ValueError with a useful message if the response is empty,
        contains invalid Python syntax, or appears to be explanatory text
        rather than test code.

        Accepts both pytest-style functions and unittest.TestCase classes,
        since pytest can execute both.
        """
        if not code or not code.strip():
            raise ValueError(
                "TestAgent returned an empty response. The LLM did not produce "
                "any Python test code."
            )

        # Verify the code is syntactically valid Python.
        try:
            ast.parse(code)
        except SyntaxError as exc:
            raise ValueError(
                f"TestAgent response is not valid Python syntax: {exc}"
            ) from exc

        # Detect responses that look like explanatory text rather than test code.
        # Heuristic: contains no import, function definition, or class definition.
        has_import = re.search(r"^\s*(import|from)\b", code, re.MULTILINE) is not None
        has_function = re.search(r"^\s*def\s+\w+", code, re.MULTILINE) is not None
        has_class = re.search(r"^\s*class\s+\w+", code, re.MULTILINE) is not None

        if not has_import and not has_function and not has_class:
            raise ValueError(
                "TestAgent response does not appear to contain Python test code "
                "(no imports, function definitions, or class definitions found). "
                "The LLM may have returned explanatory text instead of tests."
            )

        # Ensure there is at least one test-like definition: a function starting
        # with 'test_' or a unittest.TestCase class.
        has_test_function = re.search(
            r"^\s*def\s+test_\w+", code, re.MULTILINE
        ) is not None
        has_test_class = re.search(
            r"^\s*class\s+\w+.*TestCase", code
        ) is not None

        if not has_test_function and not has_test_class:
            raise ValueError(
                "TestAgent response contains Python code but no recognizable test "
                "definitions (no 'test_' functions or TestCase subclasses)."
            )
