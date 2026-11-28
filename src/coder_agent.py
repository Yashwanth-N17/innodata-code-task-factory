
"""
Feature 3: Ground-Truth Reference Coder & Self-Repair Agent.
Generates the reference solution formatted strictly as a unified Git diff (`solution.diff`)
for any supported language. Includes an iterative self-repair loop to fix rejected patches
or build/test failures.
"""

import os
import re
from typing import Optional
from .config import call_llm


class CoderAgent:
    def __init__(self, prompt_template_path: Optional[str] = None, language_adapter=None):
        self.language_adapter = language_adapter
        if not prompt_template_path:
            prompt_template_path = os.path.join(
                os.path.dirname(__file__), "..", "prompts", "coder_prompt.txt"
            )

        with open(prompt_template_path, "r", encoding="utf-8") as f:
            self.system_prompt = f.read()

    def _lang_hint(self) -> str:
        if not self.language_adapter:
            return "Language: Python\nRules: Ensure all imports, syntax, and typing are 100% valid Python."
        ctx = self.language_adapter.get_prompt_context()
        return f"Target Language: {ctx['language']}\nRules: {ctx['coder_rules']}"

    def generate_diff(self, codebase_context: str, instruction_md: str) -> str:
        """Generate unified git diff reference solution."""

        user_prompt = f"""Codebase Context:
{codebase_context}

Task Specification (instruction.md):
{instruction_md}

{self._lang_hint()}

Please generate the reference implementation that satisfies this specification.
Return ONLY the unified git diff patch.
"""

        raw_output = call_llm(
            prompt=user_prompt,
            system_prompt=self.system_prompt
        )

        # Strip surrounding Markdown code fences.
        cleaned = self._clean_markdown_fences(raw_output)

        # Validate the generated diff.
        self._validate_diff(cleaned)

        return cleaned

    def repair_diff(
        self,
        codebase_context: str,
        instruction_md: str,
        rejected_diff: str,
        error_feedback: str,
        max_retries: int = 3,
    ) -> str:
        """
        Repair a rejected unified git diff using bounded retry attempts.

        Every retry uses the original codebase context and instructions,
        the rejected diff, and the latest error feedback.

        Structural validation is performed on every repaired output.
        Actual patch execution and testing remain the verifier's responsibility.
        """

        for attempt in range(max_retries):

            repair_prompt = f"""Codebase Context:
{codebase_context}

Task Specification (instruction.md):
{instruction_md}

{self._lang_hint()}

The following unified git diff was previously rejected:

{rejected_diff}

Reason for rejection:
{error_feedback}

Please correct the patch so that it is a valid unified git diff that
applies cleanly with `git apply`.

Return ONLY the corrected unified git diff patch.
"""

            raw_output = call_llm(
                prompt=repair_prompt,
                system_prompt=self.system_prompt
            )

            cleaned = self._clean_markdown_fences(raw_output)

            try:
                self._validate_diff(cleaned)
                return cleaned

            except ValueError as exc:

                # Pass the actual validation failure to the next retry.
                error_feedback = str(exc)

                if attempt == max_retries - 1:
                    raise ValueError(
                        f"CoderAgent failed to produce a valid unified git diff "
                        f"after {max_retries} repair attempts. "
                        f"Last error: {error_feedback}"
                    ) from exc

        raise ValueError(
            f"CoderAgent failed to produce a valid unified git diff "
            f"after {max_retries} repair attempts."
        )

    @staticmethod
    def _clean_markdown_fences(raw_output: str) -> str:
        """
        Remove surrounding Markdown code fences without modifying
        the actual unified diff contents.
        """

        cleaned = raw_output.lstrip()

        # Match a fully fenced code block.
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
    def _validate_diff(diff_text: str) -> None:
        """
        Validate that the response looks like a unified git diff.

        Raises ValueError if the response is empty, contains
        explanatory text instead of a patch, or lacks diff structure.
        """

        if not diff_text or not diff_text.strip():
            raise ValueError(
                "CoderAgent returned an empty response. "
                "The LLM did not produce a unified git diff patch."
            )

        # Unified diff file headers.
        has_minus_header = re.search(
            r"^--- ",
            diff_text,
            re.MULTILINE
        ) is not None

        has_plus_header = re.search(
            r"^\+\+\+ ",
            diff_text,
            re.MULTILINE
        ) is not None

        # Unified diff hunk header.
        has_hunk = re.search(
            r"^@@ -\d+(?:,\d+)? \+\d+(?:,\d+)? @@",
            diff_text,
            re.MULTILINE
        ) is not None

        if not has_minus_header or not has_plus_header:
            raise ValueError(
                "CoderAgent response does not appear to be a valid "
                "unified git diff. Missing '--- ' and/or '+++ ' "
                "file headers. The LLM may have returned explanatory "
                "text instead of a patch."
            )

        if not has_hunk:
            raise ValueError(
                "CoderAgent response contains file headers but no "
                "hunk headers (@@ ... @@). The response may be "
                "incomplete or malformed."
            )
