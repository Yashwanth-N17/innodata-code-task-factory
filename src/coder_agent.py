"""
Feature 3: Ground-Truth Reference Coder & Self-Repair Agent.
Generates the reference solution formatted strictly as a unified Git diff (`solution.diff`).
Includes an iterative self-repair loop to fix rejected patches or test failures.

Input: Codebase context + instruction.md specification.
Output: Valid unified Git diff patch string.
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
        return self._clean_diff_output(raw_output)

    def repair_diff(
        self,
        codebase_context: str,
        instruction_md: str,
        previous_diff: str,
        error_message: str,
    ) -> str:
        """Self-Correction Feedback Loop: Request LLM to fix a failed patch or test failure."""
        repair_prompt = f"""Codebase Context:
{codebase_context}

Task Specification:
{instruction_md}

Previous Solution Diff:
{previous_diff}

The above solution failed during sandbox verification with the following error:
{error_message}

Please analyze the failure and provide a corrected unified git diff that resolves the issue and passes all tests.
Return ONLY the corrected unified git diff patch.
"""
        raw_output = call_llm(prompt=repair_prompt, system_prompt=self.system_prompt)
        return self._clean_diff_output(raw_output)

    @staticmethod
    def _clean_diff_output(raw_output: str) -> str:
        cleaned = raw_output.strip()
        if cleaned.startswith("```diff"):
            cleaned = cleaned[len("```diff"):].strip()
        elif cleaned.startswith("```"):
            cleaned = cleaned[len("```"):].strip()
        if cleaned.endswith("```"):
            cleaned = cleaned[:-len("```")].strip()
        return cleaned.strip()
