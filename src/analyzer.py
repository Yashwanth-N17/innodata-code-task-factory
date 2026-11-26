"""
Feature 1: Polyglot Codebase Semantic Scanner & 2-Tier Context Indexer.
Crawls any target repository (Python, TypeScript, C++, or any registered LanguageAdapter),
extracts manifests, directory layout, classes, interfaces, methods, functions, inheritance,
and existing test suites without executing arbitrary code.

Tier 1 (get_context_prompt): Clean architectural summary (WHAT exists) for SpecGenerator.
Tier 2 (get_targeted_context): Full numbered source excerpts for CoderAgent & TestAgent.
"""

import ast
import os
from typing import Any, Dict, List, Optional

from .languages import BaseLanguageAdapter, LanguageRegistry
from .languages.python import MethodSignature, _extract_signature, _safe_unparse


class CodebaseAnalyzer:
    def __init__(self, repo_path: str, language: Optional[str] = None):
        self.repo_path = os.path.abspath(repo_path)
        self.adapter: BaseLanguageAdapter = LanguageRegistry.get_adapter(self.repo_path, language=language)
        self.language: str = self.adapter.language_id

    def _is_test_file(self, rel_path: str, full_path: str) -> bool:
        """Delegate test-vs-source classification to the active LanguageAdapter."""
        return self.adapter.is_test_file(rel_path, full_path)

    def _parse_file(self, full_path: str, rel_path: str, summary: Dict[str, Any]) -> None:
        """Delegate symbol extraction to the active LanguageAdapter."""
        self.adapter.scan_file(full_path, rel_path, summary)

    def scan(self) -> Dict[str, Any]:
        """Scan the entire repository and return structured symbols, manifest, and test suites."""
        manifest = self.adapter.inspect_manifest(self.repo_path)
        summary: Dict[str, Any] = {
            "repo_name": os.path.basename(self.repo_path),
            "language": self.adapter.language_id,
            "display_name": self.adapter.display_name,
            "test_framework": manifest.get("test_runner") or manifest.get("test_framework") or self.adapter.test_framework,
            "manifest": manifest,
            "files": [],
            "classes": [],
            "functions": [],
            "imports": [],
            "existing_tests": [],
            "class_hierarchy": {},
            "inheritance": {},
        }

        ignored_dirs = self.adapter.ignored_dirs

        for root, dirs, files in os.walk(self.repo_path):
            dirs[:] = sorted([d for d in dirs if d not in ignored_dirs])

            for file in sorted(files):
                full_path = os.path.join(root, file)
                rel_path = os.path.relpath(full_path, self.repo_path)

                if not self.adapter.is_source_file(rel_path):
                    continue

                if self._is_test_file(rel_path, full_path):
                    summary["existing_tests"].append(rel_path)
                else:
                    summary["files"].append(rel_path)
                    self._parse_file(full_path, rel_path, summary)

        for cls in summary["classes"]:
            summary["class_hierarchy"][cls["name"]] = cls.get("bases", [])
        summary["inheritance"] = summary["class_hierarchy"]
        summary["target_files"] = self.adapter.select_targets(self.repo_path, summary)

        return summary

    def get_file_content(self, rel_path: str) -> Optional[str]:
        """Retrieve full source code of a file within the repository."""
        full_path = os.path.join(self.repo_path, rel_path)
        if os.path.exists(full_path) and os.path.isfile(full_path):
            with open(full_path, "r", encoding="utf-8", errors="replace") as f:
                return f.read()
        return None

    def get_primary_target(self) -> Dict[str, Any]:
        """Return the highest-priority target module/class to benchmark."""
        scan_data = self.scan()
        target_files = scan_data.get("target_files") or []
        preferred_file = target_files[0] if target_files else None

        if scan_data["classes"]:
            matching = [c for c in scan_data["classes"] if c.get("file") == preferred_file]
            target_cls = matching[0] if matching else scan_data["classes"][0]
            content = self.get_file_content(target_cls["file"])
            return {
                "file": target_cls["file"],
                "class_name": target_cls["name"],
                "content": content or "",
            }
        elif preferred_file or scan_data["files"]:
            target_file = preferred_file or scan_data["files"][0]
            content = self.get_file_content(target_file)
            return {
                "file": target_file,
                "class_name": "Module",
                "content": content or "",
            }
        default_ext = self.adapter.source_extensions[0] if self.adapter.source_extensions else ".py"
        return {"file": f"main{default_ext}", "class_name": "App", "content": ""}

    def get_context_prompt(self, max_classes: int = 40, max_functions: int = 40, target_offset: int = 0) -> str:
        """Tier 1: Format parsed repo blueprint into a SpecGenerator-safe summary (no line numbers)."""
        scan_data = self.scan()
        files = scan_data["files"]
        files_preview = ", ".join(files[:60])
        if len(files) > 60:
            files_preview += f", ... (+{len(files) - 60} more)"

        tests = scan_data["existing_tests"]
        test_suites_str = ", ".join(tests[:30]) if tests else "None"
        if len(tests) > 30:
            test_suites_str += f", ... (+{len(tests) - 30} more)"

        manifest = scan_data.get("manifest") or {}
        manifest_notes: List[str] = []
        if manifest.get("manifests"):
            manifest_notes.append(f"Manifests: {', '.join(manifest['manifests'])}")
        if manifest.get("workspace_packages"):
            pkg_paths = [f"{p['name']} ({p['path']})" for p in manifest["workspace_packages"][:10]]
            manifest_notes.append(f"Workspaces: {', '.join(pkg_paths)}")
        if manifest.get("src_layout"):
            manifest_notes.append("Package Layout: src/")
        if manifest.get("cmake_targets"):
            manifest_notes.append(f"CMake Targets: {', '.join(manifest['cmake_targets'])}")

        lines = [
            f"Repository: {scan_data['repo_name']}",
            f"Language: {self.adapter.display_name} (Test Framework: {scan_data['test_framework']})",
            f"Source Files: {files_preview}",
            f"Existing Test Suites: {test_suites_str}",
        ]
        lines.extend(manifest_notes)
        lines.append("\nClasses and Public Methods:")

        # Rotate priority target files when generating batches so each task covers a distinct module
        rotated_targets = self._rotated_targets(scan_data, max_files=3, target_offset=target_offset)
        target_set = set(rotated_targets)
        ordered_classes = sorted(
            scan_data["classes"],
            key=lambda c: (0 if c.get("file") in target_set else 1),
        )[:max_classes]

        for cls in ordered_classes:
            pub_methods = cls.get("public_methods", [])
            methods_repr = [str(m) for m in pub_methods] if pub_methods else [str(m) for m in cls["methods"]]
            lines.append(f"- class {cls['name']} (in {cls['file']}): methods={methods_repr}")
            if cls.get("bases"):
                lines.append(f"  Base Classes: {', '.join(cls['bases'])}")
            if pub_methods:
                lines.append(f"  Public Methods: {cls['public_methods']}")
            if cls.get("private_methods"):
                lines.append(f"  Internal Methods: {cls['private_methods']}")
            if cls.get("docstring"):
                lines.append(f"  Doc: {cls['docstring']}")

        public_fns = [fn for fn in scan_data["functions"] if not fn.get("is_private", False)][:max_functions]
        private_fns = [fn for fn in scan_data["functions"] if fn.get("is_private", False)][:max_functions]

        if public_fns:
            lines.append("\nPublic Functions:")
            for fn in public_fns:
                lines.append(f"- function {fn.get('signature', fn['name'])} (in {fn['file']})")
                if fn.get("docstring"):
                    lines.append(f"  Doc: {fn['docstring']}")

        if private_fns:
            lines.append("\nInternal Functions:")
            for fn in private_fns:
                lines.append(f"- function {fn.get('signature', fn['name'])} (in {fn['file']})")

        return "\n".join(lines)

    def _rotated_targets(self, scan_data: Dict[str, Any], max_files: int = 3, target_offset: int = 0) -> List[str]:
        all_ranked = self.adapter.select_targets(
            self.repo_path, scan_data, max_files=max(max_files, len(scan_data.get("files") or []))
        )
        if not all_ranked:
            return []
        if target_offset > 0 and len(all_ranked) > 1:
            shift = target_offset % len(all_ranked)
            all_ranked = all_ranked[shift:] + all_ranked[:shift]
        return all_ranked[:max_files]

    def get_targeted_context(self, max_files: int = 3, max_lines_per_file: int = 250, target_offset: int = 0) -> str:
        """Tier 2: Return Tier 1 blueprint plus manifest constraints, numbered source code, and sample test."""
        scan_data = self.scan()
        tier1 = self.get_context_prompt(target_offset=target_offset)
        target_files = self._rotated_targets(scan_data, max_files=max_files, target_offset=target_offset)
        source_blocks = self.adapter.format_numbered_source(
            self.repo_path, target_files, max_lines_per_file=max_lines_per_file
        )

        sections = [tier1]
        manifest = scan_data.get("manifest") or {}
        constraints: List[str] = []
        if manifest.get("filterwarnings"):
            constraints.append(f"- pytest filterwarnings: {manifest['filterwarnings']} (tests must emit zero warnings)")
        if manifest.get("src_layout"):
            constraints.append("- Package uses `src/` layout; import public package names directly without `src.` prefix.")
        if manifest.get("workspace_packages"):
            ws_names = ", ".join(f"{p['name']} ({p['path']})" for p in manifest["workspace_packages"][:8])
            constraints.append(f"- Monorepo workspace packages: {ws_names}")
        if manifest.get("jsontest_detected"):
            constraints.append("- Repository uses CMake/CTest; place new test in `tests/test_feature_patch.cpp` with standalone `int main()`.")
        if manifest.get("cmake_targets"):
            constraints.append(f"- Available CMake targets: {', '.join(manifest['cmake_targets'])}")
        if constraints:
            sections.append("## Build & Test Constraints\n" + "\n".join(constraints))

        if source_blocks:
            sections.append(f"## Target Source Files (Numbered Lines for Exact Diffing)\n{source_blocks}")

        existing_tests = scan_data.get("existing_tests") or []
        if existing_tests:
            sample_test = self.adapter.format_numbered_source(
                self.repo_path, existing_tests[:1], max_lines_per_file=80
            )
            if sample_test:
                sections.append(f"## Representative Existing Test Pattern\n{sample_test}")

        return "\n\n".join(sections)
