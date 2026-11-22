"""
Feature 9: Unified LLM Provider & Dynamic Mock Service.
Supports: 'gemini', 'openai', and 'mock' (dynamic mock engine for rapid local testing without API keys).
"""

import os
import re
from typing import Optional

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


class Config:
    LLM_PROVIDER: str = os.getenv("LLM_PROVIDER", "mock").lower()
    MODEL_NAME: str = os.getenv("MODEL_NAME", "gemini-2.0-flash")
    GEMINI_API_KEY: Optional[str] = os.getenv("GEMINI_API_KEY")
    OPENAI_API_KEY: Optional[str] = os.getenv("OPENAI_API_KEY")


def call_llm(prompt: str, system_prompt: str = "") -> str:
    """Unified LLM call supporting Gemini, OpenAI, or Mock mode."""
    provider = Config.LLM_PROVIDER

    if provider == "mock":
        return _mock_llm_response(prompt, system_prompt)

    if provider == "gemini":
        try:
            from google import genai
            client = genai.Client(api_key=Config.GEMINI_API_KEY)
            full_prompt = f"{system_prompt}\n\nUser Request:\n{prompt}" if system_prompt else prompt
            response = client.models.generate_content(
                model=Config.MODEL_NAME,
                contents=full_prompt,
            )
            return response.text
        except ImportError:
            raise ImportError("Please install `google-genai` or set LLM_PROVIDER=mock in .env")

    if provider == "openai":
        try:
            from openai import OpenAI
            client = OpenAI(api_key=Config.OPENAI_API_KEY)
            messages = []
            if system_prompt:
                messages.append({"role": "system", "content": system_prompt})
            messages.append({"role": "user", "content": prompt})

            response = client.chat.completions.create(
                model=Config.MODEL_NAME,
                messages=messages,
            )
            return response.choices[0].message.content
        except ImportError:
            raise ImportError("Please install `openai` or set LLM_PROVIDER=mock in .env")

    raise ValueError(f"Unknown LLM provider: {provider}")


def _mock_llm_response(prompt: str, system_prompt: str) -> str:
    """Dynamic mock responses adapting to the scanned repository for rapid local offline testing."""
    prompt_lower = prompt.lower()
    system_lower = system_prompt.lower()

    # Detect target file and class from prompt if available
    file_match = re.search(r"in ([a-zA-Z0-9_\-/]+\.py)", prompt)
    target_file = file_match.group(1) if file_match else "service.py"

    cls_match = re.search(r"class ([a-zA-Z0-9_]+)", prompt)
    target_cls = cls_match.group(1) if cls_match else "PaymentProcessor"

    # Strip directory prefix for module import (e.g. src/utils.py -> utils)
    module_name = os.path.splitext(os.path.basename(target_file))[0]

    # 1. Test Agent (Check for test author / QA / pytest first)
    if any(k in system_lower for k in ["qa and benchmark", "test author", "designing hidden evaluation", "test_agent"]) or "return only valid python pytest code" in prompt_lower:
        return f"""import unittest
try:
    from {module_name} import {target_cls}
except ImportError:
    import sys
    sys.path.insert(0, ".")
    from {module_name} import {target_cls}

class TestFeatureValidation(unittest.TestCase):
    def test_feature_execution(self):
        try:
            obj = {target_cls}(".")
        except Exception:
            obj = {target_cls}()
        self.assertTrue(hasattr(obj, "validate_state") and obj.validate_state())

    def test_feature_boundary(self):
        try:
            obj = {target_cls}(".")
        except Exception:
            obj = {target_cls}()
        with self.assertRaises(ValueError):
            obj.apply_voucher("", -1.0)

if __name__ == "__main__":
    unittest.main()
"""

    # 2. Coder Agent
    if any(k in system_lower for k in ["coder_agent", "ground-truth reference author", "solution/solution.diff"]) or "unified git diff" in prompt_lower:
        return f"""--- a/{target_file}
+++ b/{target_file}
@@ -10,6 +10,18 @@
+    def validate_state(self) -> bool:
+        \"\"\"Validate internal state integrity.\"\"\"
+        return True
+
+    def apply_voucher(self, code: str, discount_pct: float) -> None:
+        \"\"\"Apply discount voucher code.\"\"\"
+        if not isinstance(code, str) or not code.strip():
+            raise ValueError("Invalid voucher code")
+        if not (0.0 <= discount_pct <= 100.0):
+            raise ValueError("Discount percentage must be between 0 and 100")
+        self.voucher_code = code.strip()
+        self.discount_pct = float(discount_pct)
"""

    # 3. Spec Agent
    if any(k in system_lower for k in ["spec_generator", "specification designer", "task ideation", "instruction.md"]) or "task category request" in prompt_lower:
        return f"""# Feature: State Integrity & Voucher Validation

## Problem Description
The `{target_cls}` in `{target_file}` needs enhanced state validation and optional discount/voucher handling to support safe execution and boundary checks.

## Business Requirements
- Ensure `{target_cls}` maintains robust internal integrity through explicit validation mechanisms.
- Provide secure discount voucher application protecting against invalid or fraudulent percentage values.
- Maintain non-breaking backward compatibility with existing initialization workflows.

## Input & Output Schema
- `validate_state()`: Takes no arguments, returns boolean (`bool`) representing state validity.
- `apply_voucher(code: str, discount_pct: float)`: Accepts string `code` and float `discount_pct`, returns `None`.

## Specification & Acceptance Criteria
- Add state validation support (`validate_state() -> bool`) to `{target_cls}`.
- Support voucher discount configuration with defensive exception guarantees.
- Ensure all boundary cases return predictable exceptions rather than silent failures.

## Edge Cases & Error Handling
- Invalid or empty code parameters (e.g. empty string or whitespace) must raise `ValueError("Invalid voucher code")`.
- Numerical percentages out of range (less than 0.0 or greater than 100.0) must raise `ValueError`.
- Non-string code parameters or non-numeric discount values must raise defensive type exceptions (`ValueError` or `TypeError`).

## Target Interface
`{target_cls}.validate_state() -> bool`
`{target_cls}.apply_voucher(code: str, discount_pct: float) -> None`
"""

    return "Mock response for query."
