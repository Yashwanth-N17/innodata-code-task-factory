"""
Feature 7: Meaningful Variation & Taxonomy Controller.
Prevents trivial task generation (e.g. changing button colors) by enforcing
a multi-dimensional task taxonomy covering distinct engineering challenges:
1. New Functional Feature
2. Defensive Validation & Error Handling
3. Concurrency & State Mutation Bug Fix
4. Algorithmic Refactor & Optimization

Two levels of variation:
  * archetype - the kind of engineering work (the 4 categories above)
  * variant   - a concrete sub-type inside an archetype, so that repeating an
                archetype still produces a meaningfully different task

Selection is deterministic (repeatable): always the least-used archetype first,
then the least-used variant inside it, ties broken by declaration order. History
persists across select_category() calls, so batches stay balanced.

Input: Requested batch count of tasks.
Output: Balanced category list + diversity metrics report.
"""

import math
from typing import Any, Dict, List, Tuple


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

    # Concrete, repo-agnostic sub-types per archetype (what makes repeats genuinely different).
    VARIANTS: Dict[str, List[str]] = {
        "new_feature": [
            "Add a new public method to an existing class",
            "Add a new standalone helper function with a strict input/output contract",
            "Add a new optional parameter to an existing public method, keeping defaults backward compatible",
        ],
        "validation_edge_cases": [
            "Reject None and wrong-type inputs with explicit exceptions",
            "Enforce numeric bounds and handle empty-collection edge cases",
            "Validate string input (blank, whitespace-only, malformed values)",
        ],
        "state_bug_fix": [
            "Stop a method returning a reference to internal mutable state (return a copy)",
            "Fix a collection being modified while it is being iterated",
            "Fix shared mutable default arguments or state leaking between instances",
        ],
        "performance_refactor": [
            "Replace repeated linear scans with an index (dict or set) for lookups",
            "Cache or precompute a repeated expensive computation",
            "Replace a nested-loop comparison with a single-pass or sorted approach",
        ],
    }

    def __init__(self):
        self._history: List[str] = []                       # archetype ids, in order selected
        self._variant_history: List[Tuple[str, str]] = []   # (archetype id, variant)

    def reset(self) -> None:
        """Forget previous selections (e.g. when starting a new repository)."""
        self._history = []
        self._variant_history = []

    def select_category(self, count: int = 1, language: str = "python") -> List[Dict[str, str]]:
        """Select a balanced set of diverse categories for task generation.
        Each returned dict keeps the original keys (id, name, description) and adds
        'variant': the concrete sub-type this task should focus on."""
        selected: List[Dict[str, str]] = []
        for _ in range(max(0, count)):
            category = self._least_used_category()
            variant = self._least_used_variant(category["id"])
            entry = dict(category)
            entry["variant"] = self.adapt_variant_for_language(variant, language)
            selected.append(entry)
            self._history.append(category["id"])
            self._variant_history.append((category["id"], variant))
        return selected

    @staticmethod
    def adapt_variant_for_language(variant: str, language: str = "python") -> str:
        """Translate Python-centric terms in variants to idiomatic equivalents for other languages."""
        lang = (language or "python").lower()
        if lang == "typescript":
            return (
                variant.replace("None", "null/undefined")
                .replace("(dict or set)", "(Map or Set)")
                .replace("mutable default arguments", "shared mutable module/closure state")
            )
        if lang == "cpp":
            return (
                variant.replace("None and wrong-type", "null/empty and invalid")
                .replace("explicit exceptions", "explicit std::invalid_argument / std::out_of_range exceptions")
                .replace("(dict or set)", "(std::unordered_map or std::unordered_set)")
                .replace("mutable default arguments", "static/shared mutable state")
            )
        if lang in ("go", "rust", "java"):
            return (
                variant.replace("None", "nil/null/None")
                .replace("(dict or set)", "(hash map or hash set)")
            )
        return variant

    def _least_used_category(self) -> Dict[str, str]:
        # min() returns the first minimum, so ties follow TAXONOMY order.
        return min(self.TAXONOMY, key=lambda c: self._history.count(c["id"]))

    def _least_used_variant(self, category_id: str) -> str:
        variants = self.VARIANTS.get(category_id) or [""]
        return min(variants, key=lambda v: self._variant_history.count((category_id, v)))

    def get_variation_report(self) -> Dict[str, Any]:
        """Generate diversity metrics and a breakdown for judging evidence.

        diversity_score    archetypes used / archetypes that COULD be used for this many
                           tasks. 1.0 means maximally diverse (2 tasks in 2 archetypes = 1.0).
        taxonomy_coverage  archetypes used / all archetypes (the stricter, absolute view).
        variant_diversity  distinct (archetype, variant) pairs / the most possible.
        balance            how close the most-repeated archetype is to the ideal even split.
        """
        total = len(self._history)
        if total == 0:
            return {
                "total_tasks": 0,
                "unique_archetypes": 0,
                "unique_variants": 0,
                "diversity_score": 0.0,
                "taxonomy_coverage": 0.0,
                "variant_diversity": 0.0,
                "balance": 0.0,
                "distribution": {},
                "variant_distribution": {},
            }

        counts: Dict[str, int] = {}
        for cat_id in self._history:
            counts[cat_id] = counts.get(cat_id, 0) + 1

        variant_counts: Dict[str, int] = {}
        for cat_id, variant in self._variant_history:
            key = f"{cat_id}: {variant}"
            variant_counts[key] = variant_counts.get(key, 0) + 1

        n_categories = len(self.TAXONOMY)
        n_variants_possible = sum(len(v) for v in self.VARIANTS.values()) or n_categories
        unique_cats = len(counts)
        unique_variants = len(variant_counts)

        ideal_max_count = math.ceil(total / n_categories)
        return {
            "total_tasks": total,
            "unique_archetypes": unique_cats,
            "unique_variants": unique_variants,
            "diversity_score": round(unique_cats / min(total, n_categories), 2),
            "taxonomy_coverage": round(unique_cats / n_categories, 2),
            "variant_diversity": round(unique_variants / min(total, n_variants_possible), 2),
            "balance": round(min(1.0, ideal_max_count / max(counts.values())), 2),
            "distribution": counts,
            "variant_distribution": variant_counts,
        }
