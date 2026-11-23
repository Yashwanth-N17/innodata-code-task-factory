"""
Feature 1: Codebase Semantic Scanner & AST Symbol Extractor.
Crawls a target repository and parses Python AST trees to extract classes, public functions,
docstrings, imports, method signatures, parameters, return types, class inheritance,
and existing test suites without executing arbitrary code.

Input: Target repository directory path.
Output: Structured dictionary of symbols + formatted context summary string.
"""

import ast
import os
import re
from collections.abc import Mapping
from typing import Dict, List, Any, Optional, Union


class MethodSignature(str, Mapping):
    """Represents a method or function signature string with rich AST metadata."""

    def __new__(cls, sig: str, metadata: Dict[str, Any]):
        obj = super().__new__(cls, sig)
        obj._metadata = dict(metadata)
        obj.name = metadata.get("name", "")
        obj.signature = sig
        obj.params = metadata.get("params", [])
        obj.return_type = metadata.get("return_type")
        obj.is_private = metadata.get("is_private", False)
        obj.is_async = metadata.get("is_async", False)
        obj.docstring = metadata.get("docstring", "")
        obj.decorators = metadata.get("decorators", [])
        return obj

    def __getitem__(self, key):
        if isinstance(key, str):
            return self._metadata[key]
        return super().__getitem__(key)

    def __iter__(self):
        return iter(self._metadata)

    def __len__(self):
        return len(self._metadata)

    def get(self, key, default=None):
        return self._metadata.get(key, default)

    def keys(self):
        return self._metadata.keys()

    def values(self):
        return self._metadata.values()

    def items(self):
        return self._metadata.items()


def _safe_unparse(node: Optional[ast.AST]) -> Optional[str]:
    """Safely convert an AST node to source string."""
    if node is None:
        return None
    try:
        return ast.unparse(node)
    except Exception:
        return None


def _extract_signature(node: Union[ast.FunctionDef, ast.AsyncFunctionDef]) -> Dict[str, Any]:
    """Extract complete parameter list, annotations, defaults, and return type."""
    args = node.args
    posonly = args.posonlyargs or []
    normargs = args.args or []
    vararg = args.vararg
    kwonly = args.kwonlyargs or []
    kw_defaults = args.kw_defaults or []
    kwarg = args.kwarg
    defaults = args.defaults or []

    all_pos = posonly + normargs
    offset = len(all_pos) - len(defaults)
    params: List[str] = []

    for idx, arg in enumerate(posonly):
        s = arg.arg
        if arg.annotation:
            ann = _safe_unparse(arg.annotation)
            if ann:
                s += f": {ann}"
        if idx >= offset:
            d = _safe_unparse(defaults[idx - offset])
            if d is not None:
                s += f" = {d}"
        params.append(s)

    if posonly:
        params.append("/")

    for idx, arg in enumerate(normargs, start=len(posonly)):
        s = arg.arg
        if arg.annotation:
            ann = _safe_unparse(arg.annotation)
            if ann:
                s += f": {ann}"
        if idx >= offset:
            d = _safe_unparse(defaults[idx - offset])
            if d is not None:
                s += f" = {d}"
        params.append(s)

    if vararg:
        s = f"*{vararg.arg}"
        if vararg.annotation:
            ann = _safe_unparse(vararg.annotation)
            if ann:
                s += f": {ann}"
        params.append(s)
    elif kwonly:
        params.append("*")

    for arg, d in zip(kwonly, kw_defaults):
        s = arg.arg
        if arg.annotation:
            ann = _safe_unparse(arg.annotation)
            if ann:
                s += f": {ann}"
        if d is not None:
            dv = _safe_unparse(d)
            if dv is not None:
                s += f" = {dv}"
        params.append(s)

    if kwarg:
        s = f"**{kwarg.arg}"
        if kwarg.annotation:
            ann = _safe_unparse(kwarg.annotation)
            if ann:
                s += f": {ann}"
        params.append(s)

    ret = _safe_unparse(node.returns)
    ret_str = f" -> {ret}" if ret else ""
    param_str = ", ".join(params)
    prefix = "async " if isinstance(node, ast.AsyncFunctionDef) else ""
    sig = f"{prefix}{node.name}({param_str}){ret_str}"

    decorators = [_safe_unparse(d) for d in getattr(node, "decorator_list", []) if _safe_unparse(d)]

    return {
        "name": node.name,
        "signature": sig,
        "params": params,
        "return_type": ret,
        "is_private": node.name.startswith("_"),
        "is_async": isinstance(node, ast.AsyncFunctionDef),
        "docstring": ast.get_docstring(node) or "",
        "decorators": decorators,
    }


class CodebaseAnalyzer:
    def __init__(self, repo_path: str):
        self.repo_path = os.path.abspath(repo_path)

    def _is_test_file(self, rel_path: str, full_path: str) -> bool:
        """Accurately identify test suites vs source files.

        Files are classified as test suites only if located in test directories
        (e.g. tests/, test/, unit_tests/, etc.) or if they actually define test suites
        (e.g. test_*.py / *_test.py with test functions or TestCase classes).
        Source files named like `test_agent.py` in source folders are NOT misclassified.
        """
        parts = os.path.normpath(rel_path).split(os.sep)
        test_dirs = {"tests", "test", "testing", "unit_tests", "integration_tests"}

        # 1. Directory check: Any parent directory named in test_dirs
        if any(p.lower() in test_dirs for p in parts[:-1]):
            return True
        # If repo_path itself is named like a test dir (e.g. CodebaseAnalyzer("tests"))
        if os.path.basename(self.repo_path).lower() in test_dirs:
            return True

        # 2. Filename check with AST validation
        filename = parts[-1].lower()
        if filename.startswith("test_") or filename.endswith("_test.py"):
            try:
                with open(full_path, "r", encoding="utf-8") as f:
                    tree = ast.parse(f.read(), filename=rel_path)
                for node in ast.walk(tree):
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_"):
                        return True
                    elif isinstance(node, ast.ClassDef):
                        for base in node.bases:
                            base_str = _safe_unparse(base) or ""
                            if "TestCase" in base_str:
                                return True
                        for item in node.body:
                            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name.startswith("test_"):
                                return True
            except Exception:
                pass
            return False

        return False

    def scan(self) -> Dict[str, Any]:
        """Scan the repository and return structured summary of symbols and files."""
        summary = {
            "repo_name": os.path.basename(self.repo_path),
            "files": [],
            "classes": [],
            "functions": [],
            "imports": [],
            "existing_tests": [],
            "class_hierarchy": {},
            "inheritance": {},
        }

        ignored_dirs = {
            ".git", "__pycache__", "venv", ".venv", ".pytest_cache",
            ".agents", "build", "dist", "node_modules", ".tox", ".mypy_cache",
        }

        for root, dirs, files in os.walk(self.repo_path):
            # Ignore git, venv, cache, agents, build
            dirs[:] = sorted([d for d in dirs if d not in ignored_dirs])

            for file in sorted(files):
                if file.endswith(".py"):
                    full_path = os.path.join(root, file)
                    rel_path = os.path.relpath(full_path, self.repo_path)

                    if self._is_test_file(rel_path, full_path):
                        summary["existing_tests"].append(rel_path)
                    else:
                        summary["files"].append(rel_path)
                        self._parse_file(full_path, rel_path, summary)

        # Build class hierarchy mapping (class name -> list of base class names)
        for cls in summary["classes"]:
            summary["class_hierarchy"][cls["name"]] = cls.get("bases", [])
        summary["inheritance"] = summary["class_hierarchy"]

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
                    bases = [_safe_unparse(b) for b in item.bases if _safe_unparse(b) is not None]
                    method_defs = [m for m in item.body if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef))]
                    method_details = [_extract_signature(m) for m in method_defs]

                    public_methods = [
                        MethodSignature(m["signature"], m)
                        for m in method_details if not m["is_private"]
                    ]
                    private_methods = [
                        MethodSignature(m["signature"], m)
                        for m in method_details if m["is_private"]
                    ]
                    methods = [
                        MethodSignature(m["name"], m)
                        for m in method_details
                    ]

                    summary["classes"].append({
                        "file": rel_path,
                        "name": item.name,
                        "docstring": ast.get_docstring(item) or "",
                        "bases": bases,
                        "methods": methods,
                        "public_methods": public_methods,
                        "private_methods": private_methods,
                        "method_details": method_details,
                    })

                elif isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    fn_sig = _extract_signature(item)
                    summary["functions"].append({
                        "file": rel_path,
                        "name": fn_sig["name"],
                        "docstring": fn_sig["docstring"],
                        "params": fn_sig["params"],
                        "return_type": fn_sig["return_type"],
                        "signature": fn_sig["signature"],
                        "is_private": fn_sig["is_private"],
                        "is_async": fn_sig["is_async"],
                        "decorators": fn_sig.get("decorators", []),
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
        test_suites_str = ", ".join(scan_data["existing_tests"]) if scan_data["existing_tests"] else "None"
        lines = [
            f"Repository: {scan_data['repo_name']}",
            f"Source Files: {', '.join(scan_data['files'])}",
            f"Existing Test Suites: {test_suites_str}",
            "\nClasses and Public Methods:",
        ]
        for cls in scan_data["classes"]:
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

        public_fns = [fn for fn in scan_data["functions"] if not fn.get("is_private", False)]
        private_fns = [fn for fn in scan_data["functions"] if fn.get("is_private", False)]

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
