"""
Member 3: Task Packager.
Standardizes and saves verified tasks into SWE-bench compliant task packages:
- task.toml
- instruction.md
- solution/solution.diff
- tests/test_feature_patch.py
- validation_report.json
"""

import json
import os
import shutil
import tempfile
import time
from typing import Dict, Any
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
        overwrite: bool = False,
    ) -> str:
        """Create the standardized SWE-bench task package directory.

        All artifacts are written to a temporary staging directory first and
        then moved atomically to the final location. If any step fails, the
        staging directory is cleaned up so no partially packaged task remains.

        Args:
            task_id: Unique task identifier (used as directory name).
            repo_name: Name of the target repository.
            category: Task category (e.g. "new_feature").
            instruction_md: Feature specification markdown.
            solution_diff: Unified git diff patch.
            test_code: Python test code string.
            verification: VerificationResult from SandboxVerifier.
            overwrite: If True, replace an existing task directory.
                       If False, raise FileExistsError if it already exists.

        Returns:
            Path to the created task directory.

        Raises:
            ValueError: If any required input is missing or invalid.
            FileExistsError: If the task directory already exists and
                overwrite is False.
            TypeError: If verification is not a VerificationResult instance.
        """
        # ---- Input validation ----
        if not task_id or not isinstance(task_id, str):
            raise ValueError("task_id must be a non-empty string.")
        if not repo_name or not isinstance(repo_name, str):
            raise ValueError("repo_name must be a non-empty string.")
        if not category or not isinstance(category, str):
            raise ValueError("category must be a non-empty string.")
        if not instruction_md or not isinstance(instruction_md, str):
            raise ValueError("instruction_md must be a non-empty string.")
        if not solution_diff or not isinstance(solution_diff, str):
            raise ValueError("solution_diff must be a non-empty string.")
        if not test_code or not isinstance(test_code, str):
            raise ValueError("test_code must be a non-empty string.")
        if not isinstance(verification, VerificationResult):
            raise TypeError(
                "verification must be a VerificationResult instance "
                f"from src.verifier, got {type(verification).__name__}."
            )

        task_dir = os.path.join(self.output_root, task_id)

        # ---- Existing directory handling ----
        if os.path.exists(task_dir) and not overwrite:
            raise FileExistsError(
                f"Task directory already exists: {task_dir}. "
                f"Use overwrite=True to replace it."
            )

        # ---- Stage in a temporary directory to avoid partial packages ----
        staging_dir = tempfile.mkdtemp(prefix=f"{task_id}_stage_")
        try:
            solution_dir = os.path.join(staging_dir, "solution")
            tests_dir = os.path.join(staging_dir, "tests")

            os.makedirs(solution_dir, exist_ok=True)
            os.makedirs(tests_dir, exist_ok=True)

            # 1. Write instruction.md
            with open(os.path.join(staging_dir, "instruction.md"), "w", encoding="utf-8") as f:
                f.write(instruction_md)

            # 2. Write solution.diff
            with open(os.path.join(solution_dir, "solution.diff"), "w", encoding="utf-8") as f:
                f.write(solution_diff)

            # 3. Write tests/test_feature_patch.py
            with open(os.path.join(tests_dir, "test_feature_patch.py"), "w", encoding="utf-8") as f:
                f.write(test_code)

            # 4. Write task.toml
            toml_content = f"""# Task Package Configuration
task_id = "{task_id}"
repo_name = "{repo_name}"
category = "{category}"
created_at = "{time.strftime('%Y-%m-%dT%H:%M:%SZ')}"

[environment]
python_version = ">=3.9"
test_command = "pytest tests/test_feature_patch.py"

[evaluation]
fail_to_pass = ["tests/test_feature_patch.py"]
pass_to_pass = ["tests/"]
"""
            with open(os.path.join(staging_dir, "task.toml"), "w", encoding="utf-8") as f:
                f.write(toml_content)

            # 5. Write validation_report.json
            report = {
                "task_id": task_id,
                "category": category,
                "status": "VERIFIED" if verification.is_valid else "FAILED_VERIFICATION",
                "checks": {
                    "step1_baseline_pass": verification.step1_baseline_pass,
                    "step2_negative_fail": verification.step2_negative_fail,
                    "step3_positive_pass": verification.step3_positive_pass,
                    "patch_applied_cleanly": verification.patch_applied_cleanly,
                },
                "logs": verification.logs,
            }
            with open(os.path.join(staging_dir, "validation_report.json"), "w", encoding="utf-8") as f:
                json.dump(report, f, indent=2)

            # ---- Replace existing directory only after successful staging ----
            if overwrite and os.path.exists(task_dir):
                shutil.rmtree(task_dir)

            # ---- Atomic move to final location ----
            shutil.move(staging_dir, task_dir)

        except Exception:
            # Clean up staging directory on any failure to avoid partial packages.
            shutil.rmtree(staging_dir, ignore_errors=True)
            raise

        return task_dir
