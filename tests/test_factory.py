"""
Unit tests for the Factory pipeline modules.
"""

import unittest
import os
from src.analyzer import CodebaseAnalyzer
from src.variation_controller import VariationController
from src.spec_generator import SpecGenerator
from src.config import Config


class TestFactoryComponents(unittest.TestCase):
    def setUp(self):
        Config.LLM_PROVIDER = "mock"
        self.sample_repo = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "sample_repo")
        )

    def test_analyzer_scan(self):
        analyzer = CodebaseAnalyzer(self.sample_repo)
        data = analyzer.scan()
        self.assertIn("cart.py", data["files"])
        self.assertGreater(len(data["classes"]), 0)
        class_names = [c["name"] for c in data["classes"]]
        self.assertIn("ShoppingCart", class_names)
        self.assertIn("Item", class_names)

    def test_variation_controller(self):
        vc = VariationController()
        cats = vc.select_category(count=4)
        self.assertEqual(len(cats), 4)
        report = vc.get_variation_report()
        self.assertEqual(report["total_tasks"], 4)
        self.assertGreaterEqual(report["diversity_score"], 0.75)

    def test_spec_generator_mock(self):
        analyzer = CodebaseAnalyzer(self.sample_repo)
        ctx = analyzer.get_context_prompt()
        gen = SpecGenerator()
        spec = gen.generate(codebase_context=ctx, category="new_feature")
        self.assertIn("Specification & Acceptance Criteria", spec)
        self.assertIn("Edge Cases", spec)


if __name__ == "__main__":
    unittest.main()
