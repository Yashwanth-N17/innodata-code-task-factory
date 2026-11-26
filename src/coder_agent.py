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
        user_prompt = f"""{self._lang_hint()}

Codebase Context:
{codebase_context}

Task Specification (instruction.md):
{instruction_md}

Please generate the reference implementation that satisfies this specification.
Return ONLY the unified git diff patch.
"""
        raw_output = call_llm(
            prompt=user_prompt,
            system_prompt=self.system_prompt,
            language=getattr(self.language_adapter, "language_id", None),
        )
        return self._clean_diff_output(raw_output)

    def repair_diff(
        self,
        codebase_context: str,
        instruction_md: str,
        previous_diff: str,
        error_message: str,
    ) -> str:
        """Self-Correction Feedback Loop: Request LLM to fix a failed patch, compile error, or test failure."""
        repair_prompt = f"""{self._lang_hint()}

Codebase Context:
{codebase_context}

Task Specification:
{instruction_md}

Previous Solution Diff:
{previous_diff}

The above solution failed during sandbox verification with the following error:
{error_message}

Please analyze the failure and provide a corrected unified git diff that resolves the issue and passes all build and test checks.
Return ONLY the corrected unified git diff patch.
"""
        raw_output = call_llm(
            prompt=repair_prompt,
            system_prompt=self.system_prompt,
            language=getattr(self.language_adapter, "language_id", None),
        )
        return self._clean_diff_output(raw_output)

    @staticmethod
    def _clean_diff_output(raw_output: str) -> str:
        cleaned = raw_output.strip()
        cleaned = re.sub(r"^```[a-zA-Z0-9_+-]*\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
        return (cleaned.strip() + "\n") if cleaned.strip() else ""
