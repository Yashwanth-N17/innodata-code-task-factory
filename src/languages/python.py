"""
Python Language Adapter.
Supports AST-based semantic scanning, `src/`-layout / pyproject.toml inspection,
and isolated pytest execution with JUnit XML telemetry.
"""

import ast
import os
import re
import subprocess
import sys
import tempfile
from collections.abc import Mapping
from typing import Any, Dict, List, Optional, Tuple, Union

try:
    import tomllib
except ImportError:  # pragma: no cover - Python < 3.11 fallback
    try:
        import tomli as tomllib  # type: ignore
    except ImportError:
        tomllib = None  # type: ignore

from .base import BaseLanguageAdapter, DEFAULT_IGNORED_DIRS, parse_junit_xml


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

    for idx, arg in enumerate(kwonly):
        s = arg.arg
        if arg.annotation:
            ann = _safe_unparse(arg.annotation)
            if ann:
                s += f": {ann}"
        if idx < len(kw_defaults) and kw_defaults[idx] is not None:
            d = _safe_unparse(kw_defaults[idx])
            if d is not None:
                s += f" = {d}"
        params.append(s)

    if kwarg:
        s = f"**{kwarg.arg}"
        if kwarg.annotation:
            ann = _safe_unparse(kwarg.annotation)
            if ann:
                s += f": {ann}"
        params.append(s)

    ret_type = _safe_unparse(node.returns) if node.returns else None
    is_async = isinstance(node, ast.AsyncFunctionDef)
    prefix = "async " if is_async else ""
    ret_str = f" -> {ret_type}" if ret_type else ""
    sig_str = f"{prefix}{node.name}({', '.join(params)}){ret_str}"

    decorators = [d for d in (_safe_unparse(dec) for dec in (node.decorator_list or [])) if d]

    return {
        "name": node.name,
        "signature": sig_str,
        "params": params,
        "return_type": ret_type,
        "is_private": node.name.startswith("_"),
        "is_async": is_async,
        "docstring": ast.get_docstring(node) or "",
        "decorators": decorators,
    }


class PythonAdapter(BaseLanguageAdapter):
    """Language adapter for Python repositories (e.g., pallets/click)."""

    language_id = "python"
    display_name = "Python"
    source_extensions = (".py",)
    test_extensions = (".py",)
    test_framework = "pytest"
    test_file_relpath = os.path.join("tests", "test_feature_patch.py")
    root_test_filename = "test_patch.py"
    _NEW_TEST_MODULE = "tests.test_feature_patch"

    @classmethod
    def detect_score(cls, repo_path: str) -> int:
        score = 0
        manifests = ("pyproject.toml", "setup.py", "setup.cfg", "requirements.txt", "pytest.ini", "Pipfile")
        for m in manifests:
            if os.path.isfile(os.path.join(repo_path, m)):
                score += 25
                break
        if os.path.isfile(os.path.join(repo_path, "pyproject.toml")):
            score += 20

        py_count, other_count = 0, 0
        for root, dirs, files in os.walk(repo_path):
            dirs[:] = [d for d in dirs if d not in DEFAULT_IGNORED_DIRS]
            for f in files:
                low = f.lower()
                if low.endswith(".py"):
                    py_count += 1
                elif low.endswith((".ts", ".tsx", ".js", ".cpp", ".cc", ".cxx", ".h", ".hpp", ".go", ".rs")):
                    other_count += 1
            if py_count + other_count >= 120:
                break

        if py_count > 0:
            ratio = py_count / max(1, py_count + other_count)
            score += int(ratio * 55)
        return min(100, score)

    def inspect_manifest(self, repo_path: str) -> Dict[str, Any]:
        info: Dict[str, Any] = {
            "language": "python",
            "package_name": os.path.basename(os.path.abspath(repo_path)),
            "requires_python": ">=3.9",
            "src_layout": os.path.isdir(os.path.join(repo_path, "src")),
            "testpaths": ["tests"],
            "test_framework": "pytest",
            "manifests": [],
        }

        for m in ("pyproject.toml", "setup.py", "setup.cfg", "pytest.ini", "requirements.txt"):
            if os.path.isfile(os.path.join(repo_path, m)):
                info["manifests"].append(m)

        pyproject_path = os.path.join(repo_path, "pyproject.toml")
        if os.path.isfile(pyproject_path) and tomllib is not None:
            try:
                with open(pyproject_path, "rb") as f:
                    data = tomllib.load(f)
                proj = data.get("project", {})
                if proj.get("name"):
                    info["package_name"] = proj["name"]
                if proj.get("requires-python"):
                    info["requires_python"] = proj["requires-python"]
                pytest_opts = data.get("tool", {}).get("pytest", {}).get("ini_options", {})
                if pytest_opts.get("testpaths"):
                    info["testpaths"] = list(pytest_opts["testpaths"])
                if pytest_opts.get("filterwarnings"):
                    info["filterwarnings"] = list(pytest_opts["filterwarnings"])
                if pytest_opts.get("addopts"):
                    info["pytest_addopts"] = pytest_opts["addopts"]
            except Exception:
                pass

        return info

    def is_test_file(self, rel_path: str, full_path: str) -> bool:
        """Distinguish real test suites from source files containing 'test' in the name."""
        norm_path = rel_path.replace("\\", "/")
        parts = norm_path.split("/")
        filename = parts[-1]

        if any(part in ("tests", "test") for part in parts[:-1]):
            return True

        if filename.startswith("test_") or filename.endswith("_test.py"):
            try:
                with open(full_path, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read()
                node = ast.parse(content, filename=rel_path)
                for item in ast.walk(node):
                    if isinstance(item, ast.Import):
                        for n in item.names:
                            if n.name in ("pytest", "unittest"):
                                return True
                    elif isinstance(item, ast.ImportFrom):
                        if item.module in ("pytest", "unittest"):
                            return True
                    elif isinstance(item, ast.ClassDef):
                        bases = [_safe_unparse(b) or "" for b in item.bases]
                        if any("TestCase" in b for b in bases):
                            return True
                        if item.name.startswith("Test"):
                            for body_item in item.body:
                                if isinstance(body_item, (ast.FunctionDef, ast.AsyncFunctionDef)) and body_item.name.startswith("test_"):
                                    return True
                for item in node.body:
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name.startswith("test_"):
                        return True
            except Exception:
                pass
            return False

        return False

    def scan_file(self, full_path: str, rel_path: str, summary: Dict[str, Any]) -> None:
        try:
            with open(full_path, "r", encoding="utf-8", errors="replace") as f:
                node = ast.parse(f.read(), filename=rel_path)

            for item in node.body:
                if isinstance(item, (ast.Import, ast.ImportFrom)):
                    if isinstance(item, ast.Import):
                        for n in item.names:
                            summary["imports"].append(n.name)
                    elif item.module:
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
            pass

    def run_tests(
        self,
        work_dir: str,
        target_test: Optional[str] = None,
        timeout: int = 45,
        **kwargs,
    ) -> Tuple[int, str, Dict[str, str]]:
        python_exe = kwargs.get("python_executable") or sys.executable
        if target_test:
            targets = [target_test]
        else:
            targets = [d for d in ("tests", "test") if os.path.isdir(os.path.join(work_dir, d))]
            if not targets:
                return 0, "No existing test suite found in target repository; baseline clean.", {}

        fd, xml_path = tempfile.mkstemp(suffix=".xml", prefix="junit_")
        os.close(fd)
        cmd = [
            python_exe, "-m", "pytest", "-q", "-p", "no:cacheprovider",
            f"--junitxml={xml_path}", *targets,
        ]

        env = os.environ.copy()
        pp_entries = []
        src_dir = os.path.join(work_dir, "src")
        if os.path.isdir(src_dir):
            pp_entries.append(src_dir)
        pp_entries.append(work_dir)
        if env.get("PYTHONPATH"):
            pp_entries.append(env["PYTHONPATH"])
        env["PYTHONPATH"] = os.pathsep.join(pp_entries)

        try:
            try:
                res = subprocess.run(
                    cmd,
                    cwd=work_dir,
                    env=env,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=timeout,
                )
            except subprocess.TimeoutExpired:
                return -1, f"Test execution timed out after {timeout} seconds.", {}
            except Exception as e:
                return -1, f"Execution failed: {e}", {}

            out = res.stdout + "\n" + res.stderr
            if "No module named pytest" in out:
                return -1, "pytest is not installed in this interpreter. " + out, {}
            return res.returncode, out, parse_junit_xml(xml_path)
        finally:
            try:
                os.remove(xml_path)
            except OSError:
                pass

    def is_real_failure(self, code: int, out: str) -> bool:
        if code not in (1, 2):
            return False
        if "SyntaxError" in out or "IndentationError" in out:
            return False
        if "No module named pytest" in out:
            return False
        return True

    def is_new_test(self, test_id: str) -> bool:
        module = test_id.split("::")[0]
        return module == self._NEW_TEST_MODULE or module.startswith(self._NEW_TEST_MODULE + ".")

    def get_leakage_regexes(self) -> List[re.Pattern]:
        return [
            re.compile(r"\b[a-zA-Z0-9_\-./]+\.py:\d+(?::\d+)?\b", re.IGNORECASE),
        ]

    def get_prompt_context(self) -> Dict[str, str]:
        return {
            "language": "Python",
            "test_framework": "pytest",
            "test_file": "tests/test_feature_patch.py",
            "coder_rules": "Ensure all imports, syntax, and typing are 100% valid Python.",
            "test_rules": (
                "Write a comprehensive pytest test file (`tests/test_feature_patch.py`). "
                "Use standard `pytest` or `unittest` assertions and match the repository package layout (e.g. `src/`)."
            ),
        }

    def get_environment_metadata(self, repo_path: str, manifest: Dict[str, Any]) -> Dict[str, Any]:
        env_meta: Dict[str, Any] = {
            "python_version": manifest.get("requires_python", ">=3.9"),
            "test_command": "pytest tests/test_feature_patch.py",
        }
        if manifest.get("src_layout"):
            env_meta["pythonpath"] = "src"
        return env_meta
