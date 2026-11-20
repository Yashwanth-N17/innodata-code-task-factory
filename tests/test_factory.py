"""
Unit tests for the Factory pipeline modules.
Uses dynamic temporary fixtures so no static sample repo is required.
"""

import unittest
import os
import tempfile
import shutil
from unittest.mock import patch
from src.analyzer import CodebaseAnalyzer
from src.variation_controller import VariationController
from src.spec_generator import SpecGenerator
from src.coder_agent import CoderAgent
from src.config import Config
from src.test_agent import TestAgent


class TestFactoryComponents(unittest.TestCase):
    def setUp(self):
        Config.LLM_PROVIDER = "mock"
        self.temp_dir = tempfile.mkdtemp()
        # Create a dummy python module to test AST scanning
        dummy_file = os.path.join(self.temp_dir, "service.py")
        with open(dummy_file, "w", encoding="utf-8") as f:
            f.write('''
class PaymentProcessor:
    """Processes online payments."""
    def __init__(self, currency="USD"):
        self.currency = currency

    def charge(self, amount: float) -> bool:
        return amount > 0
''')

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_analyzer_scan(self):
        analyzer = CodebaseAnalyzer(self.temp_dir)
        data = analyzer.scan()
        self.assertIn("service.py", data["files"])
        self.assertGreater(len(data["classes"]), 0)
        class_names = [c["name"] for c in data["classes"]]
        self.assertIn("PaymentProcessor", class_names)

    def test_variation_controller(self):
        vc = VariationController()
        cats = vc.select_category(count=4)
        self.assertEqual(len(cats), 4)
        report = vc.get_variation_report()
        self.assertEqual(report["total_tasks"], 4)
        self.assertGreaterEqual(report["diversity_score"], 0.75)

    def test_spec_generator_mock(self):
        analyzer = CodebaseAnalyzer(self.temp_dir)
        ctx = analyzer.get_context_prompt()
        gen = SpecGenerator()
        spec = gen.generate(codebase_context=ctx, category="new_feature")
        self.assertIn("Specification & Acceptance Criteria", spec)
        self.assertIn("Edge Cases", spec)


class TestCoderAgent(unittest.TestCase):
    """Focused tests for CoderAgent.generate_diff using mocked LLM calls."""

    def setUp(self):
        Config.LLM_PROVIDER = "mock"
        self.agent = CoderAgent()

    VALID_DIFF = (
        "--- a/cart.py\n"
        "+++ b/cart.py\n"
        "@@ -10,6 +10,8 @@ class ShoppingCart:\n"
        "     def __init__(self):\n"
        "+        self.voucher_code = None\n"
        "+        self.discount_pct = 0.0\n"
    )

    def test_generate_diff_returns_valid_diff(self):
        with patch("src.coder_agent.call_llm", return_value=self.VALID_DIFF):
            result = self.agent.generate_diff("context", "instruction")
        self.assertEqual(result, self.VALID_DIFF)

    def test_generate_diff_strips_diff_fence(self):
        wrapped = "```diff\n" + self.VALID_DIFF + "\n```"
        with patch("src.coder_agent.call_llm", return_value=wrapped):
            result = self.agent.generate_diff("context", "instruction")
        self.assertEqual(result, self.VALID_DIFF)

    def test_generate_diff_strips_patch_fence(self):
        wrapped = "```patch\n" + self.VALID_DIFF + "\n```"
        with patch("src.coder_agent.call_llm", return_value=wrapped):
            result = self.agent.generate_diff("context", "instruction")
        self.assertEqual(result, self.VALID_DIFF)

    def test_generate_diff_strips_plain_fence(self):
        wrapped = "```\n" + self.VALID_DIFF + "\n```"
        with patch("src.coder_agent.call_llm", return_value=wrapped):
            result = self.agent.generate_diff("context", "instruction")
        self.assertEqual(result, self.VALID_DIFF)

    def test_generate_diff_empty_response_raises(self):
        with patch("src.coder_agent.call_llm", return_value=""):
            with self.assertRaises(ValueError) as ctx:
                self.agent.generate_diff("context", "instruction")
        self.assertIn("empty", str(ctx.exception).lower())

    def test_generate_diff_whitespace_only_response_raises(self):
        with patch("src.coder_agent.call_llm", return_value="   \n\n  "):
            with self.assertRaises(ValueError):
                self.agent.generate_diff("context", "instruction")

    def test_generate_diff_explanatory_text_raises(self):
        explanation = (
            "Here is how you would implement the feature:\n"
            "1. Add a method to ShoppingCart\n"
            "2. Validate the discount percentage\n"
        )
        with patch("src.coder_agent.call_llm", return_value=explanation):
            with self.assertRaises(ValueError) as ctx:
                self.agent.generate_diff("context", "instruction")
        self.assertIn("valid unified git diff", str(ctx.exception))

    def test_generate_diff_malformed_no_file_headers_raises(self):
        malformed = (
            "@@ -10,6 +10,8 @@\n"
            "     def __init__(self):\n"
            "+        self.voucher_code = None\n"
        )
        with patch("src.coder_agent.call_llm", return_value=malformed):
            with self.assertRaises(ValueError) as ctx:
                self.agent.generate_diff("context", "instruction")
        self.assertIn("file headers", str(ctx.exception))

    def test_generate_diff_malformed_no_hunks_raises(self):
        malformed = "--- a/cart.py\n+++ b/cart.py\n"
        with patch("src.coder_agent.call_llm", return_value=malformed):
            with self.assertRaises(ValueError) as ctx:
                self.agent.generate_diff("context", "instruction")
        self.assertIn("hunk", str(ctx.exception).lower())

    def test_generate_diff_llm_exception_propagates(self):
        with patch("src.coder_agent.call_llm", side_effect=RuntimeError("API down")):
            with self.assertRaises(RuntimeError):
                self.agent.generate_diff("context", "instruction")

    def test_generate_diff_multiple_files(self):
        multi_diff = (
            self.VALID_DIFF
            + "--- a/order.py\n"
            + "+++ b/order.py\n"
            + "@@ -5,3 +5,5 @@ class Order:\n"
            + "     def total(self):\n"
            + "+        return self.subtotal\n"
        )
        with patch("src.coder_agent.call_llm", return_value=multi_diff):
            result = self.agent.generate_diff("context", "instruction")
        self.assertIn("cart.py", result)
        self.assertIn("order.py", result)


class TestTestAgent(unittest.TestCase):
    """Focused tests for TestAgent.generate_tests using mocked LLM calls."""

    def setUp(self):
        Config.LLM_PROVIDER = "mock"
        self.agent = TestAgent()

    VALID_PYTEST = (
        "import pytest\n"
        "from cart import ShoppingCart\n"
        "\n"
        "def test_apply_valid_voucher():\n"
        "    cart = ShoppingCart()\n"
        "    cart.add_item(\"Shoes\", 100.0, 1)\n"
        "    cart.apply_voucher(\"SAVE20\", 20.0)\n"
        "    assert cart.get_total() == 80.0\n"
    )

    VALID_UNITTEST = (
        "import unittest\n"
        "from cart import ShoppingCart\n"
        "\n"
        "class TestVoucherFeature(unittest.TestCase):\n"
        "    def test_apply_valid_voucher(self):\n"
        "        cart = ShoppingCart()\n"
        "        cart.add_item(\"Shoes\", 100.0, 1)\n"
        "        cart.apply_voucher(\"SAVE20\", 20.0)\n"
        "        self.assertEqual(cart.get_total(), 80.0)\n"
    )

    def test_generate_tests_returns_valid_pytest(self):
        with patch("src.test_agent.call_llm", return_value=self.VALID_PYTEST):
            result = self.agent.generate_tests("context", "instruction")
        self.assertEqual(result, self.VALID_PYTEST)

    def test_generate_tests_strips_python_fence(self):
        wrapped = "```python\n" + self.VALID_PYTEST + "\n```"
        with patch("src.test_agent.call_llm", return_value=wrapped):
            result = self.agent.generate_tests("context", "instruction")
        self.assertEqual(result, self.VALID_PYTEST)

    def test_generate_tests_strips_py_fence(self):
        wrapped = "```py\n" + self.VALID_PYTEST + "\n```"
        with patch("src.test_agent.call_llm", return_value=wrapped):
            result = self.agent.generate_tests("context", "instruction")
        self.assertEqual(result, self.VALID_PYTEST)

    def test_generate_tests_strips_plain_fence(self):
        wrapped = "```\n" + self.VALID_PYTEST + "\n```"
        with patch("src.test_agent.call_llm", return_value=wrapped):
            result = self.agent.generate_tests("context", "instruction")
        self.assertEqual(result, self.VALID_PYTEST)

    def test_generate_tests_empty_response_raises(self):
        with patch("src.test_agent.call_llm", return_value=""):
            with self.assertRaises(ValueError) as ctx:
                self.agent.generate_tests("context", "instruction")
        self.assertIn("empty", str(ctx.exception).lower())

    def test_generate_tests_whitespace_only_response_raises(self):
        with patch("src.test_agent.call_llm", return_value="   \n\n  "):
            with self.assertRaises(ValueError):
                self.agent.generate_tests("context", "instruction")

    def test_generate_tests_invalid_syntax_raises(self):
        bad_syntax = "def test_foo():\n    this is not valid python!!!"
        with patch("src.test_agent.call_llm", return_value=bad_syntax):
            with self.assertRaises(ValueError) as ctx:
                self.agent.generate_tests("context", "instruction")
        self.assertIn("syntax", str(ctx.exception).lower())

    def test_generate_tests_explanatory_text_raises(self):
        # Valid Python syntax but no test definitions.
        explanation = (
            "# Here is how you would test the feature:\n"
            "# 1. Create a ShoppingCart instance\n"
            "# 2. Call apply_voucher with a valid code\n"
            "# 3. Assert the total is reduced\n"
        )
        with patch("src.test_agent.call_llm", return_value=explanation):
            with self.assertRaises(ValueError) as ctx:
                self.agent.generate_tests("context", "instruction")
        self.assertIn("test code", str(ctx.exception))

    def test_generate_tests_unittest_compatible(self):
        with patch("src.test_agent.call_llm", return_value=self.VALID_UNITTEST):
            result = self.agent.generate_tests("context", "instruction")
        self.assertEqual(result, self.VALID_UNITTEST)

    def test_generate_tests_llm_exception_propagates(self):
        with patch("src.test_agent.call_llm", side_effect=RuntimeError("API down")):
            with self.assertRaises(RuntimeError):
                self.agent.generate_tests("context", "instruction")

    def test_generate_tests_multiple_test_functions(self):
        multi_tests = (
            "import pytest\n"
            "from cart import ShoppingCart\n"
            "\n"
            "def test_apply_valid_voucher():\n"
            "    cart = ShoppingCart()\n"
            "    assert cart.get_total() == 100.0\n"
            "\n"
            "def test_invalid_voucher_raises():\n"
            "    cart = ShoppingCart()\n"
            "    with pytest.raises(ValueError):\n"
            "        cart.apply_voucher(\"\", 10.0)\n"
        )
        with patch("src.test_agent.call_llm", return_value=multi_tests):
            result = self.agent.generate_tests("context", "instruction")
        self.assertIn("test_apply_valid_voucher", result)
        self.assertIn("test_invalid_voucher_raises", result)


if __name__ == "__main__":
    unittest.main()
