import unittest

from src.variation_controller import VariationController


class VariationControllerTests(unittest.TestCase):
    def test_four_tasks_cover_all_archetypes(self):
        vc = VariationController()
        picks = vc.select_category(4)
        self.assertEqual(len({p["id"] for p in picks}), 4)
        self.assertEqual(vc.get_variation_report()["diversity_score"], 1.0)

    def test_two_different_tasks_score_full_diversity(self):
        vc = VariationController()
        vc.select_category(2)
        report = vc.get_variation_report()
        self.assertEqual(report["diversity_score"], 1.0)      # best possible for 2 tasks
        self.assertEqual(report["taxonomy_coverage"], 0.5)    # 2 of 4 archetypes used

    def test_successive_calls_stay_balanced(self):
        vc = VariationController()
        ids = [vc.select_category(1)[0]["id"] for _ in range(4)]
        self.assertEqual(len(set(ids)), 4)                    # old code: new_feature x4

    def test_repeated_archetypes_get_different_variants(self):
        vc = VariationController()
        picks = vc.select_category(8)
        by_archetype = {}
        for p in picks:
            by_archetype.setdefault(p["id"], []).append(p["variant"])
        for variants in by_archetype.values():
            self.assertEqual(len(variants), 2)
            self.assertEqual(len(set(variants)), 2)           # not the same task twice
        self.assertEqual(vc.get_variation_report()["variant_diversity"], 1.0)

    def test_all_same_archetype_scores_low(self):
        vc = VariationController()
        variant = vc.VARIANTS["new_feature"][0]
        vc._history = ["new_feature"] * 4
        vc._variant_history = [("new_feature", variant)] * 4
        report = vc.get_variation_report()
        self.assertEqual(report["diversity_score"], 0.25)
        self.assertEqual(report["balance"], 0.25)
        self.assertEqual(report["variant_diversity"], 0.25)

    def test_selection_is_deterministic(self):
        a = VariationController().select_category(6)
        b = VariationController().select_category(6)
        self.assertEqual(a, b)

    def test_original_keys_are_kept(self):
        pick = VariationController().select_category(1)[0]
        for key in ("id", "name", "description"):
            self.assertIn(key, pick)
        self.assertIn("variant", pick)

    def test_report_keeps_original_keys(self):
        vc = VariationController()
        vc.select_category(4)
        report = vc.get_variation_report()
        for key in ("total_tasks", "unique_archetypes", "diversity_score", "distribution"):
            self.assertIn(key, report)

    def test_zero_count_and_empty_report(self):
        vc = VariationController()
        self.assertEqual(vc.select_category(0), [])
        self.assertEqual(vc.get_variation_report()["total_tasks"], 0)

    def test_reset_clears_history(self):
        vc = VariationController()
        vc.select_category(3)
        vc.reset()
        self.assertEqual(vc.get_variation_report()["total_tasks"], 0)
        self.assertEqual(vc.select_category(1)[0]["id"], "new_feature")


if __name__ == "__main__":
    unittest.main()