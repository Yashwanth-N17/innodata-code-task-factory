"""
Base Language Adapter Contract & Shared Utilities.
Defines the pluggable interface enabling the factory to support any programming language.
"""

import os
import re
import xml.etree.ElementTree as ET
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Set, Tuple


DEFAULT_IGNORED_DIRS: Set[str] = {
    ".git", "__pycache__", "venv", ".venv", ".pytest_cache",
    ".agents", "build", "dist", "node_modules", ".tox", ".mypy_cache",
    "coverage", ".turbo", "out", "_factory_build", "cmake-build-debug",
    "cmake-build-release",
}


class TestRunOutcome(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    NO_TESTS_FOUND = "NO_TESTS_FOUND"
    ERROR = "ERROR"


class SandboxPreparationError(RuntimeError):
    pass


class TestResultMap(dict):
    """Dictionary-compatible test results with report integrity metadata."""

    def __init__(self):
        super().__init__()
        self.discovered_count: Optional[int] = None
        self.report_error: Optional[str] = None
        self.report_warnings: List[str] = []
        self.report_present = False
        self.no_tests_found = False


@dataclass
class TestRunEvidence:
    outcome: TestRunOutcome
    discovered_count: int
    executed_count: int
    passed_count: int
    failed_count: int
    skipped_count: int
    error_count: int
    test_results: Dict[str, str] = field(default_factory=dict)
    exit_code: int = 0
    diagnostics: str = ""

    @property
    def failed_test_ids(self) -> List[str]:
        return sorted(test_id for test_id, status in self.test_results.items() if status == "failed")

    @property
    def passed_test_ids(self) -> List[str]:
        return sorted(test_id for test_id, status in self.test_results.items() if status == "passed")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "outcome": self.outcome.value,
            "discovered_count": self.discovered_count,
            "executed_count": self.executed_count,
            "passed_count": self.passed_count,
            "failed_count": self.failed_count,
            "skipped_count": self.skipped_count,
            "error_count": self.error_count,
            "test_results": dict(self.test_results),
            "exit_code": self.exit_code,
            "diagnostics": self.diagnostics,
        }


def parse_junit_xml(
    xml_path: str,
    *,
    vitest_compatibility: bool = False,
) -> TestResultMap:
    """Return test IDs and statuses while retaining report parse metadata."""
    results = TestResultMap()
    if not xml_path or not os.path.isfile(xml_path):
        results.report_error = "JUnit XML report is missing."
        return results
    try:
        if os.path.getsize(xml_path) == 0:
            results.report_error = "JUnit XML report is empty."
            return results
        root = ET.parse(xml_path).getroot()
    except (ET.ParseError, OSError) as exc:
        results.report_error = f"JUnit XML report could not be parsed: {exc}"
        return results

    results.report_present = True
    root_tag = root.tag.rsplit("}", 1)[-1]
    if root_tag not in ("testsuite", "testsuites"):
        results.report_error = f"JUnit XML report has unexpected root element '{root.tag}'."
        return results

    cases = [
        element for element in root.iter()
        if element.tag.rsplit("}", 1)[-1] == "testcase"
    ]
    results.discovered_count = len(cases)
    declared_tests = root.get("tests")
    if declared_tests is not None:
        try:
            if int(declared_tests) != len(cases):
                results.report_error = (
                    f"JUnit XML report declares {declared_tests} tests but contains "
                    f"{len(cases)} test cases."
                )
        except ValueError:
            results.report_error = "JUnit XML report has an invalid test count."

    unnamed_counts: Dict[str, int] = {}
    identity_counts: Dict[str, int] = {}
    duplicate_occurrences = 0
    duplicate_identity_count = 0
    for case in cases:
        classname = (case.get("classname") or "").strip()
        name = (case.get("name") or "").strip()
        if not name:
            if not vitest_compatibility:
                results.report_error = (
                    "JUnit XML report contains a test case without a nonblank name."
                )
                continue
            identity_source = classname or (case.get("file") or "").strip()
            if not identity_source:
                results.report_error = (
                    "JUnit XML report contains an unnamed test case without a classname "
                    "or file identity."
                )
                continue
            unnamed_counts[identity_source] = unnamed_counts.get(identity_source, 0) + 1
            name = f"[unnamed testcase {unnamed_counts[identity_source]}]"
            results.report_warnings.append(
                f"Preserved unnamed JUnit testcase from '{identity_source}' as '{name}'."
            )
            if not classname:
                classname = identity_source
        test_id = f"{classname}::{name}" if classname else name
        if test_id in results:
            if not vitest_compatibility:
                results.report_error = (
                    f"JUnit XML report contains duplicate test identity '{test_id}'."
                )
                continue
            duplicate_occurrences += 1
            if identity_counts.get(test_id, 1) == 1:
                duplicate_identity_count += 1
            base_test_id = test_id
            occurrence = identity_counts.get(base_test_id, 1) + 1
            test_id = f"{base_test_id} [duplicate testcase {occurrence}]"
            while test_id in results:
                occurrence += 1
                test_id = f"{base_test_id} [duplicate testcase {occurrence}]"
            identity_counts[base_test_id] = occurrence
        else:
            identity_counts[test_id] = 1
        status = "passed"
        for child in case:
            if child.tag == "failure":
                status = "failed"
                break
            elif child.tag == "error":
                status = "error"
                break
            elif child.tag == "skipped":
                status = "skipped"
                break
        if status == "passed" and (case.get("status") or "").lower() in ("fail", "failed", "error"):
            status = "failed"
        results[test_id] = status
    if duplicate_occurrences:
        results.report_warnings.append(
            f"Preserved {duplicate_occurrences} repeated Vitest testcase identities "
            f"across {duplicate_identity_count} tests with stable suffixes."
        )
    return results


class BaseLanguageAdapter(ABC):
    """Abstract strategy defining language-specific analysis, test execution, and packaging."""

    language_id: str = "generic"
    display_name: str = "Generic"
    source_extensions: Tuple[str, ...] = ()
    test_extensions: Tuple[str, ...] = ()
    test_framework: str = "unknown"
    test_file_relpath: str = "tests/test_feature_patch.txt"
    root_test_filename: str = "test_patch.txt"
    ignored_dirs: Set[str] = set(DEFAULT_IGNORED_DIRS)

    @classmethod
    @abstractmethod
    def detect_score(cls, repo_path: str) -> int:
        """Return detection confidence score (0-100) for the given repository."""

    @abstractmethod
    def inspect_manifest(self, repo_path: str) -> Dict[str, Any]:
        """Parse language build/package/test manifests (e.g. pyproject.toml, package.json, CMakeLists.txt)."""

    @abstractmethod
    def is_test_file(self, rel_path: str, full_path: str) -> bool:
        """Return True if the file is an existing test suite rather than production source."""

    def is_source_file(self, rel_path: str) -> bool:
        """Return True if the file has a supported source extension for this language."""
        norm = rel_path.lower()
        return any(norm.endswith(ext) for ext in self.source_extensions)

    @abstractmethod
    def scan_file(self, full_path: str, rel_path: str, summary: Dict[str, Any]) -> None:
        """Parse one source file and append symbols/imports into the shared summary dict."""

    def select_targets(
        self,
        repo_path: str,
        scan_data: Dict[str, Any],
        max_files: int = 3,
        target_offset: int = 0,
    ) -> List[str]:
        """Select high-value target source files (rotating when target_offset > 0)."""
        files = list(scan_data.get("files") or [])
        if not files:
            return []

        file_scores: Dict[str, int] = {f: 0 for f in files}
        for cls in scan_data.get("classes") or []:
            f = cls.get("file", "")
            if f in file_scores:
                file_scores[f] += 3 + len(cls.get("public_methods") or cls.get("methods") or [])
        for fn in scan_data.get("functions") or []:
            f = fn.get("file", "")
            if f in file_scores:
                file_scores[f] += 1 if fn.get("is_private") else 2

        ranked = sorted(files, key=lambda f: (-file_scores.get(f, 0), f))
        if target_offset and len(ranked) > 1:
            shift = target_offset % len(ranked)
            ranked = ranked[shift:] + ranked[:shift]
        return ranked[:max(1, max_files)]

    def format_numbered_source(
        self,
        repo_path: str,
        rel_paths: List[str],
        max_lines_per_file: int = 250,
    ) -> str:
        """Format numbered source code excerpts for Tier 2 CoderAgent/TestAgent context."""
        blocks: List[str] = []
        for rel in rel_paths:
            full = os.path.join(repo_path, rel)
            if not os.path.isfile(full):
                continue
            try:
                with open(full, "r", encoding="utf-8", errors="replace") as f:
                    lines = f.read().splitlines()
            except Exception:
                continue

            total = len(lines)
            excerpt = lines[:max_lines_per_file]
            numbered = "\n".join(f"{idx:4d} | {line}" for idx, line in enumerate(excerpt, 1))
            trunc_note = f"\n... [truncated {total - max_lines_per_file} remaining lines]" if total > max_lines_per_file else ""
            blocks.append(f"### File: {rel} ({total} lines)\n```{self.language_id}\n{numbered}{trunc_note}\n```")
        return "\n\n".join(blocks)

    def prepare_sandbox(self, repo_path: str, work_dir: str) -> None:
        """Optional hook to prepare isolated copy (e.g. symlink node_modules)."""
        return None

    def write_test_file(self, work_dir: str, test_code: str) -> str:
        """Write synthesized test code into the sandbox working directory."""
        path = os.path.join(work_dir, self.test_file_relpath)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(test_code)
        return path

    def generated_test_relpath(
        self, work_dir: Optional[str] = None, target_file: Optional[str] = None
    ) -> str:
        """Return the relative path for the generated acceptance test.

        Default implementation returns ``test_file_relpath``. Subclasses may override
        for workspace-aware placement (e.g. Vitest monorepos).
        """
        return self.test_file_relpath

    @abstractmethod
    def run_tests(
        self,
        work_dir: str,
        target_test: Optional[str] = None,
        timeout: int = 45,
        **kwargs,
    ) -> Tuple[int, str, Dict[str, str]]:
        """Execute tests in work_dir and return (exit_code, logs, per_test_results)."""

    def has_no_tests_evidence(
        self,
        exit_code: int,
        diagnostics: str,
        results: Dict[str, str],
    ) -> bool:
        """Return True only when the framework explicitly reports no tests."""
        return False

    def canonical_test_id(self, test_id: str) -> str:
        """Normalize runner-specific path separators without weakening identity matching."""
        return re.sub(r"\s+", " ", (test_id or "").replace("\\", "/").strip())

    def normalize_test_status(self, test_id: str, status: str) -> str:
        return status.lower()

    def is_test_executed_result(self, test_id: str, status: str) -> bool:
        return status in ("passed", "failed")

    def run_tests_detailed(
        self,
        work_dir: str,
        target_test: Optional[str] = None,
        timeout: int = 45,
        **kwargs,
    ) -> TestRunEvidence:
        """Run the legacy runner once and classify its concrete test evidence."""
        exit_code, diagnostics, raw_results = self.run_tests(
            work_dir,
            target_test=target_test,
            timeout=timeout,
            **kwargs,
        )
        return self.evidence_from_test_run(exit_code, diagnostics, raw_results)

    def evidence_from_test_run(
        self,
        exit_code: int,
        diagnostics: str,
        raw_results: Dict[str, str],
    ) -> TestRunEvidence:
        """Classify one runner invocation without inferring discovery from an empty map."""
        results = {
            self.canonical_test_id(test_id): self.normalize_test_status(test_id, str(status))
            for test_id, status in raw_results.items()
        }
        discovered_count = getattr(raw_results, "discovered_count", None)
        if discovered_count is None:
            discovered_count = len(results)
        report_error = getattr(raw_results, "report_error", None)
        explicit_no_tests = self.has_no_tests_evidence(exit_code, diagnostics, raw_results)
        statuses = list(results.values())
        passed_count = statuses.count("passed")
        failed_count = statuses.count("failed")
        skipped_count = statuses.count("skipped")
        error_count = statuses.count("error")
        executed_count = sum(
            1
            for test_id, status in results.items()
            if self.is_test_executed_result(test_id, status)
        )

        if report_error and not (
            explicit_no_tests and report_error.lower().endswith("is missing.")
        ):
            outcome = TestRunOutcome.ERROR
            diagnostics = f"{diagnostics}\n{report_error}".strip()
        elif explicit_no_tests and exit_code >= 0 and discovered_count == 0 and not statuses:
            outcome = TestRunOutcome.NO_TESTS_FOUND
        elif (
            discovered_count != len(results)
            or any(status not in ("passed", "failed", "skipped", "error") for status in statuses)
            or error_count
            or exit_code < 0
        ):
            outcome = TestRunOutcome.ERROR
        elif failed_count:
            outcome = TestRunOutcome.FAIL
        elif exit_code != 0:
            outcome = TestRunOutcome.ERROR
        elif skipped_count and skipped_count == discovered_count:
            outcome = TestRunOutcome.ERROR
            diagnostics = f"{diagnostics}\nAll discovered tests were skipped.".strip()
        elif passed_count:
            outcome = TestRunOutcome.PASS
        else:
            outcome = TestRunOutcome.ERROR

        return TestRunEvidence(
            outcome=outcome,
            discovered_count=discovered_count,
            executed_count=executed_count,
            passed_count=passed_count,
            failed_count=failed_count,
            skipped_count=skipped_count,
            error_count=error_count,
            test_results=results,
            exit_code=exit_code,
            diagnostics=diagnostics,
        )

    @abstractmethod
    def is_real_failure(self, code: int, out: str) -> bool:
        """Return True if exit code/output indicates a legitimate test/feature failure (not tool crash)."""

    def is_new_test(self, test_id: str) -> bool:
        """Return True if the JUnit/runner test identifier corresponds to the synthesized feature test."""
        norm = test_id.replace("\\", "/")
        stem = os.path.splitext(os.path.basename(self.test_file_relpath))[0]
        return stem in norm

    def get_leakage_regexes(self) -> List[re.Pattern]:
        """Return additional language-specific file:line or private member leakage patterns."""
        return []

    def get_prompt_context(self) -> Dict[str, str]:
        """Return language-specific guidelines for CoderAgent and TestAgent prompts."""
        return {
            "language": self.display_name,
            "test_framework": self.test_framework,
            "test_file": self.test_file_relpath,
            "coder_rules": f"Ensure all imports, syntax, and typing are 100% valid {self.display_name}.",
            "test_rules": f"Use standard {self.test_framework} assertions in `{self.test_file_relpath}`.",
        }

    @abstractmethod
    def get_environment_metadata(self, repo_path: str, manifest: Dict[str, Any]) -> Dict[str, Any]:
        """Return standardized environment dict for task.toml and validation_report.json."""

    def format_task_toml(
        self,
        task_id: str,
        repo_name: str,
        category: str,
        created_at: str,
        env_meta: Dict[str, Any],
        repo_meta: Optional[Dict[str, str]] = None,
    ) -> str:
        """Render standardized polyglot task.toml content."""
        def _esc(s):
            return str(s).replace("\\", "\\\\").replace('"', '\\"')
        repo_meta = repo_meta or {}
        test_file = self.test_file_relpath.replace("\\", "/")
        lines = [
            "# Task Package Configuration",
            f'task_id = "{_esc(task_id)}"',
            f'repo_name = "{_esc(repo_name)}"',
            f'language = "{_esc(self.language_id)}"',
            f'category = "{_esc(category)}"',
            f'created_at = "{_esc(created_at)}"',
        ]
        if repo_meta.get("repo_url"):
            lines.append(f'repo_url = "{_esc(repo_meta["repo_url"])}"')
        if repo_meta.get("commit_hash"):
            lines.append(f'base_commit = "{_esc(repo_meta["commit_hash"])}"')

        lines.append("")
        lines.append("[environment]")
        for k, v in env_meta.items():
            if isinstance(v, bool):
                lines.append(f"{k} = {'true' if v else 'false'}")
            elif isinstance(v, (int, float)):
                lines.append(f"{k} = {v}")
            elif isinstance(v, list):
                items = ", ".join(f'"{_esc(item)}"' for item in v)
                lines.append(f"{k} = [{items}]")
            else:
                lines.append(f'{k} = "{_esc(v)}"')

        lines.extend([
            "",
            "[evaluation]",
            f'fail_to_pass = ["{test_file}"]',
            f'pass_to_pass = ["{os.path.dirname(test_file) or "."}/"]',
            "",
        ])
        return "\n".join(lines)
