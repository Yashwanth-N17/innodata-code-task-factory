"""
Feature 8: Frontier AI Benchmark Evaluation Harness.
Hands generated `instruction.md` to a target external AI model (e.g. Claude 3.5 Sonnet, GPT-4o),
captures its output patch, and evaluates whether the model passes the hidden test suite in the sandbox.

Input: Packaged task directory + target model identifier.
Output: Evaluation audit dictionary containing solve rate and test logs.
"""

# import os
# from typing import Dict, Any
# from .verifier import SandboxVerifier
# from .config import call_llm


# class BenchmarkEvaluator:
#     def __init__(self, task_dir: str, target_repo_path: str, prompt_template_path: str = None):
#         self.task_dir = os.path.abspath(task_dir)
#         self.target_repo_path = os.path.abspath(target_repo_path)
#         self.verifier = SandboxVerifier(target_repo_path)
#         if not prompt_template_path:
#             prompt_template_path = os.path.join(
#                 os.path.dirname(__file__), "..", "prompts", "evaluator_prompt.txt"
#             )
#         with open(prompt_template_path, "r", encoding="utf-8") as f:
#             self.eval_prompt_template = f.read()

#     def evaluate_model(self, model_name: str = "claude-3-5-sonnet") -> Dict[str, Any]:
#         """Evaluate an AI model against this task package."""
#         instruction_path = os.path.join(self.task_dir, "instruction.md")
#         test_file_path = os.path.join(self.task_dir, "tests", "test_feature_patch.py")

#         with open(instruction_path, "r", encoding="utf-8") as f:
#             instruction_md = f.read()

#         with open(test_file_path, "r", encoding="utf-8") as f:
#             test_code = f.read()

#         # Step 1: Prompt the target model with instruction.md
#         prompt = f"""{self.eval_prompt_template}

# Task Specification (instruction.md):
# {instruction_md}
# """
#         agent_diff = call_llm(prompt=prompt)

#         # Step 2: Grade the model using our 3-step verifier
#         result = self.verifier.verify(solution_diff=agent_diff, test_code=test_code)

#         return {
#             "model_tested": model_name,
#             "task_id": os.path.basename(self.task_dir),
#             "passed": result.is_valid,
#             "baseline_passed": result.step1_baseline_pass,
#             "patch_clean": result.patch_applied_cleanly,
#             "tests_passed": result.step3_positive_pass,
#         }


"""
Feature 8: Frontier AI Benchmark Evaluation Harness.
Prompts external frontier models with generated `instruction.md` files and codebase context,
extracts their proposed unified Git diff, grades the patch via SandboxVerifier,
and aggregates Pass@1 benchmark reports across task suites.
"""

import argparse
import inspect
import json
import logging
import os
import re
import time
from typing import Any, Dict, List, Optional, Tuple

try:
    import tomllib  # Built into Python 3.11+
except ModuleNotFoundError:
    try:
        import tomli as tomllib  # Backport for Python <= 3.10
    except ModuleNotFoundError:
        tomllib = None

from .config import call_llm
from .verifier import SandboxVerifier

try:
    from .analyzer import CodebaseAnalyzer
except ImportError:
    CodebaseAnalyzer = None

logger = logging.getLogger(__name__)

__all__ = ["BenchmarkEvaluator"]

# Default retry parameters for LLM API calls
_DEFAULT_MAX_RETRIES = 3
_DEFAULT_RETRY_BACKOFF = 2.0  # seconds; doubles each attempt


class BenchmarkEvaluator:
    def __init__(
        self,
        task_dir: Optional[str] = None,
        target_repo_path: str = ".",
        prompt_template_path: Optional[str] = None,
    ):
        self.task_dir = os.path.abspath(task_dir) if task_dir else None
        self.target_repo_path = os.path.abspath(target_repo_path)
        self.verifier = SandboxVerifier(self.target_repo_path)

        if not prompt_template_path:
            prompt_template_path = os.path.join(
                os.path.dirname(__file__), "..", "prompts", "evaluator_prompt.txt"
            )

        if os.path.exists(prompt_template_path):
            with open(prompt_template_path, "r", encoding="utf-8") as f:
                self.eval_prompt_template = f.read().strip()
        else:
            self.eval_prompt_template = (
                "You are an expert Python engineer. Return ONLY a valid unified Git diff "
                "solving the given issue without modifying test files."
            )

        self._cached_repo_context: Optional[str] = None

    def _get_repo_context(self) -> str:
        """Lazily scan and cache the target repository context using Feature 1."""
        if self._cached_repo_context is not None:
            return self._cached_repo_context

        if CodebaseAnalyzer is not None:
            try:
                analyzer = CodebaseAnalyzer(self.target_repo_path)
                self._cached_repo_context = analyzer.get_context_prompt()
                return self._cached_repo_context
            except Exception as exc:
                self._cached_repo_context = f"(Repository scan unavailable: {exc})"
                return self._cached_repo_context

        self._cached_repo_context = "(CodebaseAnalyzer not available)"
        return self._cached_repo_context

    def _load_task_metadata(self, task_dir: str) -> Dict[str, Any]:
        """Parse optional task.toml metadata (e.g., archetype/category) safely."""
        toml_path = os.path.join(task_dir, "task.toml")
        if not os.path.exists(toml_path):
            return {}

        if tomllib is None:
            logger.warning(
                "Neither tomllib (Python 3.11+) nor tomli is available; "
                "task.toml metadata will be skipped for %s",
                task_dir,
            )
            return {}

        try:
            with open(toml_path, "rb") as f:
                return tomllib.load(f)
        except Exception:
            return {}

    def _extract_archetype(self, metadata: Dict[str, Any]) -> str:
        """Locate the task archetype or category from parsed task.toml data."""
        for key in ("archetype", "category"):
            if key in metadata and isinstance(metadata[key], str):
                return metadata[key]
        for section in ("task", "metadata"):
            nested = metadata.get(section)
            if isinstance(nested, dict):
                for key in ("archetype", "category"):
                    if key in nested and isinstance(nested[key], str):
                        return nested[key]
        return "unknown"

    def _build_prompts(self, repo_context: str, instruction_md: str) -> Tuple[str, str]:
        """Build `(system_prompt, user_prompt)` cleanly without leaving unformatted placeholders."""
        template = self.eval_prompt_template
        if "{codebase_context}" in template or "{instruction_md}" in template:
            rendered = (
                template
                .replace("{codebase_context}", repo_context)
                .replace("{instruction_md}", instruction_md)
            )
            system_prompt = (
                "# FEATURE: Feature 8 - Frontier AI Benchmark Evaluator\n"
                "# LOADED BY: src/benchmark_evaluator.py\n"
                "You are an expert software engineer. Follow the specification and output ONLY a valid unified Git diff."
            )
            return system_prompt, rendered

        user_prompt = (
            f"Target Codebase Context:\n{repo_context}\n\n"
            f"Task Specification (instruction.md):\n{instruction_md}\n"
        )
        return template, user_prompt

    def _invoke_model(
        self,
        user_prompt: str,
        model_name: str,
        system_prompt: Optional[str] = None,
    ) -> str:
        """
        Adaptively call src.config.call_llm() with exponential-backoff retry.
        Inspects its signature so model_name and system_prompt are forwarded
        only when supported by the current config.py implementation.

        for now it is just calling placeholder texts and running mock tests,
        but it is supposed to call actual LLM APIs and return response text
        """
        sig = inspect.signature(call_llm)
        params = sig.parameters
        accepts_kwargs = any(
            p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values()
        )

        active_system_prompt = (
            self.eval_prompt_template if system_prompt is None else system_prompt
        )
        call_kwargs: Dict[str, Any] = {}

        if "system_prompt" in params or accepts_kwargs:
            call_kwargs["system_prompt"] = active_system_prompt
            call_kwargs["prompt"] = user_prompt
        else:
            call_kwargs["prompt"] = (
                f"{active_system_prompt}\n\n{user_prompt}" if active_system_prompt else user_prompt
            )

        if "model" in params or accepts_kwargs:
            call_kwargs["model"] = model_name
        elif "model_name" in params:
            call_kwargs["model_name"] = model_name

        last_exc: Optional[Exception] = None
        for attempt in range(_DEFAULT_MAX_RETRIES):
            try:
                response = call_llm(**call_kwargs)
                return response if isinstance(response, str) else str(response)
            except Exception as exc:
                last_exc = exc
                if attempt < _DEFAULT_MAX_RETRIES - 1:
                    wait = _DEFAULT_RETRY_BACKOFF * (2 ** attempt)
                    logger.warning(
                        "LLM call attempt %d/%d failed (%s), retrying in %.1fs…",
                        attempt + 1,
                        _DEFAULT_MAX_RETRIES,
                        exc,
                        wait,
                    )
                    time.sleep(wait)
        raise last_exc  # type: ignore[misc]

    @staticmethod
    def _extract_diff(raw_response: str) -> str:
        """
        Strip Markdown code fences and conversational filler from raw LLM output
        to isolate a clean unified Git diff patch.  When the response contains
        multiple fenced diff blocks (e.g. one per file), they are concatenated.
        """
        if not raw_response or not raw_response.strip():
            return ""

        text = raw_response.replace("\r\n", "\n").strip()

        # 1. Collect all fenced code blocks that look like diffs
        fenced_blocks = re.findall(
            r"```(?:diff|patch)?\s*\n(.*?)```", text, flags=re.DOTALL | re.IGNORECASE
        )
        diff_blocks = [
            block.strip()
            for block in fenced_blocks
            if "diff --git " in block or ("--- " in block and "+++ " in block)
        ]

        candidate = "\n".join(diff_blocks) if diff_blocks else text

        # 2. Trim any prose appearing before the first unified diff header
        start_match = re.search(
            r"^(?:diff --git |--- a/|--- )", candidate, flags=re.MULTILINE
        )
        if not start_match:
            return ""

        cleaned = candidate[start_match.start():].strip()

        # 3. Validate essential unified diff markers
        has_headers = "--- " in cleaned and "+++ " in cleaned
        has_hunk = "@@" in cleaned
        if not (has_headers and has_hunk):
            return ""

        # git apply expects a trailing newline
        return cleaned + "\n"

    @staticmethod
    def _extract_verifier_logs(result: Any) -> Dict[str, Any]:
        """Safely gather diagnostic logs exposed by VerificationResult."""
        log_fields = (
            "logs",
            "stdout",
            "stderr",
            "error",
            "error_message",
            "step1_output",
            "step2_output",
            "step3_output",
        )
        captured: Dict[str, Any] = {}
        for field in log_fields:
            value = getattr(result, field, None)
            if value:
                captured[field] = value
        return captured

    def evaluate_model(
        self,
        model_name: str = "claude-3-5-sonnet",
        task_dir: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Evaluate a single AI model against one packaged task directory. 
        Evaluating it using Pass@1, the number of passed tests and the failure reason if any will be shown"""
        active_task_dir = os.path.abspath(task_dir) if task_dir else self.task_dir
        if not active_task_dir:
            raise ValueError("A task_dir must be provided either in __init__ or evaluate_model().")

        task_id = os.path.basename(active_task_dir)
        instruction_path = os.path.join(active_task_dir, "instruction.md")
        test_file_path = os.path.join(active_task_dir, "tests", "test_feature_patch.py")

        metadata = self._load_task_metadata(active_task_dir)
        archetype = self._extract_archetype(metadata)

        # Base audit structure (preserves original starter keys for compatibility)
        audit: Dict[str, Any] = {
            "model_tested":model_name, #the name of the model to be tested, gpt sol, fable, or even hydrogen if APi key is there
            "task_id": task_id,
            "archetype": archetype,
            "passed": False,
            "baseline_passed": False,
            "patch_clean": False,
            "tests_passed": False,
            "failure_reason": "none",
            "latency_seconds": 0.0,
            "logs": {},
        }

        if not os.path.exists(instruction_path) or not os.path.exists(test_file_path):
            audit["failure_reason"] = "missing_task_files"
            audit["logs"] = {
                "error": f"Expected instruction.md and tests/test_feature_patch.py inside {active_task_dir}"
            }
            return audit

        with open(instruction_path, "r", encoding="utf-8") as f:
            instruction_md = f.read()

        with open(test_file_path, "r", encoding="utf-8") as f:
            test_code = f.read()

        # Step 1: Assemble context + specification (never expose solution.diff or tests)
        repo_context = self._get_repo_context()
        system_prompt, user_prompt = self._build_prompts(repo_context, instruction_md)

        start_time = time.perf_counter()
        try:
            raw_response = self._invoke_model(
                user_prompt=user_prompt,
                model_name=model_name,
                system_prompt=system_prompt,
            )
        except Exception as exc:
            audit["latency_seconds"] = round(time.perf_counter() - start_time, 3)
            audit["failure_reason"] = "api_error"
            audit["logs"] = {"error": str(exc)}
            return audit

        audit["latency_seconds"] = round(time.perf_counter() - start_time, 3)

        # Step 2: Extract and validate the unified Git diff
        clean_diff = self._extract_diff(raw_response)
        if not clean_diff:
            audit["failure_reason"] = "malformed_diff"
            audit["logs"] = {"raw_response_preview": raw_response[:500]}
            return audit

        # Step 3: Grade the proposed patch inside Feature 5's SandboxVerifier
        try:
            result = self.verifier.verify(solution_diff=clean_diff, test_code=test_code)
        except Exception as exc:
            audit["failure_reason"] = "verifier_error"
            audit["logs"] = {"error": str(exc)}
            return audit

        audit["passed"] = bool(getattr(result, "is_valid", False))
        audit["baseline_passed"] = bool(getattr(result, "step1_baseline_pass", False))
        audit["patch_clean"] = bool(getattr(result, "patch_applied_cleanly", False))
        audit["tests_passed"] = bool(getattr(result, "step3_positive_pass", False))
        audit["logs"] = self._extract_verifier_logs(result)

        if not audit["passed"]:
            if not audit["baseline_passed"]:
                audit["failure_reason"] = "baseline_failed"
            elif not audit["patch_clean"]:
                audit["failure_reason"] = "patch_apply_failed"
            elif not audit["tests_passed"]:
                audit["failure_reason"] = "test_failed"
            else:
                audit["failure_reason"] = "verification_failed"

        return audit

    def evaluate_suite(
        self,
        tasks_root_dir: str,
        models: Optional[List[str]] = None,
        output_report_path: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Evaluate one or more frontier models across every packaged task in tasks_root_dir,
        computing Pass@1 solve rates, failure breakdowns, and per-archetype scores.
        """
        models = models or ["claude-3-5-sonnet"]
        tasks_root_dir = os.path.abspath(tasks_root_dir)

        task_dirs: List[str] = []
        if os.path.isdir(tasks_root_dir):
            for entry in sorted(os.listdir(tasks_root_dir)):
                candidate = os.path.join(tasks_root_dir, entry)
                if os.path.isdir(candidate) and os.path.exists(
                    os.path.join(candidate, "instruction.md")
                ):
                    task_dirs.append(candidate)

        report: Dict[str, Any] = {
            "target_repo": self.target_repo_path,
            "tasks_root": tasks_root_dir,
            "total_tasks": len(task_dirs),
            "models": {},
        }

        for model_name in models:
            task_results: List[Dict[str, Any]] = []
            failure_breakdown: Dict[str, int] = {}
            archetype_stats: Dict[str, Dict[str, int]] = {}
            resolved = 0

            for task_path in task_dirs:
                outcome = self.evaluate_model(model_name=model_name, task_dir=task_path)
                task_results.append(outcome)

                arch = outcome["archetype"]
                if arch not in archetype_stats:
                    archetype_stats[arch] = {"passed": 0, "total": 0}
                archetype_stats[arch]["total"] += 1

                if outcome["passed"]:
                    resolved += 1
                    archetype_stats[arch]["passed"] += 1
                else:
                    reason = outcome["failure_reason"]
                    failure_breakdown[reason] = failure_breakdown.get(reason, 0) + 1

            total = len(task_dirs)
            solve_rate = round((resolved / total) * 100.0, 2) if total > 0 else 0.0

            report["models"][model_name] = {
                "resolved": resolved,
                "total": total,
                "pass_at_1_percent": solve_rate,
                "failure_breakdown": failure_breakdown,
                "archetype_breakdown": archetype_stats,
                "task_results": task_results,
            }

        if output_report_path:
            output_abs = os.path.abspath(output_report_path)
            parent = os.path.dirname(output_abs)
            if parent:
                os.makedirs(parent, exist_ok=True)
            with open(output_abs, "w", encoding="utf-8") as f:
                json.dump(report, f, indent=2)

        return report


def _print_summary(report: Dict[str, Any]) -> None:
    """Print a clean terminal scorecard for live hackathon demos."""
    print(f"\nBenchmark Report ({report['total_tasks']} tasks evaluated)")
    print("=" * 60)
    for model, stats in report["models"].items():
        print(
            f"Model: {model} | Resolved: {stats['resolved']}/{stats['total']} "
            f"| Pass@1: {stats['pass_at_1_percent']}%"
        )
        if stats["failure_breakdown"]:
            print(f"  Failures: {stats['failure_breakdown']}")
    print("=" * 60)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run Feature 8 Frontier AI Benchmark Harness")
    parser.add_argument("--repo", required=True, help="Path to target software repository")
    parser.add_argument("--tasks", default="tasks", help="Root directory containing packaged tasks")
    parser.add_argument(
        "--models",
        nargs="+",
        default=["claude-3-5-sonnet"],
        help="Space-separated list of model identifiers to evaluate",
    )
    parser.add_argument(
        "--output",
        default="benchmark_report.json",
        help="Output path for the JSON benchmark report",
    )
    args = parser.parse_args()

    evaluator = BenchmarkEvaluator(target_repo_path=args.repo)
    summary = evaluator.evaluate_suite(
        tasks_root_dir=args.tasks,
        models=args.models,
        output_report_path=args.output,
    )
    _print_summary(summary)