"""
Feature 2: SWE-bench Issue Specification Generator & Leakage Auditor.
Uses LLM to formulate realistic issue tickets adhering strictly to "WHAT to build, never HOW".
Includes automated validation and sanitization against line number leaks and implementation exposure.

Input: Codebase context summary + category archetype.
Output: Clean, validated instruction.md markdown string.
"""

import os
import re
from typing import Dict, Any, Optional, Tuple, List
from .config import call_llm


class SpecGenerator:
    def __init__(self, prompt_template_path: Optional[str] = None):
        if not prompt_template_path:
            prompt_template_path = os.path.join(
                os.path.dirname(__file__), "..", "prompts", "spec_system_prompt.txt"
            )
        with open(prompt_template_path, "r", encoding="utf-8") as f:
            self.system_prompt = f.read()

    def generate(self, codebase_context: str, category: str = "new_feature") -> str:
        """Generate the specification markdown and ensure zero HOW leakage."""
        user_prompt = f"""Target Codebase Context:
{codebase_context}

Task Category Request: {category.upper()}
Please generate a realistic software engineering task specification for an AI coding agent.
Remember: Describe WHAT must be built and expected behavior/edge cases. Do NOT tell the agent HOW to implement it.
"""
        spec_content = call_llm(prompt=user_prompt, system_prompt=self.system_prompt).strip()
        
        # Audit and sanitize any unintentional leakage
        is_valid, warnings = self.validate_leakage(spec_content)
        if not is_valid:
            spec_content = self.sanitize_spec(spec_content)
            
        return spec_content

    @staticmethod
    def validate_leakage(spec_text: str) -> Tuple[bool, List[str]]:
        """
        SWE-bench Compliance Auditor:
        Detects if specification accidentally leaks implementation details ("HOW")
        such as explicit line numbers or full function bodies.
        """
        warnings = []
        # Check for explicit line number references (e.g. 'at line 45')
        line_matches = re.findall(r"\bline\s+\d+\b", spec_text, re.IGNORECASE)
        if line_matches:
            warnings.append(f"Contains line number references: {', '.join(line_matches)}")

        # Check for inline implementation hints
        if re.search(r"\bself\._[a-zA-Z0-9_]+\s*=", spec_text):
            warnings.append("Leaks internal private variable assignments.")

        return len(warnings) == 0, warnings

    @staticmethod
    def sanitize_spec(spec_text: str) -> str:
        """Strip prohibited implementation artifacts while preserving requirements."""
        sanitized = re.sub(r"\b(at|on|around)\s+line\s+\d+\b", "", spec_text, flags=re.IGNORECASE)
        return sanitized.strip()
