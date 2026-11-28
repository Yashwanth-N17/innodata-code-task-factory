#!/usr/bin/env python3
"""
CLI Runner for Feature 8: Multi-Model Frontier AI Benchmark & Leaderboard Engine.
Evaluates packaged SWE-bench tasks against one or more AI models and saves
leaderboard.json + leaderboard.md.
"""

import argparse
import sys

from src.benchmark_evaluator import BatchBenchmarkRunner
from src.config import Config
from src.repo_loader import resolve_repository


def main():
    parser = argparse.ArgumentParser(
        description="Evaluate AI coding models against generated task packages"
    )
    parser.add_argument("--repo", required=True, help="Target repository path or Git URL")
    parser.add_argument("--tasks-dir", default="tasks", help="Directory containing packaged tasks")
    parser.add_argument(
        "--models",
        default="gemini-2.0-flash",
        help="Comma-separated list of model identifiers (e.g. gemini-2.0-flash,claude-3-5-sonnet,gpt-4o)",
    )
    parser.add_argument("--output", default="benchmark_results", help="Output directory for leaderboard reports")
    parser.add_argument("--language", default="auto", help="Language override ('auto' or language id)")
    parser.add_argument("--timeout", type=int, default=90, help="Verification timeout in seconds")
    parser.add_argument("--mock", action="store_true", help="Run offline using mock responses")
    args = parser.parse_args()

    if args.mock:
        Config.LLM_PROVIDER = "mock"

    try:
        repo_path, _ = resolve_repository(args.repo)
    except Exception as e:
        print(f"[ERROR] {e}")
        sys.exit(1)

    lang = None if args.language.strip().lower() == "auto" else args.language
    runner = BatchBenchmarkRunner(repo_path, language=lang, timeout=args.timeout)
    task_dirs = runner.discover_tasks(args.tasks_dir)
    if not task_dirs:
        print(f"[ERROR] No task packages found in: {args.tasks_dir}")
        sys.exit(1)

    models = [m.strip() for m in args.models.split(",") if m.strip()]
    report = runner.run(task_dirs=task_dirs, models=models, output_dir=args.output)
    print(runner.render_markdown_leaderboard(report))
    print(f"Saved reports to: {args.output}/leaderboard.json and {args.output}/leaderboard.md")


if __name__ == "__main__":
    main()
