"""
Unit tests for the Factory pipeline modules.
Uses dynamic temporary fixtures so no static sample repo is required.
"""

import unittest
import os
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

    def test_spec_leakage_detector_and_sanitizer(self):
        bad_spec = "Please modify calculate_discount around line 45 to check if self._active is True."
        is_valid, warnings = SpecGenerator.validate_leakage(bad_spec)
        self.assertFalse(is_valid)
        self.assertGreater(len(warnings), 0)

        cleaned = SpecGenerator.sanitize_spec(bad_spec)
        self.assertNotIn("line 45", cleaned)

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
