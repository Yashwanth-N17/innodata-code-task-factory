#!/usr/bin/env python3
"""
Main Entry Point: Automated Coding Task Factory.
Orchestrates Member 1, 2, 3, and 4 pipelines into an end-to-end repeatable system.
"""

import argparse
import os
import sys
import time

from src.analyzer import CodebaseAnalyzer
from src.spec_generator import SpecGenerator
from src.coder_agent import CoderAgent
from src.test_agent import TestAgent
from src.verifier import SandboxVerifier
from src.packager import TaskPackager
from src.variation_controller import VariationController
from src.config import Config


def main():
    parser = argparse.ArgumentParser(
        description="InnoData Automated Coding Task Factory (Track 1)"
    )
    parser.add_argument(
        "--repo",
        type=str,
        default="sample_repo",
        help="Path to the target codebase repository",
    )
    parser.add_argument(
        "--output",
        type=str,
        default="tasks",
        help="Directory to store generated and verified task packages",
    )
    parser.add_argument(
        "--count",
        type=int,
        default=2,
        help="Number of diverse task packages to generate",
    )
    parser.add_argument(
        "--mock",
        action="store_true",
        help="Run in mock mode without consuming external LLM API credits",
    )
    args = parser.parse_args()

    if args.mock:
        Config.LLM_PROVIDER = "mock"

    repo_path = os.path.abspath(args.repo)
    if not os.path.exists(repo_path):
        print(f"[ERROR] Target repository not found at: {repo_path}")
        sys.exit(1)

    print("=" * 65)
    print(" 🏭 INNODATA AUTOMATED CODING TASK FACTORY")
    print(f" Target Repository : {repo_path}")
    print(f" LLM Provider     : {Config.LLM_PROVIDER} ({Config.MODEL_NAME})")
    print(f" Task Count       : {args.count}")
    print("=" * 65)

    # 1. Initialize Pipeline Agents
    print("\n[Phase 1] Initializing Pipeline & Scanning Codebase (Maximus)...")
    analyzer = CodebaseAnalyzer(repo_path)
    context = analyzer.get_context_prompt()
    print(f" Scanned Codebase Summary:\n{context}")

    spec_gen = SpecGenerator()
    coder = CoderAgent()
    tester = TestAgent()
    verifier = SandboxVerifier(repo_path)
    packager = TaskPackager(args.output)
    variation_ctrl = VariationController()

    categories = variation_ctrl.select_category(count=args.count)

    verified_tasks = []

    # 2. Run Generation & Verification Loop
    for idx, cat in enumerate(categories, 1):
        task_id = f"task_{int(time.time())}_{idx:03d}_{cat['id']}"
        print(f"\n[Phase 2.{idx}] Generating Task #{idx}: {cat['name']}")
        print(f" Archetype: {cat['description']}")

        # Maximus: Spec
        print("  -> Maximus: Generating specification (instruction.md)...")
        instruction_md = spec_gen.generate(codebase_context=context, category=cat["id"])

        # Manya: Reference Code & Tests
        print("  -> Manya: Generating reference solution (solution.diff)...")
        solution_diff = coder.generate_diff(codebase_context=context, instruction_md=instruction_md)

        print("  -> Manya: Generating pytest test suite (test_patch.py)...")
        test_code = tester.generate_tests(codebase_context=context, instruction_md=instruction_md)

        # Shreya: Verification
        print("  -> Shreya: Running SWE-bench 3-Step Verification Matrix...")
        verification = verifier.verify(solution_diff=solution_diff, test_code=test_code)

        print(f"     [1] Baseline Existing Tests: {'PASS' if verification.step1_baseline_pass else 'FAIL'}")
        print(f"     [2] Negative Check (Fail-to-Pass): {'FAIL (Verified)' if verification.step2_negative_fail else 'PASS (Invalid: False Positive)'}")
        print(f"     [3] Positive Check (Pass-to-Pass): {'PASS' if verification.step3_positive_pass else 'FAIL'}")

        # Packaging
        package_dir = packager.package(
            task_id=task_id,
            repo_name=os.path.basename(repo_path),
            category=cat["id"],
            instruction_md=instruction_md,
            solution_diff=solution_diff,
            test_code=test_code,
            verification=verification,
        )
        print(f"  -> Shreya: Packaged into: {package_dir}")
        verified_tasks.append((task_id, verification.is_valid))

    # 3. Final Summary & Yashwant Variation Report
    print("\n" + "=" * 65)
    print(" 📊 FACTORY EXECUTION SUMMARY")
    print("=" * 65)
    for tid, is_valid in verified_tasks:
        status_str = "✅ VERIFIED & VALID" if is_valid else "⚠️ FAILED VERIFICATION"
        print(f" • {tid}: {status_str}")

    var_report = variation_ctrl.get_variation_report()
    print("\n[Phase 3] Meaningful Variation Report (Yashwant):")
    print(f" Diversity Score : {var_report['diversity_score'] * 100}%")
    print(f" Distribution    : {var_report['distribution']}")
    print("=" * 65)


if __name__ == "__main__":
    main()
