import json
import os
import subprocess
import sys
import tempfile
import unittest

from src.verifier import SandboxVerifier


def _pytest_available() -> bool:
    try:
        res = subprocess.run(
            [sys.executable, "-m", "pytest", "--version"],
            capture_output=True,
            text=True,
        )
        return res.returncode == 0
    except Exception:
        return False

MODIFY = """--- a/mod.py
+++ b/mod.py
@@ -1,5 +1,7 @@
 def f(x):
-    return x
+    if x is None:
+        raise ValueError("x")
+    return x

 def g():
     pass
"""

NO_CONTEXT = """--- a/mod.py
+++ b/mod.py
@@ -2,4 +2,6 @@
+    def extra(self):
+        return 1
"""

NEW_FILE = """--- /dev/null
+++ b/pkg/new.py
@@ -0,0 +1,2 @@
+def h():
+    return 1
"""


class ManualPatchTests(unittest.TestCase):
    def _run(self, diff, files):
        tmp = tempfile.mkdtemp()
        for name, content in files.items():
            with open(os.path.join(tmp, name), "w", encoding="utf-8") as f:
                f.write(content)
        patch = os.path.join(tmp, "p.patch")
        with open(patch, "w", encoding="utf-8", newline="\n") as f:
            f.write(diff)
        code, msg = SandboxVerifier(tmp, language="python")._manual_apply_patch(tmp, patch)
        return tmp, code, msg

    def test_edit_inside_function(self):
        src = "def f(x):\n    return x\n\ndef g():\n    pass\n"
        tmp, code, _ = self._run(MODIFY, {"mod.py": src})
        self.assertEqual(code, 0)
        with open(os.path.join(tmp, "mod.py"), encoding="utf-8") as f:
            out = f.read()
        self.assertIn('raise ValueError("x")', out)
        self.assertTrue(out.rstrip().endswith("pass"))   # g() untouched, not appended to

    def test_new_file(self):
        tmp, code, _ = self._run(NEW_FILE, {})
        self.assertEqual(code, 0)
        self.assertTrue(os.path.isfile(os.path.join(tmp, "pkg", "new.py")))

    def test_context_mismatch_changes_nothing(self):
        src = "def other():\n    return 0\n"
        tmp, code, _ = self._run(MODIFY, {"mod.py": src})
        self.assertNotEqual(code, 0)
        with open(os.path.join(tmp, "mod.py"), encoding="utf-8") as f:
            self.assertEqual(f.read(), src)

    def test_hunk_without_context_is_rejected(self):
        src = "class A:\n    def a(self):\n        return 0\n"
        tmp, code, msg = self._run(NO_CONTEXT, {"mod.py": src})
        self.assertNotEqual(code, 0)
        self.assertIn("malformed hunk", msg)
        with open(os.path.join(tmp, "mod.py"), encoding="utf-8") as f:
            self.assertEqual(f.read(), src)


CALC_DIFF = """--- a/calc.py
+++ b/calc.py
@@ -1,2 +1,5 @@
 def add(a, b):
     return a + b
+
+def sub(a, b):
+    return a - b
"""

BREAKING_DIFF = """--- a/calc.py
+++ b/calc.py
@@ -1,2 +1,5 @@
 def add(a, b):
-    return a + b
+    return a - b
+
+def sub(a, b):
+    return a - b
"""

NEW_TEST = "import calc\n\ndef test_sub():\n    assert calc.sub(5, 2) == 3\n"


@unittest.skipUnless(_pytest_available(), "pytest is not installed in the python environment")
class FullMatrixTests(unittest.TestCase):
    def _toy_repo(self):
        repo = tempfile.mkdtemp()
        os.makedirs(os.path.join(repo, "tests"))
        with open(os.path.join(repo, "calc.py"), "w", encoding="utf-8") as f:
            f.write("def add(a, b):\n    return a + b\n")
        with open(os.path.join(repo, "tests", "test_calc.py"), "w", encoding="utf-8") as f:
            f.write("from calc import add\n\ndef test_add():\n    assert add(1, 2) == 3\n")
        with open(os.path.join(repo, "pytest.ini"), "w", encoding="utf-8") as f:
            f.write("[pytest]\npythonpath = .\n")
        return repo

    def test_reports_fail_to_pass_and_pass_to_pass(self):
        r = SandboxVerifier(self._toy_repo()).verify(CALC_DIFF, NEW_TEST)
        self.assertTrue(r.is_valid)
        self.assertEqual(r.patch_tier, "git_apply")
        self.assertEqual(r.fail_to_pass, ["tests.test_feature_patch::test_sub"])
        self.assertEqual(r.pass_to_pass, ["tests.test_calc::test_add"])

    def test_patch_that_breaks_existing_test_is_invalid(self):
        r = SandboxVerifier(self._toy_repo()).verify(BREAKING_DIFF, NEW_TEST)
        self.assertFalse(r.step3_positive_pass)
        self.assertFalse(r.is_valid)

    def test_new_test_that_already_passes_is_invalid(self):
        always_passes = "def test_ok():\n    assert True\n"
        r = SandboxVerifier(self._toy_repo()).verify(CALC_DIFF, always_passes)
        self.assertFalse(r.step2_negative_fail)
        self.assertFalse(r.is_valid)

    def test_diff_without_trailing_newline_is_flagged(self):
        r = SandboxVerifier(self._toy_repo()).verify(CALC_DIFF.rstrip("\n"), NEW_TEST)
        self.assertTrue(any("trailing newline" in w for w in r.warnings))
        self.assertIn("trailing newline", r.logs["patch_application"])

    def test_strict_mode_rejects_diff_only_the_fallback_can_apply(self):
        diff = CALC_DIFF.rstrip("\n")          # git calls this corrupt; the fallback accepts it
        lenient = SandboxVerifier(self._toy_repo()).verify(diff, NEW_TEST)
        strict = SandboxVerifier(self._toy_repo(), strict_patch=True).verify(diff, NEW_TEST)
        self.assertEqual(lenient.patch_tier, "manual")
        self.assertFalse(strict.is_valid)

    def test_clean_diff_has_no_warnings(self):
        r = SandboxVerifier(self._toy_repo()).verify(CALC_DIFF, NEW_TEST)
        self.assertEqual(r.warnings, [])

    def test_to_dict_is_json_serializable_evidence(self):
        r = SandboxVerifier(self._toy_repo()).verify(CALC_DIFF, NEW_TEST)
        report = r.to_dict()
        json.dumps(report)                                   # must not raise
        self.assertTrue(report["is_valid"])
        self.assertEqual(report["matrix"]["step2_negative_check"]["expected"], "FAIL")
        self.assertEqual(report["patch"]["tier"], "git_apply")
        self.assertEqual(report["fail_to_pass"], ["tests.test_feature_patch::test_sub"])
        self.assertEqual(report["config"]["timeout_seconds"], 45)

    def test_to_dict_trims_long_logs(self):
        r = SandboxVerifier(self._toy_repo()).verify(CALC_DIFF, NEW_TEST)
        r.logs["step1_baseline"] = "x" * 5000
        trimmed = r.to_dict(max_log_chars=100)["logs"]["step1_baseline"]
        self.assertLess(len(trimmed), 200)
        self.assertIn("truncated", trimmed)

    def test_timeout_is_configurable_and_reported(self):
        repo = self._toy_repo()
        with open(os.path.join(repo, "tests", "test_slow.py"), "w", encoding="utf-8") as f:
            f.write("import time\n\ndef test_slow():\n    time.sleep(5)\n")
        r = SandboxVerifier(repo, timeout=1).verify(CALC_DIFF, NEW_TEST)
        self.assertFalse(r.step1_baseline_pass)
        self.assertIn("timed out after 1 seconds", r.logs["step1_baseline"])
        self.assertFalse(r.is_valid)

    def test_python_executable_is_used(self):
        ok = SandboxVerifier(self._toy_repo(), python_executable=sys.executable)
        self.assertTrue(ok.verify(CALC_DIFF, NEW_TEST).is_valid)
        bad = SandboxVerifier(self._toy_repo(), python_executable=os.path.join(tempfile.gettempdir(), "no_such_python"))
        r = bad.verify(CALC_DIFF, NEW_TEST)
        self.assertFalse(r.step1_baseline_pass)
        self.assertIn("Execution failed", r.logs["step1_baseline"])


if __name__ == "__main__":
    unittest.main()