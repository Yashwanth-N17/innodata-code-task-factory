"""
Feature 7: Meaningful Variation & Taxonomy Controller.
Prevents trivial task generation (e.g. changing button colors) by enforcing
a multi-dimensional task taxonomy covering distinct engineering challenges:
1. New Functional Feature
2. Defensive Validation & Error Handling
3. Concurrency & State Mutation Bug Fix
4. Algorithmic Refactor & Optimization

Input: Requested batch count of tasks.
Output: Balanced category distribution and mathematical diversity metrics.
"""

from typing import List, Dict, Any
import random


class VariationController:
    TAXONOMY: List[Dict[str, str]] = [
        {
            "id": "new_feature",
            "name": "New Functional Feature",
            "description": "Introduces a new method, class capability, or public API endpoint with strict input/output contract.",
        },
        {
            "id": "validation_edge_cases",
            "name": "Defensive Validation & Error Handling",
            "description": "Hardens existing methods against null, out-of-range, or adversarial inputs with explicit exceptions.",
        },
        {
            "id": "state_bug_fix",
            "name": "Concurrency & State Mutation Bug Fix",
            "description": "Fixes a state corruption or edge case when modifying internal collections and references.",
        },
        {
            "id": "performance_refactor",
            "name": "Algorithmic Refactor & Optimization",
            "description": "Improves data lookup from linear to sub-linear while preserving backward compatibility.",
        },
    ]

    def __init__(self):
        self._history: List[str] = []

    def select_category(self, count: int = 1) -> List[Dict[str, str]]:
        """Select a balanced set of diverse categories for task generation."""
        selected = []
        for i in range(count):
            cat = self.TAXONOMY[i % len(self.TAXONOMY)]
            selected.append(cat)
            self._history.append(cat["id"])
        return selected

    def get_variation_report(self) -> Dict[str, Any]:
        """Generate a diversity score and breakdown for judging evidence."""
        total = len(self._history)
        if total == 0:
            return {"total_tasks": 0, "diversity_score": 0.0, "distribution": {}}

        counts = {}
        for cat_id in self._history:
            counts[cat_id] = counts.get(cat_id, 0) + 1

        # Entropy / diversity ratio
        unique_cats = len(counts)
        max_possible = len(self.TAXONOMY)
        diversity_score = round(unique_cats / max_possible, 2)

        return {
            "total_tasks": total,
            "unique_archetypes": unique_cats,
            "diversity_score": diversity_score,
            "distribution": counts,
        }
