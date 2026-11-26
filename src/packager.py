"""
Feature 6: Standardized Polyglot Task Packager.
Standardizes and saves verified tasks into SWE-bench compliant task packages:
- task.toml (language-calibrated runtime & test command)
- instruction.md
- solution/solution.diff
- tests/test_feature_patch.<ext>
- validation_report.json & metadata.json
"""

import json
import os
import time
from typing import Any, Dict, Optional

from .languages import BaseLanguageAdapter, PythonAdapter
from .verifier import VerificationResult


class TaskPackager:
    def __init__(self, output_root: str):
        self.output_root = os.path.abspath(output_root)
        os.makedirs(self.output_root, exist_ok=True)

    def package(
        self,
        task_id: str,
        repo_name: str,
        category: str,
        instruction_md: str,
        solution_diff: str,
        test_code: str,
        verification: VerificationResult,
        language_adapter: Optional[BaseLanguageAdapter] = None,
        manifest: Optional[Dict[str, Any]] = None,
        repo_meta: Optional[Dict[str, str]] = None,
        rigor_check: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Create the standardized polyglot SWE-bench task package directory."""
        adapter = language_adapter or PythonAdapter()
        manifest = manifest or {}
        repo_meta = repo_meta or {}

        safe_id = os.path.basename(str(task_id).replace("\\", "/").rstrip("/"))
        if not safe_id or safe_id in (".", ".."):
            raise ValueError(f"Invalid task_id: {task_id!r}")
        task_dir = os.path.join(self.output_root, safe_id)
        if os.path.commonpath([os.path.realpath(task_dir), os.path.realpath(self.output_root)]) != os.path.realpath(self.output_root):
            raise ValueError(f"task_id escapes output_root: {task_id!r}")
        solution_dir = os.path.join(task_dir, "solution")
        tests_dir = os.path.join(task_dir, "tests")

        os.makedirs(solution_dir, exist_ok=True)
        os.makedirs(tests_dir, exist_ok=True)

        # 1. Write instruction.md
        with open(os.path.join(task_dir, "instruction.md"), "w", encoding="utf-8") as f:
            f.write(instruction_md)

        # 2. Write solution.diff (in solution/ and at task root)
        with open(os.path.join(solution_dir, "solution.diff"), "w", encoding="utf-8") as f:
            f.write(solution_diff)
        with open(os.path.join(task_dir, "solution.diff"), "w", encoding="utf-8") as f:
            f.write(solution_diff)

        # 3. Write language-native test file and root test_patch.<ext>
        test_rel_path = os.path.join(task_dir, adapter.test_file_relpath)
        os.makedirs(os.path.dirname(test_rel_path), exist_ok=True)
        with open(test_rel_path, "w", encoding="utf-8") as f:
            f.write(test_code)
        with open(os.path.join(task_dir, adapter.root_test_filename), "w", encoding="utf-8") as f:
            f.write(test_code)

        # 4. Write polyglot task.toml
        created_at = time.strftime("%Y-%m-%dT%H:%M:%SZ")
        env_meta = adapter.get_environment_metadata(task_dir, manifest)
        toml_content = adapter.format_task_toml(
            task_id=task_id,
            repo_name=repo_name,
            category=category,
            created_at=created_at,
            env_meta=env_meta,
            repo_meta=repo_meta,
        )
        with open(os.path.join(task_dir, "task.toml"), "w", encoding="utf-8") as f:
            f.write(toml_content)

        # 5. Write validation_report.json and metadata.json
        report = {
            "task_id": task_id,
            "repo_name": repo_name,
            "language": adapter.language_id,
            "repo_url": repo_meta.get("repo_url", ""),
            "base_commit": repo_meta.get("commit_hash", ""),
            "category": category,
            "created_at": created_at,
            "status": "VERIFIED" if verification.is_valid else "FAILED_VERIFICATION",
            "checks": {
                "step1_baseline_pass": verification.step1_baseline_pass,
                "step2_negative_fail": verification.step2_negative_fail,
                "step3_positive_pass": verification.step3_positive_pass,
                "patch_applied_cleanly": verification.patch_applied_cleanly,
            },
            "environment": env_meta,
            "evaluation": {
                "fail_to_pass": list(verification.fail_to_pass),
                "pass_to_pass": list(verification.pass_to_pass),
            },
            "logs": verification.logs,
            "rigor_check": rigor_check,
        }
        with open(os.path.join(task_dir, "validation_report.json"), "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2)
        with open(os.path.join(task_dir, "metadata.json"), "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2)

        return task_dir
