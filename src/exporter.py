"""
SWE-bench Dataset Exporter, Hugging Face Publisher & Mutation Test Rigor Auditor.
Converts packaged task directories (`tasks/<task_id>/`) into official SWE-bench JSONL records
and validates test rigor against behavioral mutations of the reference solution diff.
"""

import glob
import json
import os
import re
from typing import Any, Dict, List, Optional

from .verifier import SandboxVerifier


def _read_text(path: str) -> str:
    if os.path.isfile(path):
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read()
    return ""


def _find_test_file(task_dir: str) -> str:
    extra = [
        os.path.join(task_dir, "feature_patch_test.go"),
        os.path.join(task_dir, "src", "test", "java", "FeaturePatchTest.java"),
        os.path.join(task_dir, "FeaturePatchTest.java"),
        os.path.join(task_dir, "test_patch.py"),
    ]
    for p in extra:
        if os.path.isfile(p):
            return p
    candidates = sorted(glob.glob(os.path.join(task_dir, "tests", "test_feature_patch.*")))
    if candidates:
        return candidates[0]
    alt = sorted(glob.glob(os.path.join(task_dir, "tests", "*")))
    for path in alt:
        if os.path.isfile(path):
            return path
    return ""


def _is_comment_or_docstring(line: str) -> bool:
    stripped = line.lstrip()
    if stripped.startswith("+"):
        stripped = stripped[1:].lstrip()
    if stripped.startswith("#") or stripped.startswith("//") or stripped.startswith("/*") or stripped.startswith("*"):
        return True
    if '"""' in stripped or "'''" in stripped:
        return True
    return False


def _mutate_diff_lines(solution_diff: str) -> Optional[str]:
    """Create a minimal behavioral mutant of added lines (`+...`) inside solution_diff."""
    lines = solution_diff.splitlines(keepends=True)
    mutated = list(lines)

    replacements = [
        (r"===", "!=="),
        (r"!==", "==="),
        (r"==", "!="),
        (r"!=", "=="),
        (r">=", "<"),
        (r"<=", ">"),
        (r"\bTrue\b", "False"),
        (r"\bFalse\b", "True"),
        (r"\btrue\b", "false"),
        (r"\bfalse\b", "true"),
        (r"\bNone\b", "0"),
        (r"\bnull\b", "None"),
        (r"\bnil\b", "None"),
        (r"\breturn\s+True\b", "return False"),
        (r"\breturn\s+False\b", "return True"),
        (r" + ", " - "),
        (r" - ", " + "),
    ]
    for idx, line in enumerate(mutated):
        if not line.startswith("+") or line.startswith("+++"):
            continue
        if _is_comment_or_docstring(line):
            continue
        for pat, rep in replacements:
            new_line, count = re.subn(pat, rep, line, count=1)
            if count > 0 and new_line != line:
                mutated[idx] = new_line
                return "".join(mutated)
    return None


def check_test_mutation_rigor(
    verifier: SandboxVerifier,
    solution_diff: str,
    test_code: str,
) -> Dict[str, Any]:
    """
    Verify test suite cannot be fooled by a behavioral mutation of solution_diff.
    Returns {'checked': bool, 'mutant_killed': Optional[bool], 'note': str, 'mutant_diff': Optional[str]}.
    """
    mutant_diff = _mutate_diff_lines(solution_diff)
    if not mutant_diff:
        return {
            "checked": False,
            "mutant_killed": None,
            "mutant_diff": None,
            "note": "No eligible boolean/comparison literal in added lines for lightweight mutation.",
        }

    # Step-3 only: apply mutant_diff to a fresh copy and run tests directly
    try:
        # Use verifier.verify but we only care about step3; however to save time,
        # we still call verify. Mutant killed if step3 fails.
        res = verifier.verify(solution_diff=mutant_diff, test_code=test_code)
    except Exception as e:
        return {"checked": True, "mutant_killed": False, "mutant_diff": mutant_diff, "note": f"Patch application failed: {e}"}

    if not getattr(res, "patch_applied_cleanly", True):
        return {"checked": True, "mutant_killed": False, "mutant_diff": mutant_diff, "note": "Patch application failed"}

    killed = not getattr(res, "step3_positive_pass", True)
    note = (
        "Test suite successfully rejected mutated solution (high rigor)."
        if killed
        else "WARNING: Test suite still passed on a mutated solution; strengthen assertions."
    )
    return {"checked": True, "mutant_killed": killed, "mutant_diff": mutant_diff, "note": note}


def export_swebench_jsonl(
    tasks_dir: str = "tasks",
    output_path: str = "tasks/swebench_dataset.jsonl",
    verified_only: bool = True,
) -> Dict[str, Any]:
    """Export packaged tasks into standard SWE-bench JSONL schema."""
    abs_tasks = os.path.abspath(tasks_dir)
    abs_out = os.path.abspath(output_path)
    os.makedirs(os.path.dirname(abs_out) or ".", exist_ok=True)

    records: List[Dict[str, Any]] = []
    scanned = 0

    if os.path.isdir(abs_tasks):
        for entry in sorted(os.listdir(abs_tasks)):
            task_dir = os.path.join(abs_tasks, entry)
            if not os.path.isdir(task_dir):
                continue
            inst_path = os.path.join(task_dir, "instruction.md")
            if not os.path.isfile(inst_path):
                continue
            scanned += 1

            report_path = os.path.join(task_dir, "validation_report.json")
            report: Dict[str, Any] = {}
            if os.path.isfile(report_path):
                try:
                    with open(report_path, "r", encoding="utf-8") as f:
                        report = json.load(f)
                except Exception:
                    pass

            status = report.get("status", "")
            if verified_only and status != "VERIFIED":
                continue

            diff_text = _read_text(os.path.join(task_dir, "solution", "solution.diff"))
            if not diff_text:
                diff_text = _read_text(os.path.join(task_dir, "solution.diff"))

            test_path = _find_test_file(task_dir)
            test_text = _read_text(test_path)
            rel = os.path.relpath(test_path, task_dir) if test_path else "tests/test_feature_patch.py"
            if test_text:
                lines = test_text.splitlines()
                diff_lines = [f"--- /dev/null", f"+++ b/{rel}", f"@@ -0,0 +1,{len(lines)} @@"]
                diff_lines += [f"+{l}" for l in lines]
                test_text = "\n".join(diff_lines) + "\n"
            eval_meta = report.get("evaluation") or {}

            records.append({
                "instance_id": report.get("task_id") or entry,
                "repo": report.get("repo_url") or report.get("repo_name") or "",
                "repo_name": report.get("repo_name") or "",
                "base_commit": report.get("base_commit") or "",
                "language": report.get("language") or "python",
                "category": report.get("category") or "",
                "patch": diff_text,
                "test_patch": test_text,
                "problem_statement": _read_text(inst_path),
                "FAIL_TO_PASS": eval_meta.get("fail_to_pass") or [],
                "PASS_TO_PASS": eval_meta.get("pass_to_pass") or [],
                "created_at": report.get("created_at") or "",
                "version": "1.0",
            })

    with open(abs_out, "w", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec) + "\n")

    return {
        "total_scanned": scanned,
        "exported_count": len(records),
        "output_path": abs_out,
    }


def push_to_huggingface(jsonl_path: str, repo_id: str, private: bool = False, token: Optional[str] = None) -> str:
    """Publish an exported SWE-bench JSONL file to Hugging Face Hub."""
    if not repo_id or not repo_id.strip():
        raise ValueError("Hugging Face dataset repo_id cannot be empty.")
    try:
        from datasets import Dataset  # type: ignore
    except ImportError:
        raise ImportError("Please install `datasets` and `huggingface_hub` to push to Hugging Face Hub.")

    ds = Dataset.from_json(os.path.abspath(jsonl_path))
    ds.push_to_hub(repo_id.strip(), private=private, token=token)
    return f"https://huggingface.co/datasets/{repo_id.strip()}"