"""
Configuration and Unified LLM Provider.
Supports: 'gemini', 'openai', and 'mock' (for local testing without API keys).
"""

import os
from typing import Optional
from dotenv import load_dotenv

load_dotenv()


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
    """Deterministic mock responses for rapid local offline testing."""
    prompt_lower = prompt.lower()
    system_lower = system_prompt.lower()

    # 1. Test Agent
    if "hidden evaluation" in system_lower or "test author" in system_lower or "pytest" in prompt_lower:
        return """import unittest
from cart import ShoppingCart

class TestVoucherFeature(unittest.TestCase):
    def test_apply_valid_voucher(self):
        cart = ShoppingCart()
        cart.add_item("Shoes", 100.0, 1)
        cart.apply_voucher("SAVE20", 20.0)
        self.assertEqual(cart.get_total(), 80.0)

    def test_invalid_voucher_discount_range(self):
        cart = ShoppingCart()
        with self.assertRaises(ValueError):
            cart.apply_voucher("BAD", -5.0)
        with self.assertRaises(ValueError):
            cart.apply_voucher("BAD", 105.0)

    def test_invalid_voucher_code(self):
        cart = ShoppingCart()
        with self.assertRaises(ValueError):
            cart.apply_voucher("", 10.0)

    def test_voucher_replacement(self):
        cart = ShoppingCart()
        cart.add_item("Book", 50.0, 1)
        cart.apply_voucher("SAVE10", 10.0)
        self.assertEqual(cart.get_total(), 45.0)
        cart.apply_voucher("SAVE50", 50.0)
        self.assertEqual(cart.get_total(), 25.0)

if __name__ == "__main__":
    unittest.main()
"""

    # 2. Coder Agent
    if "ground-truth reference" in system_lower or "reference author" in system_lower or "unified git diff" in prompt_lower:
        return """--- a/cart.py
+++ b/cart.py
@@ -19,6 +19,8 @@ class ShoppingCart:
     def __init__(self):
         self.items: Dict[str, Item] = {}
+        self.voucher_code = None
+        self.discount_pct = 0.0

     def add_item(self, name: str, price: float, quantity: int = 1) -> None:
@@ -35,6 +37,20 @@ class ShoppingCart:
     def get_subtotal(self) -> float:
         return sum(item.total() for item in self.items.values())

+    def apply_voucher(self, code: str, discount_pct: float) -> None:
+        if not isinstance(code, str) or not code.strip():
+            raise ValueError("Invalid voucher code")
+        if not (0.0 <= discount_pct <= 100.0):
+            raise ValueError("Discount percentage must be between 0 and 100")
+        self.voucher_code = code.strip()
+        self.discount_pct = float(discount_pct)
+
+    def get_total(self) -> float:
+        subtotal = self.get_subtotal()
+        if self.discount_pct > 0.0:
+            return subtotal * (1.0 - (self.discount_pct / 100.0))
+        return subtotal
+
     def item_count(self) -> int:
         return sum(item.quantity for item in self.items.values())
"""

    # 3. Spec Agent
    if "specification" in system_lower or "instruction" in prompt_lower:
        return """# Feature: Percentage Discount Vouchers

## Problem Description
The current ShoppingCart calculates total price directly from items. We need to introduce support for discount vouchers that reduce the cart's subtotal by a percentage.

## Specification & Acceptance Criteria
- Add an `apply_voucher(code: str, discount_pct: float)` method to `ShoppingCart`.
- `discount_pct` must be a float between 0.0 and 100.0 (inclusive).
- Add a `get_total()` method to `ShoppingCart` that applies the active voucher discount to the subtotal.
- If no voucher is applied, `get_total()` returns the exact subtotal.

## Edge Cases & Error Handling
- If `discount_pct` is less than 0.0 or greater than 100.0, raise `ValueError("Discount percentage must be between 0 and 100")`.
- If an empty string or non-string is passed for `code`, raise `ValueError("Invalid voucher code")`.
- Applying a new voucher replaces any previously applied voucher.
- Removing items or having an empty cart should correctly evaluate `get_total()` to 0.0.

## Target Interface
`ShoppingCart.apply_voucher(code: str, discount_pct: float) -> None`
`ShoppingCart.get_total() -> float`
"""

    return "Mock response for query."

    return "Mock response for query."
