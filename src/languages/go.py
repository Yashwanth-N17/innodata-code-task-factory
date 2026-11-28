"""
Go Language Adapter.
Supports go.mod parsing, Go struct/interface/function scanning, and `go test -json` verification.
"""

import json
import os
import re
import shutil
import subprocess
from typing import Any, Dict, List, Optional, Tuple

from .base import BaseLanguageAdapter, DEFAULT_IGNORED_DIRS, TestResultMap


_GO_IMPORT_RE = re.compile(r'^\s*"([^"]+)"')
_GO_TYPE_RE = re.compile(r"^\s*type\s+([A-Za-z_][A-Za-z0-9_]*)\s+(struct|interface)\s*\{")
_GO_FUNC_RE = re.compile(r"^\s*func\s+(?:\(\s*[A-Za-z_][A-Za-z0-9_]*\s+\*?([A-Za-z_][A-Za-z0-9_]*)\s*\)\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*\(([^)]*)\)(?:\s*([^{]+))?\s*\{")


class GoAdapter(BaseLanguageAdapter):
    """Language adapter for Go repositories."""

    language_id = "go"
    display_name = "Go"
    source_extensions = (".go",)
    test_extensions = ("_test.go",)
    test_framework = "go test"
    test_file_relpath = "feature_patch_test.go"
    root_test_filename = "feature_patch_test.go"

    @classmethod
    def detect_score(cls, repo_path: str) -> int:
        score = 0
        if os.path.isfile(os.path.join(repo_path, "go.mod")):
            score += 45
        go_count, other_count = 0, 0
        for root, dirs, files in os.walk(repo_path):
            dirs[:] = [d for d in dirs if d not in DEFAULT_IGNORED_DIRS]
            for f in files:
                low = f.lower()
                if low.endswith(".go"):
                    go_count += 1
                elif low.endswith((".py", ".ts", ".js", ".cpp", ".h", ".rs", ".java")):
                    other_count += 1
            if go_count + other_count >= 120:
                break
        if go_count > 0:
            score += int((go_count / max(1, go_count + other_count)) * 55)
        return min(100, score)

    def inspect_manifest(self, repo_path: str) -> Dict[str, Any]:
        info: Dict[str, Any] = {
            "language": "go",
            "module_name": os.path.basename(os.path.abspath(repo_path)),
            "go_version": ">=1.21",
            "test_framework": "go test",
            "manifests": [],
        }
        go_mod = os.path.join(repo_path, "go.mod")
        if os.path.isfile(go_mod):
            info["manifests"].append("go.mod")
            try:
                with open(go_mod, "r", encoding="utf-8", errors="replace") as f:
                    text = f.read()
                mod_m = re.search(r"^\s*module\s+(\S+)", text, re.MULTILINE)
                if mod_m:
                    info["module_name"] = mod_m.group(1)
                ver_m = re.search(r"^\s*go\s+(\S+)", text, re.MULTILINE)
                if ver_m:
                    info["go_version"] = f">={ver_m.group(1)}"
            except Exception:
                pass
        return info

    def is_test_file(self, rel_path: str, full_path: str) -> bool:
        return rel_path.lower().endswith("_test.go")

    def scan_file(self, full_path: str, rel_path: str, summary: Dict[str, Any]) -> None:
        try:
            with open(full_path, "r", encoding="utf-8", errors="replace") as f:
                lines = f.read().splitlines()
        except Exception:
            return

        struct_map: Dict[str, Dict[str, Any]] = {}
        for line in lines:
            imp = _GO_IMPORT_RE.match(line)
            if imp:
                summary["imports"].append(imp.group(1))
                continue

            tm = _GO_TYPE_RE.match(line)
            if tm:
                tname, kind = tm.group(1), tm.group(2)
                entry = {
                    "file": rel_path,
                    "name": tname,
                    "kind": kind,
                    "docstring": "",
                    "bases": [],
                    "methods": [],
                    "public_methods": [],
                    "private_methods": [],
                    "method_details": [],
                }
                struct_map[tname] = entry
                summary["classes"].append(entry)
                continue

            fm = _GO_FUNC_RE.match(line)
            if fm:
                recv, fname, params_raw, ret_raw = fm.group(1), fm.group(2), fm.group(3), (fm.group(4) or "").strip()
                params = [p.strip() for p in params_raw.split(",") if p.strip()]
                is_priv = fname[0].islower()
                ret_s = f" {ret_raw}" if ret_raw else ""
                sig = f"{fname}({', '.join(params)}){ret_s}"
                if recv and recv in struct_map:
                    struct_map[recv]["methods"].append(fname)
                    if is_priv:
                        struct_map[recv]["private_methods"].append(sig)
                    else:
                        struct_map[recv]["public_methods"].append(sig)
                else:
                    summary["functions"].append({
                        "file": rel_path,
                        "name": fname,
                        "docstring": "",
                        "params": params,
                        "return_type": ret_raw or None,
                        "signature": sig,
                        "is_private": is_priv,
                        "is_async": False,
                        "decorators": [],
                    })

    def run_tests(
        self,
        work_dir: str,
        target_test: Optional[str] = None,
        timeout: int = 60,
        **kwargs,
    ) -> Tuple[int, str, Dict[str, str]]:
        go_bin = shutil.which("go")
        if not go_bin:
            return -1, "go toolchain is not installed in PATH.", {}
        cmd = [go_bin, "test", "-json", "./..."]
        if target_test:
            rel = os.path.relpath(target_test, work_dir) if os.path.isabs(target_test) else target_test
            pkg_dir = os.path.dirname(rel) or "."
            cmd = [go_bin, "test", "-json", f"./{pkg_dir}" if pkg_dir != "." else "."]
        try:
            res = subprocess.run(
                cmd, cwd=work_dir, capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=timeout,
            )
            diagnostics = res.stdout + "\n" + res.stderr
            return res.returncode, diagnostics, self.parse_test_output(res.stdout)
        except subprocess.TimeoutExpired:
            return -1, f"Go test timed out after {timeout} seconds.", {}
        except Exception as e:
            return -1, f"Go test execution failed: {e}", {}

    @staticmethod
    def parse_test_output(output: str) -> TestResultMap:
        results = TestResultMap()
        parse_errors = 0
        build_failures = 0
        for line in output.splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                if line.strip():
                    parse_errors += 1
                continue
            action = event.get("Action")
            if action == "build-fail":
                build_failures += 1
                continue
            test_name = event.get("Test")
            if test_name and action in ("pass", "fail", "skip"):
                package = event.get("Package", "pkg")
                status = {
                    "pass": "passed",
                    "fail": "failed",
                    "skip": "skipped",
                }[action]
                results[f"{package}::{test_name}"] = status
        results.discovered_count = len(results)
        errors = []
        if parse_errors:
            errors.append(
                f"Go test JSON output contained {parse_errors} malformed event line(s)."
            )
        if build_failures:
            errors.append(f"Go test reported {build_failures} package build failure(s).")
        if errors:
            results.report_error = " ".join(errors)
        return results

    def is_real_failure(self, code: int, out: str) -> bool:
        return code > 0 and "not installed in PATH" not in out

    def has_no_tests_evidence(self, exit_code: int, diagnostics: str, results: Dict[str, str]) -> bool:
        return (
            exit_code == 0
            and not results
            and "[no test files]" in diagnostics.lower()
        )

    def is_new_test(self, test_id: str) -> bool:
        return "FeaturePatch" in test_id or "feature_patch" in test_id.lower()

    def get_leakage_regexes(self) -> List[re.Pattern]:
        return [re.compile(r"\b[a-zA-Z0-9_\-./]+\.go:\d+(?::\d+)?\b", re.IGNORECASE)]

    def get_environment_metadata(self, repo_path: str, manifest: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "go_version": manifest.get("go_version", ">=1.21"),
            "test_command": "go test ./...",
        }
