"""
Unit tests for the Factory pipeline modules.
Uses dynamic temporary fixtures so no static sample repo is required.
"""

import unittest
import json
import os
import tempfile
import shutil
from unittest.mock import patch
from src.analyzer import CodebaseAnalyzer
from src.variation_controller import VariationController
from src.spec_generator import SpecGenerator
from src.coder_agent import CoderAgent
from src.config import Config
from src.packager import TaskPackager
from src.test_agent import TestAgent
from src.verifier import VerificationResult


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


class TestCoderAgentRepair(unittest.TestCase):
    """Tests for CoderAgent.repair_diff()."""

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

    INVALID_DIFF = "--- a/cart.py\n+++ b/cart.py\n"

    def test_repair_diff_success_on_first_attempt(self):
        with patch("src.coder_agent.call_llm", return_value=self.VALID_DIFF):
            result = self.agent.repair_diff(
                codebase_context="context",
                instruction_md="instruction",
                rejected_diff=self.INVALID_DIFF,
                error_feedback="Missing hunk headers",
                max_retries=3,
            )
        self.assertEqual(result, self.VALID_DIFF)

    def test_repair_diff_success_after_retry(self):
        # First attempt returns invalid, second returns valid.
        with patch(
            "src.coder_agent.call_llm",
            side_effect=[self.INVALID_DIFF, self.VALID_DIFF],
        ):
            result = self.agent.repair_diff(
                codebase_context="context",
                instruction_md="instruction",
                rejected_diff=self.INVALID_DIFF,
                error_feedback="Missing hunk headers",
                max_retries=3,
            )
        self.assertEqual(result, self.VALID_DIFF)

    def test_repair_diff_exhausts_retries(self):
        with patch("src.coder_agent.call_llm", return_value=self.INVALID_DIFF):
            with self.assertRaises(ValueError) as ctx:
                self.agent.repair_diff(
                    codebase_context="context",
                    instruction_md="instruction",
                    rejected_diff=self.INVALID_DIFF,
                    error_feedback="Missing hunk headers",
                    max_retries=2,
                )
        self.assertIn("2 repair attempts", str(ctx.exception))

    def test_repair_diff_llm_exception_propagates(self):
        with patch(
            "src.coder_agent.call_llm",
            side_effect=RuntimeError("API down"),
        ):
            with self.assertRaises(RuntimeError):
                self.agent.repair_diff(
                    codebase_context="context",
                    instruction_md="instruction",
                    rejected_diff=self.INVALID_DIFF,
                    error_feedback="Missing hunk headers",
                    max_retries=3,
                )

    def test_repair_diff_uses_original_context_and_instruction(self):
        captured = {}

        def fake_llm(prompt, system_prompt=""):
            captured["prompt"] = prompt
            return self.VALID_DIFF

        with patch("src.coder_agent.call_llm", side_effect=fake_llm):
            self.agent.repair_diff(
                codebase_context="MY_CONTEXT",
                instruction_md="MY_INSTRUCTION",
                rejected_diff=self.INVALID_DIFF,
                error_feedback="Fix this",
                max_retries=1,
            )
        self.assertIn("MY_CONTEXT", captured["prompt"])
        self.assertIn("MY_INSTRUCTION", captured["prompt"])
        self.assertIn("Fix this", captured["prompt"])
        self.assertIn(self.INVALID_DIFF, captured["prompt"])


class TestTestAgentRepair(unittest.TestCase):
    """Tests for TestAgent.repair_tests()."""

    def setUp(self):
        Config.LLM_PROVIDER = "mock"
        self.agent = TestAgent()

    VALID_TESTS = (
        "import pytest\n"
        "from cart import ShoppingCart\n"
        "\n"
        "def test_apply_valid_voucher():\n"
        "    cart = ShoppingCart()\n"
        "    cart.add_item(\"Shoes\", 100.0, 1)\n"
        "    cart.apply_voucher(\"SAVE20\", 20.0)\n"
        "    assert cart.get_total() == 80.0\n"
    )

    INVALID_TESTS = "# just a comment\n"

    def test_repair_tests_success_on_first_attempt(self):
        with patch("src.test_agent.call_llm", return_value=self.VALID_TESTS):
            result = self.agent.repair_tests(
                codebase_context="context",
                instruction_md="instruction",
                rejected_tests=self.INVALID_TESTS,
                error_feedback="No test definitions found",
                max_retries=3,
            )
        self.assertEqual(result, self.VALID_TESTS)

    def test_repair_tests_success_after_retry(self):
        with patch(
            "src.test_agent.call_llm",
            side_effect=[self.INVALID_TESTS, self.VALID_TESTS],
        ):
            result = self.agent.repair_tests(
                codebase_context="context",
                instruction_md="instruction",
                rejected_tests=self.INVALID_TESTS,
                error_feedback="No test definitions found",
                max_retries=3,
            )
        self.assertEqual(result, self.VALID_TESTS)

    def test_repair_tests_exhausts_retries(self):
        with patch("src.test_agent.call_llm", return_value=self.INVALID_TESTS):
            with self.assertRaises(ValueError) as ctx:
                self.agent.repair_tests(
                    codebase_context="context",
                    instruction_md="instruction",
                    rejected_tests=self.INVALID_TESTS,
                    error_feedback="No test definitions found",
                    max_retries=2,
                )
        self.assertIn("2 repair attempts", str(ctx.exception))

    def test_repair_tests_llm_exception_propagates(self):
        with patch(
            "src.test_agent.call_llm",
            side_effect=RuntimeError("API down"),
        ):
            with self.assertRaises(RuntimeError):
                self.agent.repair_tests(
                    codebase_context="context",
                    instruction_md="instruction",
                    rejected_tests=self.INVALID_TESTS,
                    error_feedback="No test definitions found",
                    max_retries=3,
                )

    def test_repair_tests_uses_original_context_and_instruction(self):
        captured = {}

        def fake_llm(prompt, system_prompt=""):
            captured["prompt"] = prompt
            return self.VALID_TESTS

        with patch("src.test_agent.call_llm", side_effect=fake_llm):
            self.agent.repair_tests(
                codebase_context="MY_CONTEXT",
                instruction_md="MY_INSTRUCTION",
                rejected_tests=self.INVALID_TESTS,
                error_feedback="Fix this",
                max_retries=1,
            )
        self.assertIn("MY_CONTEXT", captured["prompt"])
        self.assertIn("MY_INSTRUCTION", captured["prompt"])
        self.assertIn("Fix this", captured["prompt"])
        self.assertIn(self.INVALID_TESTS, captured["prompt"])


class TestTaskPackager(unittest.TestCase):
    """Tests for TaskPackager.package()."""

    def setUp(self):
        Config.LLM_PROVIDER = "mock"
        self.temp_dir = tempfile.mkdtemp()
        self.packager = TaskPackager(self.temp_dir)

        self.verification = VerificationResult()
        self.verification.step1_baseline_pass = True
        self.verification.step2_negative_fail = True
        self.verification.step3_positive_pass = True
        self.verification.patch_applied_cleanly = True
        self.verification.logs = {}

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_package_creates_directory_structure(self):
        result_dir = self.packager.package(
            task_id="task_001",
            repo_name="myrepo",
            category="new_feature",
            instruction_md="# Feature\nAdd foo.",
            solution_diff="--- a/foo.py\n+++ b/foo.py\n@@ -1,0 +1 @@\n+print('hi')\n",
            test_code="def test_foo():\n    assert True\n",
            verification=self.verification,
        )
        self.assertTrue(os.path.isdir(result_dir))
        self.assertTrue(os.path.isfile(os.path.join(result_dir, "instruction.md")))
        self.assertTrue(os.path.isfile(os.path.join(result_dir, "solution", "solution.diff")))
        self.assertTrue(os.path.isfile(os.path.join(result_dir, "tests", "test_feature_patch.py")))
        self.assertTrue(os.path.isfile(os.path.join(result_dir, "task.toml")))
        self.assertTrue(os.path.isfile(os.path.join(result_dir, "validation_report.json")))

    def test_package_writes_correct_contents(self):
        self.packager.package(
            task_id="task_001",
            repo_name="myrepo",
            category="new_feature",
            instruction_md="# Feature\nAdd foo.",
            solution_diff="--- a/foo.py\n+++ b/foo.py\n@@ -1,0 +1 @@\n+print('hi')\n",
            test_code="def test_foo():\n    assert True\n",
            verification=self.verification,
        )
        with open(os.path.join(self.temp_dir, "task_001", "instruction.md")) as f:
            self.assertEqual(f.read(), "# Feature\nAdd foo.")
        with open(os.path.join(self.temp_dir, "task_001", "solution", "solution.diff")) as f:
            self.assertEqual(f.read(), "--- a/foo.py\n+++ b/foo.py\n@@ -1,0 +1 @@\n+print('hi')\n")
        with open(os.path.join(self.temp_dir, "task_001", "tests", "test_feature_patch.py")) as f:
            self.assertEqual(f.read(), "def test_foo():\n    assert True\n")
        with open(os.path.join(self.temp_dir, "task_001", "task.toml")) as f:
            toml = f.read()
            self.assertIn('task_id = "task_001"', toml)
            self.assertIn('repo_name = "myrepo"', toml)
            self.assertIn('category = "new_feature"', toml)
        with open(os.path.join(self.temp_dir, "task_001", "validation_report.json")) as f:
            report = json.load(f)
            self.assertEqual(report["task_id"], "task_001")
            self.assertEqual(report["status"], "VERIFIED")
            self.assertTrue(report["checks"]["step1_baseline_pass"])
            self.assertTrue(report["checks"]["step2_negative_fail"])
            self.assertTrue(report["checks"]["step3_positive_pass"])

    def test_package_validation_rejects_empty_task_id(self):
        with self.assertRaises(ValueError):
            self.packager.package(
                task_id="",
                repo_name="myrepo",
                category="new_feature",
                instruction_md="# Feature",
                solution_diff="--- a/foo.py\n+++ b/foo.py\n",
                test_code="def test_foo():\n    pass\n",
                verification=self.verification,
            )

    def test_package_validation_rejects_empty_instruction_md(self):
        with self.assertRaises(ValueError):
            self.packager.package(
                task_id="task_001",
                repo_name="myrepo",
                category="new_feature",
                instruction_md="",
                solution_diff="--- a/foo.py\n+++ b/foo.py\n",
                test_code="def test_foo():\n    pass\n",
                verification=self.verification,
            )

    def test_package_validation_rejects_empty_solution_diff(self):
        with self.assertRaises(ValueError):
            self.packager.package(
                task_id="task_001",
                repo_name="myrepo",
                category="new_feature",
                instruction_md="# Feature",
                solution_diff="",
                test_code="def test_foo():\n    pass\n",
                verification=self.verification,
            )

    def test_package_validation_rejects_empty_test_code(self):
        with self.assertRaises(ValueError):
            self.packager.package(
                task_id="task_001",
                repo_name="myrepo",
                category="new_feature",
                instruction_md="# Feature",
                solution_diff="--- a/foo.py\n+++ b/foo.py\n",
                test_code="",
                verification=self.verification,
            )

    def test_package_validation_rejects_wrong_verification_type(self):
        with self.assertRaises(TypeError):
            self.packager.package(
                task_id="task_001",
                repo_name="myrepo",
                category="new_feature",
                instruction_md="# Feature",
                solution_diff="--- a/foo.py\n+++ b/foo.py\n",
                test_code="def test_foo():\n    pass\n",
                verification={"not": "a VerificationResult"},
            )

    def test_package_existing_directory_raises_without_overwrite(self):
        self.packager.package(
            task_id="task_001",
            repo_name="myrepo",
            category="new_feature",
            instruction_md="# Feature",
            solution_diff="--- a/foo.py\n+++ b/foo.py\n",
            test_code="def test_foo():\n    pass\n",
            verification=self.verification,
        )
        with self.assertRaises(FileExistsError):
            self.packager.package(
                task_id="task_001",
                repo_name="myrepo",
                category="new_feature",
                instruction_md="# Feature v2",
                solution_diff="--- a/foo.py\n+++ b/foo.py\n",
                test_code="def test_foo():\n    pass\n",
                verification=self.verification,
            )

    def test_package_overwrite_replaces_existing(self):
        first_dir = self.packager.package(
            task_id="task_001",
            repo_name="myrepo",
            category="new_feature",
            instruction_md="# Feature v1",
            solution_diff="--- a/foo.py\n+++ b/foo.py\n",
            test_code="def test_foo():\n    pass\n",
            verification=self.verification,
        )
        # Verify first content
        with open(os.path.join(first_dir, "instruction.md")) as f:
            self.assertEqual(f.read(), "# Feature v1")

        # Overwrite
        second_dir = self.packager.package(
            task_id="task_001",
            repo_name="myrepo",
            category="new_feature",
            instruction_md="# Feature v2",
            solution_diff="--- a/bar.py\n+++ b/bar.py\n",
            test_code="def test_bar():\n    pass\n",
            verification=self.verification,
            overwrite=True,
        )
        self.assertEqual(first_dir, second_dir)
        with open(os.path.join(second_dir, "instruction.md")) as f:
            self.assertEqual(f.read(), "# Feature v2")


if __name__ == "__main__":
    unittest.main()
