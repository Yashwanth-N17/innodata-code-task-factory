"""
Feature 5: Polyglot Sandbox Verifier & SWE-bench 3-Step Verification Matrix.
Executes the SWE-bench automated quality gate across Python, TypeScript, C++, or any
registered LanguageAdapter, each step in its own isolated copy:
1. Baseline: Original repo + existing tests -> PASS
2. Negative Check: Original repo + new tests -> FAIL (Fail-to-Pass verified)
3. Positive Check: Repo + solution.diff + all tests -> PASS (Solvability verified)
Includes multi-tier patch application fallback
(git apply -> git apply -C1 -> pure-Python unified-diff applier).
"""

import os
import re
import shutil
import subprocess
import sys
import tempfile
from typing import Dict, List, Optional, Tuple

from .languages import BaseLanguageAdapter, LanguageRegistry, PythonAdapter, parse_junit_xml
from .languages.python import detect_repo_python
from .sandbox_docker import DockerSandboxRunner

_COPY_IGNORE = shutil.ignore_patterns(
    "venv", ".venv", ".git", "__pycache__", ".pytest_cache",
    ".env", "node_modules", "*.pyc", "_factory_build", "cmake-build-debug",
)

_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


# ----------------------------------------------------------------------------
# Pure-Python unified diff applier (tier 3)
# ----------------------------------------------------------------------------

def _strip_path(p: str):
    p = p.split("\t")[0].strip()
    if p == "/dev/null":
        return None
    if p.startswith(("a/", "b/")):
        p = p[2:]
    return p


def _parse_unified_diff(patch_text: str):
    """Parse a unified diff into [{'old', 'new', 'hunks': [(old_start, [(op, text)], old_count)]}]."""
    lines = patch_text.splitlines()
    files, cur, i = [], None, 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("--- ") and i + 1 < len(lines) and lines[i + 1].startswith("+++ "):
            cur = {"old": _strip_path(line[4:]), "new": _strip_path(lines[i + 1][4:]), "hunks": []}
            files.append(cur)
            i += 2
            continue
        m = _HUNK_RE.match(line)
        if m and cur is not None:
            old_start, hunk = int(m.group(1)), []
            old_count = int(m.group(2)) if m.group(2) is not None else 1
            i += 1
            while i < len(lines):
                l = lines[i]
                if l.startswith(("@@", "diff --git")) or (
                    l.startswith("--- ") and i + 1 < len(lines) and lines[i + 1].startswith("+++ ")
                ):
                    break
                if l.startswith("\\"):
                    i += 1
                    continue
                if l[:1] in (" ", "+", "-"):
                    hunk.append((l[0], l[1:]))
                elif l == "":
                    hunk.append((" ", ""))
                else:
                    break
                i += 1
            cur["hunks"].append((old_start, hunk, old_count))
            continue
        i += 1
    return files


def _find_block(lines, block, expected):
    """Position of `block` in `lines`, nearest to `expected`; exact match, then whitespace-trimmed."""
    if not block:
        return min(max(expected, 0), len(lines))
    n = len(block)
    for norm in (lambda s: s, lambda s: s.strip()):
        nb = [norm(x) for x in block]
        cands = [p for p in range(len(lines) - n + 1)
                 if [norm(x) for x in lines[p:p + n]] == nb]
        if cands:
            return min(cands, key=lambda p: abs(p - expected))
    return None


def _apply_hunks(orig_lines, hunks):
    out, offset = list(orig_lines), 0
    for old_start, hunk, old_count in hunks:
        old_block = [t for op, t in hunk if op in (" ", "-")]
        new_block = [t for op, t in hunk if op in (" ", "+")]
        if not old_block and old_count > 0:
            raise ValueError(
                f"malformed hunk @@ -{old_start},{old_count} ...: the header says it covers "
                f"{old_count} existing line(s) but the body has only '+' lines (no context, "
                f"nothing removed), so there is no way to know where to insert them. "
                f"Regenerate the diff with proper context lines.")
        base = old_start if not old_block else max(old_start - 1, 0)
        pos = _find_block(out, old_block, base + offset)
        if pos is None:
            raise ValueError(f"hunk @@ -{old_start} does not match the file content")
        out[pos:pos + len(old_block)] = new_block
        offset = pos - base + len(new_block) - len(old_block)
    return out


# Backward-compatible JUnit helper aliases
_NEW_TEST_MODULE = "tests.test_feature_patch"
_parse_junit = parse_junit_xml


def _is_new_test(test_id: str) -> bool:
    module = test_id.split("::")[0]
    return module == _NEW_TEST_MODULE or module.startswith(_NEW_TEST_MODULE + ".")


# ----------------------------------------------------------------------------
# Result + Verifier
# ----------------------------------------------------------------------------

class VerificationResult:
    def __init__(self):
        self.step1_baseline_pass: bool = False
        self.step2_negative_fail: bool = False
        self.step3_positive_pass: bool = False
        self.patch_applied_cleanly: bool = False
        self.patch_tier: str = ""
        self.fail_to_pass: List[str] = []
        self.pass_to_pass: List[str] = []
        self.warnings: List[str] = []
        self.config: Dict[str, object] = {}
        self.logs: Dict[str, str] = {}

    @property
    def is_valid(self) -> bool:
        """A task is only valid if ALL THREE steps pass the matrix."""
        return (
            self.step1_baseline_pass
            and self.step2_negative_fail
            and self.step3_positive_pass
        )

    def to_dict(self, max_log_chars: int = 2000) -> Dict[str, object]:
        """JSON-serializable validation evidence (for validation_report.json)."""
        def trim(text: str) -> str:
            if len(text) <= max_log_chars:
                return text
            return "...[truncated]\n" + text[-max_log_chars:]

        return {
            "is_valid": self.is_valid,
            "matrix": {
                "step1_baseline": {"expected": "PASS", "passed": self.step1_baseline_pass},
                "step2_negative_check": {"expected": "FAIL", "passed": self.step2_negative_fail},
                "step3_positive_check": {"expected": "PASS", "passed": self.step3_positive_pass},
            },
            "patch": {"applied_cleanly": self.patch_applied_cleanly, "tier": self.patch_tier},
            "fail_to_pass": list(self.fail_to_pass),
            "pass_to_pass": list(self.pass_to_pass),
            "warnings": list(self.warnings),
            "config": dict(self.config),
            "logs": {name: trim(text) for name, text in self.logs.items()},
        }


class SandboxVerifier:
    def __init__(
        self,
        repo_path: str,
        strict_patch: bool = False,
        timeout: int = 45,
        python_executable: str = None,
        language: Optional[str] = None,
        adapter: Optional[BaseLanguageAdapter] = None,
        sandbox: str = "local",
        docker_image: Optional[str] = None,
    ):
        self.repo_path = os.path.abspath(repo_path)
        self.strict_patch = strict_patch
        self.timeout = timeout
        self.python_executable = python_executable or detect_repo_python(self.repo_path) or sys.executable
        self.adapter: BaseLanguageAdapter = adapter or LanguageRegistry.get_adapter(self.repo_path, language=language)
        self.sandbox = (sandbox or "local").strip().lower()
        self.docker_image = docker_image
        if self.sandbox == "docker" and not DockerSandboxRunner.is_available():
            raise RuntimeError("Docker sandbox requested, but Docker CLI/daemon is unavailable.")
        self.last_patch_tier = ""

    def _execute_tests(self, work_dir: str, target_test: Optional[str] = None) -> Tuple[int, str, Dict[str, str]]:
        """Dispatch test execution to either local subprocess or isolated Docker container."""
        if self.sandbox == "docker":
            inject = getattr(self.adapter, "_inject_cmake_test_target", None)
            if callable(inject):
                try:
                    inject(work_dir, target_test)
                except Exception:
                    pass
            return DockerSandboxRunner.run_tests(
                work_dir=work_dir,
                adapter=self.adapter,
                target_test=target_test,
                timeout=self.timeout,
                image=self.docker_image,
            )
        return self.adapter.run_tests(
            work_dir, target_test=target_test, timeout=self.timeout, python_executable=self.python_executable
        )

    # ---- helpers -----------------------------------------------------------

    def _fresh_copy(self, temp_dir: str, name: str) -> str:
        """Isolated copy of the repo prepared for the target language's sandbox."""
        dst = os.path.join(temp_dir, name)
        shutil.copytree(self.repo_path, dst, ignore=_COPY_IGNORE)
        self.adapter.prepare_sandbox(self.repo_path, dst)
        return dst

    def _write_test(self, work_dir: str, test_code: str) -> str:
        return self.adapter.write_test_file(work_dir, test_code)

    @staticmethod
    def _is_real_failure(code: int, out: str) -> bool:
        return PythonAdapter().is_real_failure(code, out)

    # ---- main entry --------------------------------------------------------

    def verify(self, solution_diff: str, test_code: str) -> VerificationResult:
        """Run the 3-step matrix, each step in its own clean copy."""
        result = VerificationResult()
        result.config = {
            "timeout_seconds": self.timeout,
            "strict_patch": self.strict_patch,
            "language": self.adapter.language_id,
            "sandbox": self.sandbox,
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            # Step 1: Baseline (original repo + existing tests -> PASS)
            base_dir = self._fresh_copy(temp_dir, "step1_baseline")
            code, out, base_results = self._execute_tests(base_dir)
            result.logs["step1_baseline"] = out
            result.step1_baseline_pass = (code == 0)

            # Step 2: Negative check (original repo + new tests -> real FAIL)
            neg_dir = self._fresh_copy(temp_dir, "step2_negative")
            test_path = self.adapter.write_test_file(neg_dir, test_code)
            code, out, _ = self._execute_tests(neg_dir, target_test=test_path)
            result.logs["step2_negative_check"] = out
            result.step2_negative_fail = self.adapter.is_real_failure(code, out)

            # Step 3: Positive check (repo + solution.diff + all tests -> PASS)
            pos_dir = self._fresh_copy(temp_dir, "step3_positive")
            patch_file_path = os.path.join(temp_dir, "solution.patch")
            with open(patch_file_path, "w", encoding="utf-8", newline="\n") as f:
                f.write(solution_diff)

            patch_code, patch_out = self._apply_patch(pos_dir, patch_file_path)
            result.patch_tier = self.last_patch_tier
            if patch_code == 0 and self.strict_patch and self.last_patch_tier == "manual":
                patch_code, patch_out = 1, (
                    "Strict mode: git could not apply this diff; only the "
                    "pure-Python fallback did. " + patch_out
                )
            if solution_diff and not solution_diff.endswith("\n"):
                warning = (
                    "solution.diff does not end with a newline; `git apply` rejects such a "
                    "diff as a 'corrupt patch'. End the diff with a trailing newline."
                )
                result.warnings.append(warning)
                patch_out = f"WARNING: {warning} | {patch_out}"
            result.logs["patch_application"] = patch_out
            result.patch_applied_cleanly = (patch_code == 0)

            if result.patch_applied_cleanly:
                self.adapter.write_test_file(pos_dir, test_code)
                all_code, all_out, after = self._execute_tests(pos_dir)
                result.logs["step3_positive_check"] = all_out

                result.fail_to_pass = sorted(
                    t for t, st in after.items() if self.adapter.is_new_test(t) and st == "passed"
                )
                result.pass_to_pass = sorted(
                    t for t, st in after.items()
                    if not self.adapter.is_new_test(t) and st == "passed"
                    and base_results.get(t) == "passed"
                )
                result.step3_positive_pass = (all_code == 0 and len(result.fail_to_pass) > 0)
            else:
                result.logs["step3_positive_check"] = f"Patch failed to apply: {patch_out}"
                result.step3_positive_pass = False

        return result

    # ---- backward-compatible test execution helpers ------------------------

    def _run_pytest(self, cwd: str, target_test: str = None) -> Tuple[int, str]:
        code, out, _ = self._run_pytest_detailed(cwd, target_test)
        return code, out

    def _run_pytest_detailed(self, cwd: str, target_test: str = None) -> Tuple[int, str, Dict[str, str]]:
        return PythonAdapter().run_tests(
            cwd, target_test=target_test, timeout=self.timeout, python_executable=self.python_executable
        )

    # ---- patch application -------------------------------------------------

    def _apply_patch(self, cwd: str, patch_path: str) -> Tuple[int, str]:
        """Apply a diff with a three-tier fallback; records the tier in self.last_patch_tier."""
        self.last_patch_tier = ""
        git_err = ""
        try:
            for cmd in (
                ["git", "init"],
                ["git", "config", "user.name", "Verifier"],
                ["git", "config", "user.email", "verifier@factory.local"],
                ["git", "add", "."],
                ["git", "commit", "-m", "baseline_snapshot"],
            ):
                subprocess.run(cmd, cwd=cwd, capture_output=True)

            tiers = (
                ("git_apply", ["git", "apply", "--ignore-whitespace", "--recount", patch_path]),
                ("git_apply_C1", ["git", "apply", "-C1", "--ignore-whitespace", patch_path]),
            )
            for name, cmd in tiers:
                res = subprocess.run(
                    cmd, cwd=cwd, capture_output=True, text=True,
                    encoding="utf-8", errors="replace",
                )
                if res.returncode == 0:
                    self.last_patch_tier = name
                    return 0, f"Patch applied successfully ({name})."
                git_err = res.stderr or res.stdout or git_err
        except Exception as e:
            git_err = str(e)

        code, msg = self._manual_apply_patch(cwd, patch_path, git_err)
        if code == 0:
            self.last_patch_tier = "manual"
            msg += f" | git apply had failed with: {git_err.strip()[:400] or 'no error output'}"
        return code, msg

    def _manual_apply_patch(self, cwd: str, patch_path: str, git_err: str = "") -> Tuple[int, str]:
        """Pure-Python unified diff applier used only when `git apply` fails."""
        try:
            with open(patch_path, "r", encoding="utf-8", errors="replace") as f:
                files = _parse_unified_diff(f.read())
            if not files:
                return 1, f"Manual patch failed: no valid diff hunks found. {git_err}".strip()

            def _safe(rel):
                if not rel:
                    return None
                if os.path.isabs(rel):
                    return None
                norm = os.path.normpath(rel)
                if norm == ".." or norm.startswith("../") or "/../" in norm:
                    return None
                full = os.path.realpath(os.path.join(cwd, norm))
                if os.path.commonpath([full, os.path.realpath(cwd)]) != os.path.realpath(cwd):
                    return None
                return full

            planned = []
            for entry in files:
                old_p, new_p, hunks = entry["old"], entry["new"], entry["hunks"]
                if new_p is None and old_p:
                    p = _safe(old_p)
                    if p is None:
                        return 1, "Manual patch failed: unsafe path."
                    planned.append((p, None))
                    continue

                target = _safe(new_p or old_p)
                if target is None:
                    return 1, "Manual patch failed: unsafe path."
                if old_p is None:
                    orig = []
                else:
                    src = _safe(old_p)
                    if src is None:
                        return 1, "Manual patch failed: unsafe path."
                    if not os.path.exists(src):
                        return 1, f"Manual patch failed: target file {old_p} not found."
                    with open(src, "r", encoding="utf-8", errors="replace") as f:
                        orig = f.read().splitlines()

                updated = _apply_hunks(orig, hunks)
                planned.append((target, "\n".join(updated) + "\n"))

            for path, content in planned:
                if content is None:
                    if os.path.exists(path):
                        os.remove(path)
                else:
                    parent = os.path.dirname(path)
                    if parent:
                        os.makedirs(parent, exist_ok=True)
                    with open(path, "w", encoding="utf-8", newline="\n") as f:
                        f.write(content)
            return 0, "Patch applied successfully (pure-Python fallback)."
        except Exception as e:
            return 1, f"Manual patch failed: {e}"
