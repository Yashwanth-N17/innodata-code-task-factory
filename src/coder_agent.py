"""
Member 2: Reference Coder Agent.
Generates the ground-truth solution implementation formatted as a unified Git diff (`solution.diff`).
"""

import os
import re
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

        # Strip surrounding Markdown code fences without touching patch contents.
        cleaned = self._clean_markdown_fences(raw_output)

        # Validate that the result looks like a real unified git diff.
        self._validate_diff(cleaned)

        return cleaned

    @staticmethod
    def _clean_markdown_fences(raw_output: str) -> str:
        """Remove surrounding Markdown code fences from the LLM response.

        Handles fences with or without a language label (e.g. ```diff,
        ```patch, ```). Only removes fences that wrap the entire response;
        the actual patch contents are never modified.
        """
        cleaned = raw_output.lstrip()

        # Match a fully fenced block: ```lang ... ``` (content captured in group 2).
        fence_pattern = r"^```(\w+)?\n(.*?)\n```\s*$"
        match = re.match(fence_pattern, cleaned, re.DOTALL)
        if match:
            return match.group(2)

        # Fallback: strip a leading fence line and/or trailing fence line
        # when they appear independently (non-standard wrapping).
        # keepends=True preserves line terminators so trailing newlines survive.
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
        """Validate that the response looks like a unified git diff.

        Raises ValueError with a useful message if the response is empty,
        contains only explanatory text, or lacks unified diff indicators.
        """
        if not diff_text or not diff_text.strip():
            raise ValueError(
                "CoderAgent returned an empty response. The LLM did not produce "
                "a unified git diff patch."
            )

        # Unified diff file headers: --- a/... and +++ b/...
        has_minus_header = re.search(r"^--- ", diff_text, re.MULTILINE) is not None
        has_plus_header = re.search(r"^\+\+\+ ", diff_text, re.MULTILINE) is not None

        # Hunk header: @@ -start,count +start,count @@
        has_hunk = re.search(
            r"^@@ -\d+(?:,\d+)? \+\d+(?:,\d+)? @@", diff_text, re.MULTILINE
        ) is not None

        if not has_minus_header or not has_plus_header:
            raise ValueError(
                "CoderAgent response does not appear to be a valid unified git diff. "
                "Missing '--- ' and/or '+++ ' file headers. "
                "The LLM may have returned explanatory text instead of a patch."
            )

        if not has_hunk:
            raise ValueError(
                "CoderAgent response contains file headers but no hunk headers "
                "(@@ ... @@). The response may be incomplete or malformed."
            )
