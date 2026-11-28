"""
Controlled pipeline validation using local fixtures (no network access).

Exercises the verification pipeline end-to-end using Python test fixtures:
- Valid baseline (PASS)
- Patch that fixes a failing acceptance test (FAIL → PASS)
- Patch that does not fix the failure (FAIL → FAIL)
- Patch that breaks an existing passing test (PASS → FAIL)
- Baseline that cannot execute (ERROR)
- Missing test runner (ERROR)
- Patch that cannot apply (PATCH FAIL)

Verifies that environment/setup failures are never reported as successful verification.
"""

import json
import os
import shutil
import tempfile
import unittest
from unittest.mock import patch

from src.languages import LanguageRegistry
from src.verifier import SandboxVerifier


def _write(path, content):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


class PipelineValidationTests(unittest.TestCase):
    """End-to-end verification pipeline validation using controlled Python fixtures."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.repo = os.path.join(self.tmp, "repo")
        os.makedirs(self.repo)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _make_python_repo(self, source_content, test_content):
        """Create a minimal Python repo with a source file and existing test."""
        _write(os.path.join(self.repo, "mymod.py"), source_content)
        _write(os.path.join(self.repo, "tests", "test_existing.py"), test_content)
        # Make tests dir importable
        _write(os.path.join(self.repo, "tests", "__init__.py"), "")

    def _make_verifier(self, sandbox="local"):
        adapter = LanguageRegistry.get_adapter(self.repo, language="python")
        return SandboxVerifier(
            self.repo,
            timeout=30,
            adapter=adapter,
            sandbox=sandbox,
        )

    def test_valid_baseline_passes(self):
        """Step 1: Existing tests pass on the original repo."""
        self._make_python_repo(
            "def add(a, b):\n    return a + b\n",
            "from mymod import add\n\ndef test_add():\n    assert add(1, 2) == 3\n",
        )
        verifier = self._make_verifier()
        result = verifier.verify(
            solution_diff="",
            test_code="",
        )
        # Empty diff + empty test code: baseline should PASS (existing tests pass)
        self.assertTrue(result.step1_baseline_pass, f"baseline failed: {result.logs.get('step1_baseline')}")

    def test_patch_fixes_failing_test(self):
        """Step 2→3: New test fails on original, passes after patch fixes source.

        Baseline passes (existing tests OK on original source).
        New test fails on original source (wrong expected value).
        Patch fixes the function so new test passes, existing tests still pass.
        """
        self._make_python_repo(
            "def get_a():\n    return 1\n\ndef get_b():\n    return 2\n",
            "from mymod import get_a\n\ndef test_get_a():\n    assert get_a() == 1\n",
        )
        verifier = self._make_verifier()

        # New test that checks get_b returns 100 (fails on original: get_b() == 2)
        test_code = (
            "from mymod import get_a, get_b\n"
            "\n"
            "def test_get_a():\n"
            "    assert get_a() == 1\n"
            "def test_get_b():\n"
            "    assert get_b() == 100  # fails on original (get_b() == 2)\n"
        )

        # Solution diff: change get_b to return 100
        solution_diff = """--- a/mymod.py
+++ b/mymod.py
@@ -3 +3 @@
 def get_b():
-    return 2
+    return 100
"""

        result = verifier.verify(solution_diff=solution_diff, test_code=test_code)

        # Step 1: baseline must pass (existing tests on original source)
        self.assertTrue(
            result.step1_baseline_pass,
            f"baseline should pass: {result.logs.get('step1_baseline')}",
        )
        # Step 2: negative check - new test fails on original (get_b() == 2, not 100)
        self.assertTrue(
            result.step2_negative_fail,
            f"negative check should fail: {result.logs.get('step2_negative_check')}",
        )
        # Step 3: positive check - after patch, all tests pass
        self.assertTrue(
            result.step3_positive_pass,
            f"positive check should pass: {result.logs.get('step3_positive_check')}",
        )
        self.assertTrue(result.is_valid, f"task should be valid: {result.logs}")

    def test_patch_does_not_fix_failure(self):
        """Step 3: Patch applied but new test still fails → FAIL."""
        shutil.rmtree(self.repo)
        os.makedirs(self.repo)
        self._make_python_repo(
            "def add(a, b):\n    return a + b\n",
            "from mymod import add\n\ndef test_add():\n    assert add(1, 2) == 3\n",
        )
        verifier = self._make_verifier()

        test_code = (
            "from mymod import add\n"
            "\n"
            "def test_add_sub():\n"
            "    assert add(5, 3) == 2  # fails even after any reasonable patch\n"
        )

        # A no-op patch that doesn't fix anything
        solution_diff = """--- a/mymod.py
+++ b/mymod.py
@@ -1 +1 @@
-def add(a, b):
-    return a + b
+def add(a, b):
-    return a + b  # no real change
"""

        result = verifier.verify(solution_diff=solution_diff, test_code=test_code)
        self.assertFalse(
            result.step3_positive_pass,
            f"positive check should fail when patch doesn't fix: {result.logs.get('step3_positive_check')}",
        )

    def test_patch_breaks_existing_passing_test(self):
        """Step 3: Patch breaks an existing test → pass_to_pass fails."""
        shutil.rmtree(self.repo)
        os.makedirs(self.repo)
        self._make_python_repo(
            "def add(a, b):\n    return a + b\n",
            "from mymod import add\n\ndef test_add():\n    assert add(1, 2) == 3\n",
        )
        verifier = self._make_verifier()

        test_code = (
            "from mymod import add\n"
            "\n"
            "def test_add_new():\n"
            "    assert add(1, 2) == 3\n"
        )

        # Patch that breaks add
        solution_diff = """--- a/mymod.py
+++ b/mymod.py
@@ -1 +1 @@
-def add(a, b):
-    return a + b
+def add(a, b):
+    return a - b
"""

        result = verifier.verify(solution_diff=solution_diff, test_code=test_code)
        # The existing test test_add should now fail → pass_to_pass fails
        self.assertFalse(
            result.step3_positive_pass,
            f"positive check should fail when existing test breaks: {result.logs.get('step3_positive_check')}",
        )

    def test_baseline_cannot_execute_is_error(self):
        """Baseline that cannot execute must be ERROR, not PASS."""
        # No source files, no tests → baseline can't find tests
        verifier = self._make_verifier()
        result = verifier.verify(solution_diff="", test_code="")
        # Should not be a PASS
        self.assertNotEqual(result.step1_evidence.outcome.value, "PASS")

    def test_missing_test_runner_is_error_not_pass(self):
        """A missing test runner must produce ERROR, never PASS."""
        # Create a TS-like repo scenario where vitest is configured but missing
        # We simulate this by patching _find_local_bin to return None
        self._make_python_repo(
            "def add(a, b):\n    return a + b\n",
            "from mymod import add\n\ndef test_add():\n    assert add(1, 2) == 3\n",
        )
        verifier = self._make_verifier()

        # Patch prepare_sandbox to raise SandboxPreparationError (simulating missing deps)
        from src.languages.base import SandboxPreparationError

        with patch.object(
            verifier.adapter,
            "prepare_sandbox",
            side_effect=SandboxPreparationError("vitest not found in node_modules"),
        ):
            result = verifier.verify(solution_diff="", test_code="")
            # Baseline should be ERROR due to sandbox prep failure
            self.assertEqual(
                result.step1_evidence.outcome.value,
                "ERROR",
                f"baseline should be ERROR, got {result.step1_evidence.outcome.value}",
            )
            self.assertFalse(result.step1_baseline_pass)
            # Task must not be valid
            self.assertFalse(result.is_valid)

    def test_environment_failure_never_reported_as_success(self):
        """Critical: setup/environment failure must never be VERIFIED."""
        self._make_python_repo(
            "def add(a, b):\n    return a + b\n",
            "from mymod import add\n\ndef test_add():\n    assert add(1, 2) == 3\n",
        )
        verifier = self._make_verifier()

        from src.languages.base import SandboxPreparationError

        with patch.object(
            verifier.adapter,
            "prepare_sandbox",
            side_effect=SandboxPreparationError("missing dependencies"),
        ):
            result = verifier.verify(solution_diff="", test_code="")
            self.assertFalse(result.is_valid)
            self.assertEqual(result.step1_evidence.outcome.value, "ERROR")
            # Validation report would show FAILED_VERIFICATION, never VERIFIED


if __name__ == "__main__":
    unittest.main()
