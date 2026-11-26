"""
Feature 8: Frontier AI Benchmark Evaluation Harness.
Hands generated `instruction.md` to a target external AI model (e.g. Claude 3.5 Sonnet, GPT-4o, Gemini),
captures its output patch, and evaluates whether the model passes the hidden test suite in the sandbox.
"""

import glob
import os
from typing import Any, Dict, Optional

from .config import call_llm
from .verifier import SandboxVerifier


class BenchmarkEvaluator:
    def __init__(
        self,
        task_dir: str,
        target_repo_path: str,
        prompt_template_path: Optional[str] = None,
        language: Optional[str] = None,
    ):
        self.task_dir = os.path.abspath(task_dir)
        self.target_repo_path = os.path.abspath(target_repo_path)
        self.verifier = SandboxVerifier(target_repo_path, language=language)
        if not prompt_template_path:
            prompt_template_path = os.path.join(
                os.path.dirname(__file__), "..", "prompts", "evaluator_prompt.txt"
            )
        with open(prompt_template_path, "r", encoding="utf-8") as f:
            self.eval_prompt_template = f.read()

    def _find_test_file(self) -> str:
        candidates = sorted(glob.glob(os.path.join(self.task_dir, "tests", "test_feature_patch.*")))
        if candidates:
            return candidates[0]
        return os.path.join(self.task_dir, "tests", "test_feature_patch.py")

    def evaluate_model(self, model_name: str = "claude-3-5-sonnet") -> Dict[str, Any]:
        """Evaluate an AI model against this packaged task."""
        instruction_path = os.path.join(self.task_dir, "instruction.md")
        test_file_path = self._find_test_file()

        with open(instruction_path, "r", encoding="utf-8") as f:
            instruction_md = f.read()

        with open(test_file_path, "r", encoding="utf-8") as f:
            test_code = f.read()

        prompt = f"""{self.eval_prompt_template}

Task Specification (instruction.md):
{instruction_md}
"""
        agent_diff = call_llm(prompt=prompt, language=self.verifier.adapter.language_id)
        result = self.verifier.verify(solution_diff=agent_diff, test_code=test_code)

        return {
            "model_tested": model_name,
            "task_id": os.path.basename(self.task_dir),
            "language": self.verifier.adapter.language_id,
            "passed": result.is_valid,
            "baseline_passed": result.step1_baseline_pass,
            "patch_clean": result.patch_applied_cleanly,
            "tests_passed": result.step3_positive_pass,
        }
