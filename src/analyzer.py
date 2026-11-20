"""
Member 1: Codebase Analyzer.
Crawls a target repository, parses Python AST to extract classes, functions,
docstrings, and existing test suites without running arbitrary code.
"""

import ast
import os
from typing import Dict, List, Any


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
                if isinstance(item, ast.ClassDef):
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
        except Exception as e:
            # Skip unparseable files
            pass

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
