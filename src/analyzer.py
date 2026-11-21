"""
Feature 1: Codebase Semantic Scanner & AST Symbol Extractor.
Crawls a target repository and parses Python AST trees to extract classes, public functions,
docstrings, imports, and existing test suites without executing arbitrary code.

Input: Target repository directory path.
Output: Structured dictionary of symbols + formatted context summary string.
"""

import ast
import os
from typing import Dict, List, Any, Optional


class CodebaseAnalyzer:
    def __init__(self, repo_path: str):
        self.repo_path = os.path.abspath(repo_path)

    def scan(self) -> Dict[str, Any]:
        """Scan the repository and return structured summary of symbols and files."""
        summary = {
            "repo_name": os.path.basename(self.repo_path),
            "files": [],
            "classes": [],
            "functions": [],
            "imports": [],
            "existing_tests": [],
        }

        for root, dirs, files in os.walk(self.repo_path):
            # Ignore git, venv, cache
            dirs[:] = [d for d in dirs if d not in {".git", "__pycache__", "venv", ".venv", ".pytest_cache"}]

            for file in files:
                if file.endswith(".py"):
                    full_path = os.path.join(root, file)
                    rel_path = os.path.relpath(full_path, self.repo_path)

                    if "test" in rel_path.lower():
                        summary["existing_tests"].append(rel_path)
                    else:
                        summary["files"].append(rel_path)
                        self._parse_file(full_path, rel_path, summary)

        return summary

    def _parse_file(self, full_path: str, rel_path: str, summary: Dict[str, Any]) -> None:
        try:
            with open(full_path, "r", encoding="utf-8") as f:
                node = ast.parse(f.read(), filename=rel_path)

            for item in node.body:
                if isinstance(item, (ast.Import, ast.ImportFrom)):
                    if isinstance(item, ast.Import):
                        for n in item.names:
                            summary["imports"].append(n.name)
                    else:
                        if item.module:
                            summary["imports"].append(item.module)
                elif isinstance(item, ast.ClassDef):
                    methods = [m.name for m in item.body if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef))]
                    summary["classes"].append({
                        "file": rel_path,
                        "name": item.name,
                        "docstring": ast.get_docstring(item) or "",
                        "methods": methods,
                    })
                elif isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    summary["functions"].append({
                        "file": rel_path,
                        "name": item.name,
                        "docstring": ast.get_docstring(item) or "",
                    })
        except Exception:
            # Skip unparseable files
            pass

    def get_file_content(self, rel_path: str) -> Optional[str]:
        """Retrieve full source code of a file within the repository."""
        full_path = os.path.join(self.repo_path, rel_path)
        if os.path.exists(full_path) and os.path.isfile(full_path):
            with open(full_path, "r", encoding="utf-8") as f:
                return f.read()
        return None

    def get_primary_target(self) -> Dict[str, Any]:
        """Return the primary target class or module to benchmark."""
        scan_data = self.scan()
        if scan_data["classes"]:
            target_cls = scan_data["classes"][0]
            content = self.get_file_content(target_cls["file"])
            return {
                "file": target_cls["file"],
                "class_name": target_cls["name"],
                "content": content or "",
            }
        elif scan_data["files"]:
            target_file = scan_data["files"][0]
            content = self.get_file_content(target_file)
            return {
                "file": target_file,
                "class_name": "Module",
                "content": content or "",
            }
        return {"file": "main.py", "class_name": "App", "content": ""}

    def get_context_prompt(self) -> str:
        """Format the parsed symbols into a prompt-friendly text summary."""
        scan_data = self.scan()
        lines = [
            f"Repository: {scan_data['repo_name']}",
            f"Source Files: {', '.join(scan_data['files'])}",
            f"Existing Test Suites: {', '.join(scan_data['existing_tests'])}",
            "\nClasses and Public Methods:",
        ]
        for cls in scan_data["classes"]:
            lines.append(f"- class {cls['name']} (in {cls['file']}): methods={cls['methods']}")
            if cls["docstring"]:
                lines.append(f"  Doc: {cls['docstring']}")

        for fn in scan_data["functions"]:
            lines.append(f"- function {fn['name']} (in {fn['file']})")

        return "\n".join(lines)
