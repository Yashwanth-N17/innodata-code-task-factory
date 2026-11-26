"""
Feature 8: Frontier AI Benchmark Evaluation & Leaderboard Engine.
Evaluates external AI models (Claude, GPT-4o/o3-mini, Gemini, DeepSeek, or mock)
against generated task packages with full codebase context, aggregates Pass@1 solve
rates by language and archetype, and generates JSON & Markdown leaderboards.
"""

import glob
import json
import os
import re
import time
from typing import Any, Dict, List, Optional

from .analyzer import CodebaseAnalyzer
from .config import Config, call_llm
from .verifier import SandboxVerifier


def infer_provider(model_name: str, explicit_provider: Optional[str] = None) -> Optional[str]:
    """Infer LLM provider from model_name while honoring mock mode and explicit overrides."""
    if explicit_provider and explicit_provider.strip():
        return explicit_provider.strip().lower()
    # Only fall back to mock if explicitly in mock mode
    if explicit_provider == "mock":
        return "mock"
    # Provider-prefixed names (openai/gpt-4o, anthropic/claude-..., openrouter/...)
    m = (model_name or "").strip().lower()
    if "/" in m:
        prefix, rest = m.split("/", 1)
        if prefix in ("openai", "anthropic", "gemini", "deepseek", "openrouter", "groq", "ollama", "localai", "vllm"):
            if prefix == "openrouter":
                # infer from rest
                m = rest
            else:
                # map known prefixes
                if prefix == "openai":
                    return "openai"
                if prefix == "anthropic":
                    return "anthropic"
                if prefix == "gemini":
                    return "gemini"
                if prefix == "deepseek":
                    return "deepseek"
                if prefix in ("ollama", "localai", "vllm"):
                    return prefix
                if prefix == "groq":
                    return "openai"
    base = m.split("/")[-1]
    if base.startswith("claude"):
        return "anthropic"
    if base.startswith("gemini"):
        return "gemini"
    if base.startswith(("gpt-", "o1", "o3", "o4")) or base.startswith(("o1", "o3", "o4")):
        return "openai"
    if base.startswith("deepseek"):
        return "deepseek"
    # Mock fallback only if Config explicitly mock
    if (Config.LLM_PROVIDER or "").strip().lower() == "mock":
        return "mock"
    return None


def _clean_diff(raw_output: str) -> str:
    cleaned = (raw_output or "").strip()
    cleaned = re.sub(r"^```[a-zA-Z0-9_+-]*\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    return (cleaned.strip() + "\n") if cleaned.strip() else ""


class BenchmarkEvaluator:
    def __init__(
        self,
        task_dir: str,
        target_repo_path: str,
        prompt_template_path: Optional[str] = None,
        language: Optional[str] = None,
        timeout: int = 90,
    ):
        self.task_dir = os.path.abspath(task_dir)
        self.target_repo_path = os.path.abspath(target_repo_path)
        self.verifier = SandboxVerifier(target_repo_path, timeout=timeout, language=language)
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

    def _load_task_metadata(self) -> Dict[str, Any]:
        meta_path = os.path.join(self.task_dir, "validation_report.json")
        if os.path.isfile(meta_path):
            try:
                with open(meta_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {}

    def evaluate_model(
        self,
        model_name: str = "claude-3-5-sonnet",
        provider: Optional[str] = None,
        max_context_files: int = 4,
    ) -> Dict[str, Any]:
        """Evaluate an AI model against this packaged task using real repository context."""
        instruction_path = os.path.join(self.task_dir, "instruction.md")
        test_file_path = self._find_test_file()
        task_meta = self._load_task_metadata()

        with open(instruction_path, "r", encoding="utf-8") as f:
            instruction_md = f.read()

        with open(test_file_path, "r", encoding="utf-8") as f:
            test_code = f.read()

        analyzer = CodebaseAnalyzer(self.target_repo_path, language=self.verifier.adapter.language_id)
        codebase_context = analyzer.get_targeted_context(max_files=max_context_files)

        prompt = f"""{self.eval_prompt_template}

Target Codebase Context:
{codebase_context}

Task Specification (instruction.md):
{instruction_md}

Return ONLY a clean unified Git diff (`git diff`) implementing this specification.
"""
        resolved_provider = infer_provider(model_name, explicit_provider=provider)
        t0 = time.monotonic()
        raw_diff = call_llm(
            prompt=prompt,
            model=model_name,
            provider=resolved_provider,
            language=self.verifier.adapter.language_id,
        )
        llm_latency_ms = round((time.monotonic() - t0) * 1000.0, 2)
        agent_diff = _clean_diff(raw_diff)

        result = self.verifier.verify(solution_diff=agent_diff, test_code=test_code)

        return {
            "model_tested": model_name,
            "provider": resolved_provider or Config.LLM_PROVIDER,
            "task_id": os.path.basename(self.task_dir),
            "language": self.verifier.adapter.language_id,
            "category": task_meta.get("category", "unknown"),
            "passed": result.is_valid,
            "baseline_passed": result.step1_baseline_pass,
            "patch_clean": result.patch_applied_cleanly,
            "tests_passed": result.step3_positive_pass,
            "fail_to_pass_count": len(result.fail_to_pass),
            "pass_to_pass_count": len(result.pass_to_pass),
            "llm_latency_ms": llm_latency_ms,
        }


class BatchBenchmarkRunner:
    """Evaluates multiple task packages across multiple frontier models and builds a leaderboard."""

    def __init__(self, target_repo_path: str, language: Optional[str] = None, timeout: int = 90):
        self.target_repo_path = os.path.abspath(target_repo_path)
        self.language = language
        self.timeout = timeout

    def discover_tasks(self, tasks_root: str) -> List[str]:
        if not os.path.isdir(tasks_root):
            return []
        dirs = []
        for entry in sorted(os.listdir(tasks_root)):
            full = os.path.join(tasks_root, entry)
            if os.path.isdir(full) and os.path.isfile(os.path.join(full, "instruction.md")):
                dirs.append(full)
        return dirs

    def run(
        self,
        task_dirs: List[str],
        models: List[str],
        output_dir: str = "benchmark_results",
    ) -> Dict[str, Any]:
        os.makedirs(output_dir, exist_ok=True)
        rows: List[Dict[str, Any]] = []

        for task_dir in task_dirs:
            evaluator = BenchmarkEvaluator(
                task_dir=task_dir,
                target_repo_path=self.target_repo_path,
                language=self.language,
                timeout=self.timeout,
            )
            for model in models:
                try:
                    rows.append(evaluator.evaluate_model(model_name=model))
                except Exception as exc:
                    import logging
                    logging.error("Model %s evaluation failed: %s", model, exc)
                    rows.append({"model_tested": model, "passed": False, "patch_clean": False, "llm_latency_ms": 0, "error": str(exc), "status": "FAILED"})

        leaderboard = self._aggregate_leaderboard(rows)
        report = {
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "repo_path": self.target_repo_path,
            "total_tasks": len(task_dirs),
            "models": models,
            "leaderboard": leaderboard,
            "results": rows,
        }

        json_path = os.path.join(output_dir, "leaderboard.json")
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2)

        md_path = os.path.join(output_dir, "leaderboard.md")
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(self.render_markdown_leaderboard(report))

        return report

    @staticmethod
    def _aggregate_leaderboard(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        grouped: Dict[str, List[Dict[str, Any]]] = {}
        for r in rows:
            grouped.setdefault(r["model_tested"], []).append(r)

        board: List[Dict[str, Any]] = []
        for model, items in grouped.items():
            total = len(items)
            solved = sum(1 for x in items if x["passed"])
            patch_clean = sum(1 for x in items if x["patch_clean"])
            avg_ms = round(sum(x["llm_latency_ms"] for x in items) / max(1, total), 2)

            by_category: Dict[str, Dict[str, int]] = {}
            by_language: Dict[str, Dict[str, int]] = {}
            for x in items:
                cat = x.get("category", "unknown")
                lang = x.get("language", "unknown")
                by_category.setdefault(cat, {"passed": 0, "total": 0})
                by_category[cat]["total"] += 1
                if x["passed"]:
                    by_category[cat]["passed"] += 1
                by_language.setdefault(lang, {"passed": 0, "total": 0})
                by_language[lang]["total"] += 1
                if x["passed"]:
                    by_language[lang]["passed"] += 1

            board.append({
                "model": model,
                "solved": solved,
                "total": total,
                "pass_at_1_pct": round((solved / max(1, total)) * 100.0, 1),
                "clean_patch_rate_pct": round((patch_clean / max(1, total)) * 100.0, 1),
                "avg_latency_ms": avg_ms,
                "by_category": by_category,
                "by_language": by_language,
            })

        return sorted(board, key=lambda b: (-b["pass_at_1_pct"], b["avg_latency_ms"]))

    @staticmethod
    def render_markdown_leaderboard(report: Dict[str, Any]) -> str:
        lines = [
            "# Frontier AI Benchmark Leaderboard",
            "",
            f"- **Repository:** `{os.path.basename(report['repo_path'])}`",
            f"- **Tasks Evaluated:** {report['total_tasks']}",
            f"- **Generated At:** {report['generated_at']}",
            "",
            "| Rank | Model | Pass@1 | Solved | Clean Patch Rate | Avg Latency (ms) |",
            "| :---: | :--- | :---: | :---: | :---: | :---: |",
        ]
        for idx, entry in enumerate(report.get("leaderboard") or [], 1):
            lines.append(
                f"| {idx} | `{entry['model']}` | **{entry['pass_at_1_pct']}%** | "
                f"{entry['solved']}/{entry['total']} | {entry['clean_patch_rate_pct']}% | {entry['avg_latency_ms']} |"
            )
        lines.append("")
        return "\n".join(lines)
