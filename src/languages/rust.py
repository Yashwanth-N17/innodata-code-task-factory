"""
Rust Language Adapter.
Supports Cargo.toml parsing, struct/enum/trait/fn scanning, and `cargo test` execution.
"""

import os
import re
import shutil
import subprocess
from typing import Any, Dict, List, Optional, Tuple

from .base import BaseLanguageAdapter, DEFAULT_IGNORED_DIRS, TestResultMap


_RS_USE_RE = re.compile(r"^\s*(?:pub\s+)?use\s+([^;]+);")
_RS_STRUCT_RE = re.compile(r"^\s*(?:pub(?:\([^)]*\))?\s+)?(struct|enum|trait)\s+([A-Za-z_][A-Za-z0-9_]*)")
_RS_FN_RE = re.compile(r"^\s*(?:(pub)(?:\([^)]*\))?\s+)?(?:(async)\s+)?fn\s+([A-Za-z_][A-Za-z0-9_]*)\s*(?:<[^>]*>)?\(([^)]*)\)(?:\s*->\s*([^\{;]+))?")
_RS_TEST_LINE_RE = re.compile(r"^test\s+(\S+)\s+\.\.\.\s+(ok|FAILED|ignored)")


class RustAdapter(BaseLanguageAdapter):
    """Language adapter for Rust repositories."""

    language_id = "rust"
    display_name = "Rust"
    source_extensions = (".rs",)
    test_extensions = (".rs",)
    test_framework = "cargo test"
    test_file_relpath = os.path.join("tests", "test_feature_patch.rs")
    root_test_filename = "test_patch.rs"

    @classmethod
    def detect_score(cls, repo_path: str) -> int:
        score = 0
        if os.path.isfile(os.path.join(repo_path, "Cargo.toml")):
            score += 45
        rs_count, other_count = 0, 0
        for root, dirs, files in os.walk(repo_path):
            dirs[:] = [d for d in dirs if d not in DEFAULT_IGNORED_DIRS and d != "target"]
            for f in files:
                low = f.lower()
                if low.endswith(".rs"):
                    rs_count += 1
                elif low.endswith((".py", ".ts", ".js", ".cpp", ".h", ".go", ".java")):
                    other_count += 1
            if rs_count + other_count >= 120:
                break
        if rs_count > 0:
            score += int((rs_count / max(1, rs_count + other_count)) * 55)
        return min(100, score)

    def inspect_manifest(self, repo_path: str) -> Dict[str, Any]:
        info: Dict[str, Any] = {
            "language": "rust",
            "package_name": os.path.basename(os.path.abspath(repo_path)),
            "edition": "2021",
            "test_framework": "cargo test",
            "manifests": [],
        }
        cargo = os.path.join(repo_path, "Cargo.toml")
        if os.path.isfile(cargo):
            info["manifests"].append("Cargo.toml")
            try:
                with open(cargo, "r", encoding="utf-8", errors="replace") as f:
                    text = f.read()
                nm = re.search(r'^\s*name\s*=\s*"([^"]+)"', text, re.MULTILINE)
                if nm:
                    info["package_name"] = nm.group(1)
                ed = re.search(r'^\s*edition\s*=\s*"([^"]+)"', text, re.MULTILINE)
                if ed:
                    info["edition"] = ed.group(1)
            except Exception:
                pass
        return info

    def is_test_file(self, rel_path: str, full_path: str) -> bool:
        norm = rel_path.replace("\\", "/")
        parts = norm.split("/")
        return any(p in ("tests", "benches") for p in parts[:-1]) or norm.endswith("_test.rs")

    def scan_file(self, full_path: str, rel_path: str, summary: Dict[str, Any]) -> None:
        try:
            with open(full_path, "r", encoding="utf-8", errors="replace") as f:
                lines = f.read().splitlines()
        except Exception:
            return

        for line in lines:
            um = _RS_USE_RE.match(line)
            if um:
                summary["imports"].append(um.group(1).strip())
                continue
            sm = _RS_STRUCT_RE.match(line)
            if sm:
                summary["classes"].append({
                    "file": rel_path,
                    "name": sm.group(2),
                    "kind": sm.group(1),
                    "docstring": "",
                    "bases": [],
                    "methods": [],
                    "public_methods": [],
                    "private_methods": [],
                    "method_details": [],
                })
                continue
            fm = _RS_FN_RE.match(line)
            if fm:
                is_pub = bool(fm.group(1))
                is_async = bool(fm.group(2))
                fname = fm.group(3)
                params = [p.strip() for p in fm.group(4).split(",") if p.strip()]
                ret = (fm.group(5) or "").strip() or None
                sig = f"{'async ' if is_async else ''}{fname}({', '.join(params)}){f' -> {ret}' if ret else ''}"
                summary["functions"].append({
                    "file": rel_path,
                    "name": fname,
                    "docstring": "",
                    "params": params,
                    "return_type": ret,
                    "signature": sig,
                    "is_private": not is_pub,
                    "is_async": is_async,
                    "decorators": [],
                })

    def run_tests(
        self,
        work_dir: str,
        target_test: Optional[str] = None,
        timeout: int = 120,
        **kwargs,
    ) -> Tuple[int, str, Dict[str, str]]:
        cargo_bin = shutil.which("cargo")
        if not cargo_bin:
            return -1, "cargo is not installed in PATH.", {}
        cmd = [cargo_bin, "test"]
        if target_test:
            cmd.extend(["--test", "test_feature_patch"])
        try:
            res = subprocess.run(
                cmd, cwd=work_dir, capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=timeout,
            )
            out = res.stdout + "\n" + res.stderr
            return res.returncode, out, self.parse_test_output(out)
        except subprocess.TimeoutExpired:
            return -1, f"cargo test timed out after {timeout} seconds.", {}
        except Exception as e:
            return -1, f"cargo test failed: {e}", {}

    @staticmethod
    def parse_test_output(output: str) -> TestResultMap:
        results = TestResultMap()
        current_suite = "cargo"
        reported_count = 0
        summary_count_found = False
        for line in output.splitlines():
            suite_match = re.search(r"\bRunning (.+?) \(", line)
            if suite_match:
                current_suite = suite_match.group(1).replace("\\", "/").strip()
            count_match = re.search(r"\brunning\s+(\d+)\s+tests?\b", line)
            if count_match:
                reported_count += int(count_match.group(1))
                summary_count_found = True
            match = _RS_TEST_LINE_RE.match(line.strip())
            if match:
                test_name, status = match.group(1), match.group(2)
                test_id = f"{current_suite}::{test_name}"
                results[test_id] = (
                    "passed" if status == "ok"
                    else "skipped" if status == "ignored"
                    else "failed"
                )
        results.discovered_count = (
            reported_count if summary_count_found else len(results)
        )
        if summary_count_found and reported_count != len(results):
            results.report_error = (
                f"Cargo reported {reported_count} tests but {len(results)} individual "
                "test results were parsed."
            )
        if re.search(r"^error(?:\[E\d+\])?:|^error: could not compile", output, re.MULTILINE):
            results.report_error = (
                (results.report_error + " " if results.report_error else "")
                + "Cargo reported a compiler/build error."
            )
        return results

    def is_new_test(self, test_name: str) -> bool:
        norm = (test_name or "").lower().replace("\\\\", "/")
        return ("test_feature_patch" in norm) or ("feature_patch" in norm)

    def is_real_failure(self, code: int, out: str) -> bool:
        return code > 0 and "is not installed in PATH" not in out

    def has_no_tests_evidence(self, exit_code: int, diagnostics: str, results: Dict[str, str]) -> bool:
        return (
            exit_code == 0
            and not results
            and bool(re.search(r"\brunning\s+0\s+tests?\b", diagnostics, re.IGNORECASE))
        )

    def canonical_test_id(self, test_id: str) -> str:
        normalized = super().canonical_test_id(test_id)
        return normalized.replace("test_feature_patch::test_feature_patch::", "test_feature_patch::")

    def get_leakage_regexes(self) -> List[re.Pattern]:
        return [re.compile(r"\b[a-zA-Z0-9_\-./]+\.rs:\d+(?::\d+)?\b", re.IGNORECASE)]

    def get_environment_metadata(self, repo_path: str, manifest: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "rust_edition": manifest.get("edition", "2021"),
            "test_command": "cargo test --test test_feature_patch",
        }
