"""
Unit tests for the Factory pipeline modules.
Uses dynamic temporary fixtures so no static sample repo is required.
"""

import unittest
import os
import re
import tempfile
import shutil
from src.analyzer import CodebaseAnalyzer
from src.variation_controller import VariationController
from src.spec_generator import SpecGenerator
from src.config import Config


class TestFactoryComponents(unittest.TestCase):
    def setUp(self):
        Config.LLM_PROVIDER = "mock"
        self.temp_dir = tempfile.mkdtemp()

        # 1. Primary service module with classes, inheritance, type annotations, and methods
        dummy_file = os.path.join(self.temp_dir, "service.py")
        with open(dummy_file, "w", encoding="utf-8") as f:
            f.write('''
class PaymentProcessor:
    """Processes online payments."""
    def __init__(self, currency="USD"):
        self.currency = currency

    def charge(self, amount: float) -> bool:
        return amount > 0

    def _verify_card(self, number: str) -> bool:
        return len(number) == 16


class StripeProcessor(PaymentProcessor):
    """Stripe payment processor subclass."""
    def refund(self, txn_id: str, amount: float = 0.0) -> bool:
        return True


def process_webhook(payload: dict, secret: str = "default_secret") -> bool:
    """Process incoming webhook."""
    return True


def _internal_digest(raw_data: bytes) -> str:
    return "sha256"
''')

        # 2. Source file with 'test' in filename (must NOT be misclassified as test suite)
        test_agent_file = os.path.join(self.temp_dir, "test_agent.py")
        with open(test_agent_file, "w", encoding="utf-8") as f:
            f.write('''
class TestAgent:
    """Synthesizes acceptance test suites."""
    def __init__(self):
        pass

    def generate_tests(self, spec: str) -> str:
        return spec
''')

        # 3. Source file with substring 'test' (must NOT be misclassified)
        speedtest_file = os.path.join(self.temp_dir, "speedtest.py")
        with open(speedtest_file, "w", encoding="utf-8") as f:
            f.write('''
class SpeedMonitor:
    def ping(self) -> int:
        return 10
''')

        # 4. Actual test directory with real test suite
        test_dir = os.path.join(self.temp_dir, "tests")
        os.makedirs(test_dir, exist_ok=True)
        suite_file = os.path.join(test_dir, "test_service.py")
        with open(suite_file, "w", encoding="utf-8") as f:
            f.write('''
import unittest

class TestPayment(unittest.TestCase):
    def test_charge(self):
        self.assertTrue(True)
''')

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_analyzer_scan(self):
        analyzer = CodebaseAnalyzer(self.temp_dir)
        data = analyzer.scan()

        # 1. Source file vs test suite classification
        self.assertIn("service.py", data["files"])
        self.assertIn("test_agent.py", data["files"])
        self.assertIn("speedtest.py", data["files"])
        self.assertNotIn("test_agent.py", data["existing_tests"])
        self.assertNotIn("speedtest.py", data["existing_tests"])

        test_suite_rel = os.path.join("tests", "test_service.py")
        self.assertIn(test_suite_rel, data["existing_tests"])
        self.assertNotIn(test_suite_rel, data["files"])

        # 2. Classes and Base Classes
        class_names = [c["name"] for c in data["classes"]]
        self.assertIn("PaymentProcessor", class_names)
        self.assertIn("StripeProcessor", class_names)
        self.assertIn("TestAgent", class_names)

        p_cls = [c for c in data["classes"] if c["name"] == "PaymentProcessor"][0]
        self.assertEqual(p_cls["bases"], [])

        s_cls = [c for c in data["classes"] if c["name"] == "StripeProcessor"][0]
        self.assertEqual(s_cls["bases"], ["PaymentProcessor"])

        # 3. Method Signatures, Types, and Public vs Private Separation
        # PaymentProcessor: charge is public, __init__ and _verify_card are private
        public_sigs = [str(m) for m in p_cls["public_methods"]]
        private_sigs = [str(m) for m in p_cls["private_methods"]]

        self.assertTrue(any("charge" in s and "amount: float" in s and "-> bool" in s for s in public_sigs))
        self.assertTrue(any("__init__" in s for s in private_sigs))
        self.assertTrue(any("_verify_card" in s and "number: str" in s for s in private_sigs))

        # Backward compatibility check: methods contains method names or signatures
        self.assertTrue(any("charge" in m for m in p_cls["methods"]))
        self.assertTrue(any("__init__" in m for m in p_cls["methods"]))

        # Rich metadata check on method objects
        charge_m = [m for m in p_cls["public_methods"] if "charge" in m][0]
        self.assertEqual(charge_m["name"], "charge")
        self.assertEqual(charge_m["return_type"], "bool")
        self.assertEqual(charge_m["params"], ["self", "amount: float"])
        self.assertFalse(charge_m["is_private"])

        # StripeProcessor public methods
        s_public_sigs = [str(m) for m in s_cls["public_methods"]]
        self.assertTrue(any("refund" in s and "amount: float = 0.0" in s for s in s_public_sigs))

        # 4. Class Hierarchy Mapping
        self.assertIn("class_hierarchy", data)
        self.assertEqual(data["class_hierarchy"]["PaymentProcessor"], [])
        self.assertEqual(data["class_hierarchy"]["StripeProcessor"], ["PaymentProcessor"])

        # 5. Functions & Type Signatures
        fn_names = [f["name"] for f in data["functions"]]
        self.assertIn("process_webhook", fn_names)
        self.assertIn("_internal_digest", fn_names)

        pub_fn = [f for f in data["functions"] if f["name"] == "process_webhook"][0]
        self.assertFalse(pub_fn["is_private"])
        self.assertEqual(pub_fn["return_type"], "bool")
        self.assertTrue(any("payload: dict" in p for p in pub_fn["params"]))

        priv_fn = [f for f in data["functions"] if f["name"] == "_internal_digest"][0]
        self.assertTrue(priv_fn["is_private"])
        self.assertEqual(priv_fn["return_type"], "str")

    def test_analyzer_test_file_classification(self):
        """Verify test classification eliminates false positives and identifies real tests."""
        # Add a standalone test script outside test directories
        standalone_test = os.path.join(self.temp_dir, "test_standalone.py")
        with open(standalone_test, "w", encoding="utf-8") as f:
            f.write('''
def test_standalone_execution():
    assert True
''')

        analyzer = CodebaseAnalyzer(self.temp_dir)
        data = analyzer.scan()

        # Standalone test file defines test_ function -> must be classified as test suite
        self.assertIn("test_standalone.py", data["existing_tests"])
        self.assertNotIn("test_standalone.py", data["files"])

        # test_agent.py has class TestAgent but no test methods -> must remain source file
        self.assertIn("test_agent.py", data["files"])
        self.assertNotIn("test_agent.py", data["existing_tests"])

        # speedtest.py has substring 'test' -> must remain source file
        self.assertIn("speedtest.py", data["files"])
        self.assertNotIn("speedtest.py", data["existing_tests"])

    def test_analyzer_context_prompt_formatting(self):
        """Verify context prompt format includes enriched metadata and matches mock regexes."""
        analyzer = CodebaseAnalyzer(self.temp_dir)
        prompt = analyzer.get_context_prompt()

        # Must report repository and files
        self.assertIn("Repository:", prompt)
        self.assertIn("Source Files:", prompt)
        self.assertIn("service.py", prompt)
        self.assertIn("test_agent.py", prompt)

        # Regex compatibility with src/config.py mock detection
        file_match = re.search(r"in ([a-zA-Z0-9_\-/]+\.py)", prompt)
        self.assertIsNotNone(file_match)

        cls_match = re.search(r"class ([a-zA-Z0-9_]+)", prompt)
        self.assertIsNotNone(cls_match)

        # Regex match for class line format: - class (\w+) (in ([^)]+)): methods=(.+)
        class_line_match = re.search(r"- class (\w+) \(in ([^)]+)\): methods=(.+)", prompt)
        self.assertIsNotNone(class_line_match)

        # Enriched metadata sections
        self.assertIn("Public Methods:", prompt)
        self.assertIn("Internal Methods:", prompt)
        self.assertIn("Base Classes: PaymentProcessor", prompt)
        self.assertIn("Public Functions:", prompt)
        self.assertIn("Internal Functions:", prompt)

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
        self.assertIn("Business Requirements", spec)
        self.assertIn("Input & Output Schema", spec)
        self.assertIn("Specification & Acceptance Criteria", spec)
        self.assertIn("Edge Cases", spec)
        cases = gen.extract_edge_cases(spec)
        self.assertGreaterEqual(len(cases), 3)

        # Structural validation check
        is_valid, issues = gen.validate_structure(spec)
        self.assertTrue(is_valid, f"Structure validation failed: {issues}")

    def test_spec_leakage_detector_and_sanitizer(self):
        bad_spec = (
            "Please modify calculate_discount around line 45 to check if self._active is True. "
            "See service.py:42 and line 99 for details."
        )
        is_valid, warnings = SpecGenerator.validate_leakage(bad_spec)
        self.assertFalse(is_valid)
        self.assertGreater(len(warnings), 0)

        warn_text = " ".join(warnings)
        self.assertTrue(any("line 45" in warn_text or "line" in warn_text for _ in [1]))
        self.assertIn("service.py:42", warn_text)
        self.assertIn("self._active", warn_text)

        cleaned = SpecGenerator.sanitize_spec(bad_spec)
        self.assertNotIn("line 45", cleaned)
        self.assertNotIn("line 99", cleaned)
        self.assertNotIn(":42", cleaned)
        self.assertNotIn("self._active", cleaned)
        self.assertIn("[internal]", cleaned)

        # Invariant check: sanitized spec passes leakage audit
        is_valid_after, remaining_warnings = SpecGenerator.validate_leakage(cleaned)
        self.assertTrue(is_valid_after, f"Cleaned spec failed validation: {remaining_warnings}")

        # Test additional line formats and private method calls
        bad_spec_2 = "Refer to L42 and lines 10-20. Call _helper(x) on self._count."
        valid_2, warns_2 = SpecGenerator.validate_leakage(bad_spec_2)
        self.assertFalse(valid_2)
        cleaned_2 = SpecGenerator.sanitize_spec(bad_spec_2)
        self.assertNotIn("L42", cleaned_2)
        self.assertNotIn("lines 10-20", cleaned_2)
        self.assertNotIn("_helper", cleaned_2)
        self.assertNotIn("self._count", cleaned_2)
        valid_cleaned_2, warns_cleaned_2 = SpecGenerator.validate_leakage(cleaned_2)
        self.assertTrue(valid_cleaned_2, f"Cleaned spec 2 failed: {warns_cleaned_2}")

        # Test nested and chained private attributes
        bad = "Check self._sub._prop and modify line 42."
        cleaned = SpecGenerator.sanitize_spec(bad)
        self.assertNotIn("_prop", cleaned)
        self.assertNotIn("_sub", cleaned)
        is_valid, _ = SpecGenerator.validate_leakage(cleaned)
        self.assertTrue(is_valid)

    def test_coder_and_test_agent_self_healing(self):
        from src.coder_agent import CoderAgent
        from src.test_agent import TestAgent
        coder = CoderAgent()
        tester = TestAgent()

        # Verify repair methods exist and return strings
        diff = coder.repair_diff(codebase_context="ctx", instruction_md="spec", previous_diff="diff", error_message="err")
        self.assertTrue(isinstance(diff, str))

        tests = tester.repair_tests(codebase_context="ctx", instruction_md="spec", previous_tests="tests", error_message="err")
        self.assertTrue(isinstance(tests, str))


if __name__ == "__main__":
    unittest.main()
