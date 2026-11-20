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
    ) -> str:
        """Create the standardized SWE-bench task package directory."""
        task_dir = os.path.join(self.output_root, task_id)
        solution_dir = os.path.join(task_dir, "solution")
        tests_dir = os.path.join(task_dir, "tests")

        os.makedirs(solution_dir, exist_ok=True)
        os.makedirs(tests_dir, exist_ok=True)

        # 1. Write instruction.md
        with open(os.path.join(task_dir, "instruction.md"), "w", encoding="utf-8") as f:
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
        with open(os.path.join(task_dir, "task.toml"), "w", encoding="utf-8") as f:
            f.write(toml_content)

        # 5. Write validation_report.json (Proof for judges)
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
        with open(os.path.join(task_dir, "validation_report.json"), "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2)

        return task_dir
