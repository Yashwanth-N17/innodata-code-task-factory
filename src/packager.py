
"""
Feature 6: Standardized Task Packager.

Standardizes and saves verified tasks into SWE-bench compliant task packages:
- task.toml
- instruction.md
- solution/solution.diff
- tests/test_feature_patch.py
- validation_report.json

Input: Task artifacts, repo metadata, and verification audit results.
Output: Standardized directory under tasks/<task_id>/.
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
        """Create a standardized SWE-bench task package directory.

        All artifacts are written to a temporary staging directory first.
        The completed package is moved to its final location only after
        all files have been successfully created.

        Args:
            task_id: Unique task identifier used as directory name.
            repo_name: Name of the target repository.
            category: Task category.
            instruction_md: Feature specification markdown.
            solution_diff: Unified git diff patch.
            test_code: Python test code.
            verification: VerificationResult from SandboxVerifier.
            overwrite: Whether to replace an existing task directory.

        Returns:
            Absolute path to the created task directory.

        Raises:
            ValueError: If required inputs are missing or invalid.
            FileExistsError: If the task directory already exists
                and overwrite is False.
            TypeError: If verification is not a VerificationResult.
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

        # Prevent task_id from escaping the output directory.
        if os.path.basename(task_id) != task_id:
            raise ValueError("task_id must be a valid directory name.")

        task_dir = os.path.join(self.output_root, task_id)

        # ---- Existing directory handling ----

        if os.path.exists(task_dir) and not overwrite:
            raise FileExistsError(
                f"Task directory already exists: {task_dir}. "
                f"Use overwrite=True to replace it."
            )

        # ---- Stage package in temporary directory ----

        staging_dir = tempfile.mkdtemp(
            prefix=f"{task_id}_stage_",
            dir=self.output_root,
        )

        try:
            solution_dir = os.path.join(staging_dir, "solution")
            tests_dir = os.path.join(staging_dir, "tests")

            os.makedirs(solution_dir, exist_ok=True)
            os.makedirs(tests_dir, exist_ok=True)

            # 1. Write instruction.md
            with open(
                os.path.join(staging_dir, "instruction.md"),
                "w",
                encoding="utf-8",
            ) as f:
                f.write(instruction_md)

            # 2. Write solution/solution.diff
            with open(
                os.path.join(solution_dir, "solution.diff"),
                "w",
                encoding="utf-8",
            ) as f:
                f.write(solution_diff)

            # 3. Write tests/test_feature_patch.py
            with open(
                os.path.join(tests_dir, "test_feature_patch.py"),
                "w",
                encoding="utf-8",
            ) as f:
                f.write(test_code)

            # 4. Write task.toml
            created_at = time.strftime("%Y-%m-%dT%H:%M:%SZ")

            toml_content = f"""# Task Package Configuration
task_id = "{task_id}"
repo_name = "{repo_name}"
category = "{category}"
created_at = "{created_at}"

[environment]
python_version = ">=3.9"
test_command = "pytest tests/test_feature_patch.py"

[evaluation]
fail_to_pass = ["tests/test_feature_patch.py"]
pass_to_pass = ["tests/"]
"""

            with open(
                os.path.join(staging_dir, "task.toml"),
                "w",
                encoding="utf-8",
            ) as f:
                f.write(toml_content)

            # 5. Write validation_report.json
            report = {
                "task_id": task_id,
                "repo_name": repo_name,
                "category": category,
                "created_at": created_at,
                "status": (
                    "VERIFIED"
                    if verification.is_valid
                    else "FAILED_VERIFICATION"
                ),
                "checks": {
                    "step1_baseline_pass": verification.step1_baseline_pass,
                    "step2_negative_fail": verification.step2_negative_fail,
                    "step3_positive_pass": verification.step3_positive_pass,
                    "patch_applied_cleanly": verification.patch_applied_cleanly,
                },
                "environment": {
                    "python_version": ">=3.9",
                    "test_command": "pytest tests/test_feature_patch.py",
                },
                "evaluation": {
                    "fail_to_pass": ["tests/test_feature_patch.py"],
                    "pass_to_pass": ["tests/"],
                },
                "logs": verification.logs,
            }

            with open(
                os.path.join(staging_dir, "validation_report.json"),
                "w",
                encoding="utf-8",
            ) as f:
                json.dump(report, f, indent=2)

            # ---- Replace existing package if overwrite is enabled ----

            if overwrite and os.path.exists(task_dir):
                shutil.rmtree(task_dir)

            # ---- Move completed package to final destination ----

            shutil.move(staging_dir, task_dir)

        except Exception:
            # Clean up staging directory if packaging fails.
            shutil.rmtree(staging_dir, ignore_errors=True)
            raise

        return task_dir
