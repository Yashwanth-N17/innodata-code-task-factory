"""
Member 4: Frontier AI Benchmark Evaluator.
Hands generated `instruction.md` to a target AI model, captures its output patch,
and evaluates whether the model passes the hidden test suite.
"""

import os
from typing import Dict, Any
from .verifier import SandboxVerifier
from .config import call_llm


class BenchmarkEvaluator:
    def __init__(self, task_dir: str, target_repo_path: str, prompt_template_path: str = None):
        self.task_dir = os.path.abspath(task_dir)
        self.target_repo_path = os.path.abspath(target_repo_path)
        self.verifier = SandboxVerifier(target_repo_path)
        if not prompt_template_path:
            prompt_template_path = os.path.join(
                os.path.dirname(__file__), "..", "prompts", "evaluator_prompt.txt"
            )
        with open(prompt_template_path, "r", encoding="utf-8") as f:
            self.eval_prompt_template = f.read()

    def evaluate_model(self, model_name: str = "claude-3-5-sonnet") -> Dict[str, Any]:
        """Evaluate an AI model against this task package."""
        instruction_path = os.path.join(self.task_dir, "instruction.md")
        test_file_path = os.path.join(self.task_dir, "tests", "test_feature_patch.py")

        with open(instruction_path, "r", encoding="utf-8") as f:
            instruction_md = f.read()

        with open(test_file_path, "r", encoding="utf-8") as f:
            test_code = f.read()

        # Step 1: Prompt the target model with instruction.md
        prompt = f"""{self.eval_prompt_template}

Task Specification (instruction.md):
{instruction_md}
"""
        agent_diff = call_llm(prompt=prompt)

        # Step 2: Grade the model using our 3-step verifier
        result = self.verifier.verify(solution_diff=agent_diff, test_code=test_code)

        return {
            "model_tested": model_name,
            "task_id": os.path.basename(self.task_dir),
            "passed": result.is_valid,
            "baseline_passed": result.step1_baseline_pass,
            "patch_clean": result.patch_applied_cleanly,
            "tests_passed": result.step3_positive_pass,
        }
