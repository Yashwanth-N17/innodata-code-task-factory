"""
Feature 5: Sandbox Verifier & SWE-bench 3-Step Verification Matrix.
Executes the SWE-bench automated quality gate, each step in its own isolated copy:
1. Baseline: Original repo + existing tests -> PASS
2. Negative Check: Original repo + new tests -> FAIL (Fail-to-Pass verified)
3. Positive Check: Repo + solution.diff + all tests -> PASS (Solvability verified)
Includes multi-tier patch application fallback
(git apply -> git apply -C1 -> pure-Python unified-diff applier).

Input: Target repository directory + solution.diff + test_patch.py.
Output: VerificationResult object with step statuses and execution logs.
"""

import os
import re
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from typing import Dict, List, Tuple

_COPY_IGNORE = shutil.ignore_patterns(
    "venv", ".venv", ".git", "__pycache__", ".pytest_cache",
    ".env", "node_modules", "*.pyc",
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
                if l.startswith("\\"):          # "\ No newline at end of file"
                    i += 1
                    continue
                if l[:1] in (" ", "+", "-"):
                    hunk.append((l[0], l[1:]))
                elif l == "":
                    hunk.append((" ", ""))      # blank context line stripped by an editor
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



# ----------------------------------------------------------------------------
# Per-test results (parsed from pytest's JUnit XML)
# ----------------------------------------------------------------------------

_NEW_TEST_MODULE = "tests.test_feature_patch"


def _parse_junit(xml_path: str) -> Dict[str, str]:
    """Return {test_id: 'passed'|'failed'|'error'|'skipped'} from a pytest JUnit XML file."""
    results: Dict[str, str] = {}
    try:
        root = ET.parse(xml_path).getroot()
    except Exception:
        return results
    for case in root.iter("testcase"):
        test_id = f"{case.get('classname', '')}::{case.get('name', '')}"
        status = "passed"
        for child in case:
            if child.tag == "failure":
                status = "failed"
            elif child.tag == "error":
                status = "error"
            elif child.tag == "skipped":
                status = "skipped"
        results[test_id] = status
    return results


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
        self.patch_tier: str = ""   # "git_apply" | "git_apply_C1" | "manual" | ""
        self.fail_to_pass: List[str] = []   # new tests: not passing before the patch, passing after
        self.pass_to_pass: List[str] = []   # existing tests: passing before AND after the patch
        self.warnings: List[str] = []       # non-fatal problems found in the inputs
        self.config: Dict[str, object] = {}  # settings used for this run (for transparency)
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
        """JSON-serializable validation evidence (for validation_report.json).
        Logs are trimmed to their last `max_log_chars` characters."""
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
    def __init__(self, repo_path: str, strict_patch: bool = False,
                 timeout: int = 45, python_executable: str = None):
        """
        repo_path:         repository to verify (never modified; each step uses a copy)
        strict_patch:      True -> reject diffs that only the pure-Python fallback can apply
        timeout:           seconds allowed per pytest run (raise it for slow test suites)
        python_executable: interpreter used to run pytest. Defaults to the factory's own
                           Python; point it at the target repo's venv if the repo has
                           dependencies the factory's environment lacks (pytest must be
                           installed there).
        """
        self.repo_path = os.path.abspath(repo_path)
        self.strict_patch = strict_patch
        self.timeout = timeout
        self.python_executable = python_executable or sys.executable
        self.last_patch_tier = ""

    # ---- helpers -----------------------------------------------------------

    def _fresh_copy(self, temp_dir: str, name: str) -> str:
        """Isolated copy of the repo (no venv/.git/caches) for one matrix step."""
        dst = os.path.join(temp_dir, name)
        shutil.copytree(self.repo_path, dst, ignore=_COPY_IGNORE)
        return dst

    @staticmethod
    def _write_test(work_dir: str, test_code: str) -> str:
        path = os.path.join(work_dir, "tests", "test_feature_patch.py")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(test_code)
        return path

    @staticmethod
    def _is_real_failure(code: int, out: str) -> bool:
        """Negative check must be a genuine test failure, not a crash.
        pytest exit 1 = tests failed, 2 = collection error (e.g. ImportError
        because the feature doesn't exist yet). Reject timeouts (-1), 'no tests
        collected' (5), internal/usage errors (3, 4) and broken test files."""
        if code not in (1, 2):
            return False
        if "SyntaxError" in out or "IndentationError" in out:
            return False
        if "No module named pytest" in out:
            return False
        return True

    # ---- main entry --------------------------------------------------------

    def verify(self, solution_diff: str, test_code: str) -> VerificationResult:
        """Run the 3-step matrix, each step in its own clean copy."""
        result = VerificationResult()
        result.config = {"timeout_seconds": self.timeout, "strict_patch": self.strict_patch}

        with tempfile.TemporaryDirectory() as temp_dir:
            # Step 1: Baseline (original repo + existing tests -> PASS)
            base_dir = self._fresh_copy(temp_dir, "step1_baseline")
            code, out, base_results = self._run_pytest_detailed(base_dir)
            result.logs["step1_baseline"] = out
            result.step1_baseline_pass = (code == 0)

            # Step 2: Negative check (original repo + new tests -> real FAIL)
            neg_dir = self._fresh_copy(temp_dir, "step2_negative")
            test_path = self._write_test(neg_dir, test_code)
            code, out = self._run_pytest(neg_dir, target_test=test_path)
            result.logs["step2_negative_check"] = out
            result.step2_negative_fail = self._is_real_failure(code, out)

            # Step 3: Positive check (repo + solution.diff + all tests -> PASS)
            pos_dir = self._fresh_copy(temp_dir, "step3_positive")
            patch_file_path = os.path.join(temp_dir, "solution.patch")
            with open(patch_file_path, "w", encoding="utf-8", newline="\n") as f:
                f.write(solution_diff)

            patch_code, patch_out = self._apply_patch(pos_dir, patch_file_path)
            result.patch_tier = self.last_patch_tier
            if patch_code == 0 and self.strict_patch and self.last_patch_tier == "manual":
                patch_code, patch_out = 1, ("Strict mode: git could not apply this diff; only the "
                                            "pure-Python fallback did. " + patch_out)
            if solution_diff and not solution_diff.endswith("\n"):
                warning = ("solution.diff does not end with a newline; `git apply` rejects such a "
                           "diff as a 'corrupt patch'. End the diff with a trailing newline.")
                result.warnings.append(warning)
                patch_out = f"WARNING: {warning} | {patch_out}"
            result.logs["patch_application"] = patch_out
            result.patch_applied_cleanly = (patch_code == 0)

            if result.patch_applied_cleanly:
                self._write_test(pos_dir, test_code)
                # One full run: existing tests + the new test file together
                all_code, all_out, after = self._run_pytest_detailed(pos_dir)
                result.logs["step3_positive_check"] = all_out

                result.fail_to_pass = sorted(
                    t for t, st in after.items() if _is_new_test(t) and st == "passed")
                result.pass_to_pass = sorted(
                    t for t, st in after.items()
                    if not _is_new_test(t) and st == "passed"
                    and base_results.get(t) == "passed")
                # Valid only if everything passes AND at least one new test proves the feature
                result.step3_positive_pass = (all_code == 0 and len(result.fail_to_pass) > 0)
            else:
                result.logs["step3_positive_check"] = f"Patch failed to apply: {patch_out}"
                result.step3_positive_pass = False

        return result

    # ---- test execution ----------------------------------------------------

    def _run_pytest(self, cwd: str, target_test: str = None) -> Tuple[int, str]:
        """Run pytest (required, no unittest fallback). Returns (exit_code, output)."""
        code, out, _ = self._run_pytest_detailed(cwd, target_test)
        return code, out

    def _run_pytest_detailed(self, cwd: str, target_test: str = None) -> Tuple[int, str, Dict[str, str]]:
        """Run pytest and also return per-test results {test_id: status}."""
        if target_test:
            targets = [target_test]
        else:
            targets = [d for d in ("tests", "test") if os.path.isdir(os.path.join(cwd, d))]
            if not targets:
                return 0, "No existing test suite found in target repository; baseline clean.", {}

        fd, xml_path = tempfile.mkstemp(suffix=".xml", prefix="junit_")
        os.close(fd)
        cmd = [self.python_executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
               f"--junitxml={xml_path}", *targets]
        try:
            try:
                res = subprocess.run(
                    cmd, cwd=cwd, capture_output=True, text=True,
                    encoding="utf-8", errors="replace", timeout=self.timeout,
                )
            except subprocess.TimeoutExpired:
                return -1, f"Test execution timed out after {self.timeout} seconds.", {}
            except Exception as e:
                return -1, f"Execution failed: {e}", {}

            out = res.stdout + "\n" + res.stderr
            if "No module named pytest" in out:
                return -1, "pytest is not installed in this interpreter. " + out, {}
            return res.returncode, out, _parse_junit(xml_path)
        finally:
            try:
                os.remove(xml_path)
            except OSError:
                pass

    # ---- patch application -------------------------------------------------

    def _apply_patch(self, cwd: str, patch_path: str) -> Tuple[int, str]:
        """Apply a diff with a three-tier fallback; records the tier in self.last_patch_tier."""
        self.last_patch_tier = ""
        git_err = ""
        try:
            for cmd in (["git", "init"],
                        ["git", "config", "user.name", "Verifier"],
                        ["git", "config", "user.email", "verifier@factory.local"],
                        ["git", "add", "."],
                        ["git", "commit", "-m", "baseline_snapshot"]):
                subprocess.run(cmd, cwd=cwd, capture_output=True)

            tiers = (
                ("git_apply", ["git", "apply", "--ignore-whitespace", "--recount", patch_path]),
                ("git_apply_C1", ["git", "apply", "-C1", "--ignore-whitespace", patch_path]),
            )
            for name, cmd in tiers:
                res = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                                     encoding="utf-8", errors="replace")
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
        """Pure-Python unified-diff applier: context-checked, multi-file, all-or-nothing."""
        try:
            with open(patch_path, "r", encoding="utf-8") as f:
                file_patches = _parse_unified_diff(f.read())
            if not file_patches:
                return 1, f"No file patches found in diff. Git error: {git_err}"

            staged = {}                      # relative path -> new text, or None to delete
            for fp in file_patches:
                if fp["old"] is None:        # new file
                    orig, ends_nl = [], True
                else:
                    src = os.path.join(cwd, fp["old"])
                    if not os.path.isfile(src):
                        return 1, f"Target file does not exist in repo: {fp['old']}. Git error: {git_err}"
                    with open(src, "r", encoding="utf-8") as f:
                        text = f.read()
                    orig, ends_nl = text.splitlines(), text.endswith("\n")
                new_lines = _apply_hunks(orig, fp["hunks"])
                if fp["new"] is None:
                    staged[fp["old"]] = None
                else:
                    staged[fp["new"]] = "\n".join(new_lines) + ("\n" if new_lines and ends_nl else "")

            for rel, content in staged.items():     # write only after every file patched
                full = os.path.join(cwd, rel)
                if content is None:
                    os.remove(full)
                else:
                    os.makedirs(os.path.dirname(full) or cwd, exist_ok=True)
                    with open(full, "w", encoding="utf-8", newline="\n") as f:
                        f.write(content)
            return 0, f"Pure-Python patch applier updated {len(staged)} file(s)."
        except Exception as e:
            return 1, f"Manual patch failed: {e}. Git error: {git_err}"