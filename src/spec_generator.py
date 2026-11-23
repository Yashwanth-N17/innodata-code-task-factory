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
    # Pattern to find line number leaks in validation
    LINE_LEAK_FIND_RE = re.compile(
        r"\b(?:(?:at|on|around|near|in|from)\s+)?lines?\s*(?:#|:)?\s*\d+(?:\s*(?:-|to)\s*\d+)?\b"
        r"|\b[a-zA-Z0-9_\-./]+\.py:\d+(?::\d+)?\b"
        r"|\bL\d+(?:-(?:L)?\d+)?\b",
        re.IGNORECASE,
    )

    # Patterns used during sanitization
    FILE_LINE_RE = re.compile(r"(?<=\.py):\d+(?::\d+)?\b", re.IGNORECASE)
    LINE_NUM_RE = re.compile(
        r"\b(?:(?:at|on|around|near|in|from)\s+)?lines?\s*(?:#|:)?\s*\d+(?:\s*(?:-|to)\s*\d+)?\b"
        r"|\bL\d+(?:-(?:L)?\d+)?\b",
        re.IGNORECASE,
    )

    # Pattern for private variables, chained private members, and private method calls
    PRIV_LEAK_RE = re.compile(
        r"\b[a-zA-Z0-9_]+(?:\._[a-zA-Z0-9_]*)+\b"
        r"|\._[a-zA-Z0-9_]*\b"
        r"|\b_[a-zA-Z0-9_]+(?=\s*\()",
        re.IGNORECASE,
    )

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
            # Re-verify post-sanitization
            is_valid, warnings = self.validate_leakage(spec_content)
            if not is_valid:
                spec_content = self.sanitize_spec(spec_content)

        return spec_content

    @classmethod
    def validate_leakage(cls, spec_text: str) -> Tuple[bool, List[str]]:
        """
        SWE-bench Compliance Auditor:
        Detects if specification accidentally leaks implementation details ("HOW")
        such as explicit line numbers, file:line combinations, or private variables/methods.
        """
        warnings = []
        line_matches = cls.LINE_LEAK_FIND_RE.findall(spec_text)
        if line_matches:
            warnings.append(f"Contains line number references: {', '.join(line_matches)}")

        priv_matches = cls.PRIV_LEAK_RE.findall(spec_text)
        if priv_matches:
            warnings.append(f"Leaks internal private member references: {', '.join(priv_matches)}")

        return len(warnings) == 0, warnings

    @classmethod
    def sanitize_spec(cls, spec_text: str) -> str:
        """Strip prohibited implementation artifacts while preserving requirements."""
        # 1. Strip file line numbers (e.g. service.py:42 -> service.py)
        sanitized = cls.FILE_LINE_RE.sub("", spec_text)
        # 2. Strip general line numbers and references (e.g. line 45, around line 99, L42)
        sanitized = cls.LINE_NUM_RE.sub("", sanitized)
        # 3. Redact private variables and method references with [internal]
        sanitized = cls.PRIV_LEAK_RE.sub("[internal]", sanitized)
        # 4. Clean up any residual private leaks and collapse consecutive [internal] markers
        if cls.PRIV_LEAK_RE.search(sanitized):
            sanitized = cls.PRIV_LEAK_RE.sub("[internal]", sanitized)
        sanitized = re.sub(r"\[internal\](?:\s*\.\s*\[internal\]|\[internal\])+", "[internal]", sanitized)
        # 5. Clean up whitespace and punctuation artifacts
        sanitized = re.sub(r" +", " ", sanitized)
        sanitized = re.sub(r"\s+([,.:;?!])", r"\1", sanitized)
        lines = [re.sub(r" +", " ", line).strip() for line in sanitized.splitlines()]
        sanitized = "\n".join(lines)
        return sanitized.strip()

    @staticmethod
    def extract_edge_cases(spec_text: str) -> List[str]:
        """Extract concrete edge case items from the Edge Cases section."""
        match = re.search(
            r"##\s*Edge Cases[^\n]*\n(.*?)(?=\n##|\Z)",
            spec_text,
            re.DOTALL | re.IGNORECASE,
        )
        if not match:
            return []
        section = match.group(1).strip()
        cases = []
        for line in section.splitlines():
            line_str = line.strip()
            if not line_str:
                continue
            if re.match(r"^(?:[-*+•]|\d+[.)])\s+", line_str):
                cleaned_line = re.sub(r"^(?:[-*+•]|\d+[.)])\s+", "", line_str).strip()
                if cleaned_line:
                    cases.append(cleaned_line)
        return cases

    @classmethod
    def validate_structure(cls, spec_text: str) -> Tuple[bool, List[str]]:
        """Check for mandatory SWE-bench sections and edge case density."""
        issues = []
        for req_heading in [
            "Business Requirements",
            "Input & Output Schema",
            "Specification & Acceptance Criteria",
            "Edge Cases",
        ]:
            if req_heading.lower() not in spec_text.lower():
                issues.append(f"Missing required section: {req_heading}")
        cases = cls.extract_edge_cases(spec_text)
        if len(cases) < 3:
            issues.append(f"Requires >= 3 edge cases, found {len(cases)}")
        return len(issues) == 0, issues
