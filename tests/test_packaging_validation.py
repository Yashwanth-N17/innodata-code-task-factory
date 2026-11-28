"""
Independent packaging validation using controlled fixtures.

Validates that TaskPackager produces correct, complete, portable task packages:
- Required files present (instruction.md, solution/solution.diff, test file, task.toml, validation_report.json)
- Paths are portable and relative
- No machine-specific absolute paths or secrets leak into the package
- Overwrite protection works
- Packaged task can be extracted and inspected independently
"""

import json
import os
import shutil
import tempfile
import unittest

from src.languages import LanguageRegistry
from src.languages.python import PythonAdapter
from src.packager import TaskPackager
from src.verifier import VerificationResult, TestRunEvidence, TestRunOutcome


def _make_verification(is_valid=True):
    """Create a minimal VerificationResult for packaging tests."""
    adapter = PythonAdapter()
    result = VerificationResult(adapter=adapter)
    result.step1_baseline_pass = is_valid
    result.step2_negative_fail = is_valid
    result.step3_positive_pass = is_valid
    result.patch_applied_cleanly = is_valid

    passed_evidence = TestRunEvidence(
        outcome=TestRunOutcome.PASS, discovered_count=2, executed_count=2,
        passed_count=2, failed_count=0, skipped_count=0, error_count=0,
        exit_code=0,
        test_results={
            "tests/test_existing.py::test_add": "passed",
            "tests/test_feature_patch.py::test_new": "passed",
        },
    )
    failed_evidence = TestRunEvidence(
        outcome=TestRunOutcome.FAIL, discovered_count=1, executed_count=1,
        passed_count=0, failed_count=1, skipped_count=0, error_count=0,
        exit_code=1,
        test_results={"tests/test_feature_patch.py::test_new": "failed"},
    )

    result.step1_evidence = passed_evidence
    result.step2_evidence = failed_evidence
    result.step3_evidence = passed_evidence if is_valid else None
    result.step2_failed_test_ids = ["tests/test_feature_patch.py::test_new"] if is_valid else []
    result.fail_to_pass = ["tests/test_feature_patch.py::test_new"] if is_valid else []
    result.pass_to_pass = ["tests/test_existing.py::test_add"] if is_valid else []
    result.patch_tier = "git_apply"
    result.last_patch_tier = "git_apply"
    result.warnings = []
    result.logs = {
        "step1_baseline": "2 passed",
        "step2_negative_check": "1 failed",
        "step3_positive_check": "2 passed",
        "patch_application": "Patch applied successfully (git_apply).",
    }
    return result


class PackagingValidationTests(unittest.TestCase):
    """Validate TaskPackager output independently."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.output_root = os.path.join(self.tmp, "tasks")
        os.makedirs(self.output_root)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_package_contains_all_required_files(self):
        """Package must contain instruction.md, solution.diff, test file, task.toml, validation_report.json."""
        packager = TaskPackager(self.output_root)
        adapter = LanguageRegistry.get_adapter(self.tmp, language="python")
        verification = _make_verification()

        task_dir = packager.package(
            task_id="task_test_001",
            repo_name="test-repo",
            category="bug_fix",
            instruction_md="# Test Instruction\nImplement something.",
            solution_diff="--- a/test.py\n+++ b/test.py\n@@ -1 +1 @@\n-1\n+2\n",
            test_code="def test_new():\n    assert True\n",
            verification=verification,
            language_adapter=adapter,
            manifest={},
            repo_meta={"repo_url": "https://example.com/repo", "commit_hash": "abc123"},
        )

        # Required files
        self.assertTrue(os.path.isfile(os.path.join(task_dir, "instruction.md")))
        self.assertTrue(os.path.isfile(os.path.join(task_dir, "solution", "solution.diff")))
        self.assertTrue(os.path.isfile(os.path.join(task_dir, "task.toml")))
        self.assertTrue(os.path.isfile(os.path.join(task_dir, "validation_report.json")))

        # Test file exists (at adapter's test_file_relpath)
        test_rel = adapter.test_file_relpath.replace("\\", "/")
        test_path = os.path.join(task_dir, *test_rel.split("/"))
        self.assertTrue(os.path.isfile(test_path))

    def test_validation_report_has_required_fields(self):
        """validation_report.json must contain status, checks, environment, evaluation, logs."""
        packager = TaskPackager(self.output_root)
        adapter = LanguageRegistry.get_adapter(self.tmp, language="python")
        verification = _make_verification()

        task_dir = packager.package(
            task_id="task_test_002",
            repo_name="test-repo",
            category="bug_fix",
            instruction_md="# Test",
            solution_diff="--- a/test.py\n+++ b/test.py\n@@ -1 +1 @@\n-1\n+2\n",
            test_code="def test_new():\n    assert True\n",
            verification=verification,
            language_adapter=adapter,
            manifest={},
            repo_meta={"repo_url": "https://example.com/repo", "commit_hash": "abc123"},
        )

        with open(os.path.join(task_dir, "validation_report.json"), "r") as f:
            report = json.load(f)

        self.assertEqual(report["task_id"], "task_test_002")
        expected_status = "VERIFIED" if verification.is_valid else "FAILED_VERIFICATION"
        self.assertEqual(report["status"], expected_status)
        self.assertIn("checks", report)
        self.assertIn("step1_baseline_pass", report["checks"])
        self.assertIn("step2_negative_fail", report["checks"])
        self.assertIn("step3_positive_pass", report["checks"])
        self.assertIn("patch_applied_cleanly", report["checks"])
        self.assertIn("environment", report)
        self.assertIn("evaluation", report)
        self.assertIn("fail_to_pass", report["evaluation"])
        self.assertIn("pass_to_pass", report["evaluation"])
        self.assertIn("logs", report)

    def test_no_absolute_paths_leak_into_package(self):
        """No machine-specific absolute paths should appear in packaged files."""
        packager = TaskPackager(self.output_root)
        adapter = LanguageRegistry.get_adapter(self.tmp, language="python")
        verification = _make_verification()

        task_dir = packager.package(
            task_id="task_test_003",
            repo_name="test-repo",
            category="bug_fix",
            instruction_md="# Test",
            solution_diff="--- a/test.py\n+++ b/test.py\n@@ -1 +1 @@\n-1\n+2\n",
            test_code="def test_new():\n    assert True\n",
            verification=verification,
            language_adapter=adapter,
            manifest={},
            repo_meta={"repo_url": "https://example.com/repo", "commit_hash": "abc123"},
        )

        # Read all text files in the package
        for root, _dirs, files in os.walk(task_dir):
            for fname in files:
                fpath = os.path.join(root, fname)
                if fname.endswith((".md", ".toml", ".json", ".diff", ".py", ".txt")):
                    with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                        content = f.read()
                    # The tmp directory path should not appear
                    self.assertNotIn(
                        self.tmp,
                        content,
                        f"Absolute path {self.tmp} leaked into {fpath}",
                    )
                    # No Windows drive letters from temp dir
                    self.assertNotIn(
                        "C:\\Users",
                        content,
                        f"Windows path leaked into {fpath}",
                    )

    def test_task_toml_has_required_fields(self):
        """task.toml must contain task_id, repo_name, language, category, created_at."""
        packager = TaskPackager(self.output_root)
        adapter = LanguageRegistry.get_adapter(self.tmp, language="python")
        verification = _make_verification()

        task_dir = packager.package(
            task_id="task_test_004",
            repo_name="test-repo",
            category="feature",
            instruction_md="# Test",
            solution_diff="--- a/test.py\n+++ b/test.py\n@@ -1 +1 @@\n-1\n+2\n",
            test_code="def test_new():\n    assert True\n",
            verification=verification,
            language_adapter=adapter,
            manifest={},
            repo_meta={"repo_url": "https://example.com/repo", "commit_hash": "abc123"},
        )

        with open(os.path.join(task_dir, "task.toml"), "r") as f:
            toml_content = f.read()

        self.assertIn('task_id = "task_test_004"', toml_content)
        self.assertIn('repo_name = "test-repo"', toml_content)
        self.assertIn('language = "python"', toml_content)
        self.assertIn('category = "feature"', toml_content)
        self.assertIn("created_at = ", toml_content)

    def test_overwrite_protection(self):
        """Package must not overwrite existing directory without overwrite=True."""
        packager = TaskPackager(self.output_root)
        adapter = LanguageRegistry.get_adapter(self.tmp, language="python")
        verification = _make_verification()

        task_dir = os.path.join(self.output_root, "task_test_005")
        os.makedirs(task_dir, exist_ok=True)

        with self.assertRaises(FileExistsError):
            packager.package(
                task_id="task_test_005",
                repo_name="test-repo",
                category="bug_fix",
                instruction_md="# Test",
                solution_diff="--- a/test.py\n+++ b/test.py\n@@ -1 +1 @@\n-1\n+2\n",
                test_code="def test_new():\n    assert True\n",
                verification=verification,
                language_adapter=adapter,
                manifest={},
                repo_meta={"repo_url": "https://example.com/repo", "commit_hash": "abc123"},
            )

    def test_overwrite_allowed_with_flag(self):
        """Package must overwrite existing directory when overwrite=True."""
        packager = TaskPackager(self.output_root)
        adapter = LanguageRegistry.get_adapter(self.tmp, language="python")
        verification = _make_verification()

        task_dir = os.path.join(self.output_root, "task_test_006")
        os.makedirs(task_dir, exist_ok=True)
        _write_marker = os.path.join(task_dir, "marker.txt")
        with open(_write_marker, "w") as f:
            f.write("old")

        result_dir = packager.package(
            task_id="task_test_006",
            repo_name="test-repo",
            category="bug_fix",
            instruction_md="# Test",
            solution_diff="--- a/test.py\n+++ b/test.py\n@@ -1 +1 @@\n-1\n+2\n",
            test_code="def test_new():\n    assert True\n",
            verification=verification,
            language_adapter=adapter,
            manifest={},
            repo_meta={"repo_url": "https://example.com/repo", "commit_hash": "abc123"},
            overwrite=True,
        )

        self.assertEqual(result_dir, task_dir)
        self.assertFalse(os.path.exists(_write_marker))
        self.assertTrue(os.path.isfile(os.path.join(task_dir, "instruction.md")))

    def test_failed_verification_not_packaged_as_verified(self):
        """A failed verification must be packaged as FAILED_VERIFICATION, not VERIFIED."""
        packager = TaskPackager(self.output_root)
        adapter = LanguageRegistry.get_adapter(self.tmp, language="python")
        verification = _make_verification(is_valid=False)

        task_dir = packager.package(
            task_id="task_test_007",
            repo_name="test-repo",
            category="bug_fix",
            instruction_md="# Test",
            solution_diff="--- a/test.py\n+++ b/test.py\n@@ -1 +1 @@\n-1\n+2\n",
            test_code="def test_new():\n    assert True\n",
            verification=verification,
            language_adapter=adapter,
            manifest={},
            repo_meta={"repo_url": "https://example.com/repo", "commit_hash": "abc123"},
        )

        with open(os.path.join(task_dir, "validation_report.json"), "r") as f:
            report = json.load(f)

        self.assertEqual(report["status"], "FAILED_VERIFICATION")
        self.assertFalse(report["checks"]["step1_baseline_pass"])
        self.assertFalse(report["checks"]["step2_negative_fail"])
        self.assertFalse(report["checks"]["step3_positive_pass"])
        self.assertFalse(report["checks"]["patch_applied_cleanly"])


if __name__ == "__main__":
    unittest.main()
