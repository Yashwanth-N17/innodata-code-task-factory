"""
Base Language Adapter Contract & Shared Utilities.
Defines the pluggable interface enabling the factory to support any programming language.
"""

import os
import re
import xml.etree.ElementTree as ET
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Set, Tuple


DEFAULT_IGNORED_DIRS: Set[str] = {
    ".git", "__pycache__", "venv", ".venv", ".pytest_cache",
    ".agents", "build", "dist", "node_modules", ".tox", ".mypy_cache",
    "coverage", ".turbo", "out", "_factory_build", "cmake-build-debug",
    "cmake-build-release",
}


def parse_junit_xml(xml_path: str) -> Dict[str, str]:
    """Return {test_id: 'passed'|'failed'|'error'|'skipped'} from any standard JUnit XML file."""
    results: Dict[str, str] = {}
    if not xml_path or not os.path.isfile(xml_path):
        return results
    try:
        root = ET.parse(xml_path).getroot()
    except Exception:
        return results

    for case in root.iter("testcase"):
        classname = (case.get("classname") or "").strip()
        name = (case.get("name") or "").strip()
        test_id = f"{classname}::{name}" if classname else name
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
        if test_id:
            results[test_id] = status
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

    @abstractmethod
    def run_tests(
        self,
        work_dir: str,
        target_test: Optional[str] = None,
        timeout: int = 45,
        **kwargs,
    ) -> Tuple[int, str, Dict[str, str]]:
        """Execute tests in work_dir and return (exit_code, logs, per_test_results)."""

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
            return str(s).replace("\\\\", "\\\\\\\\").replace('"', '\\\\"')
        repo_meta = repo_meta or {}
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
            f'fail_to_pass = ["{self.test_file_relpath}"]',
            f'pass_to_pass = ["{os.path.dirname(self.test_file_relpath) or "."}/"]',
            "",
        ])
        return "\n".join(lines)
