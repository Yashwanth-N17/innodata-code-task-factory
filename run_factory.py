#!/usr/bin/env python3
"""
Main Entry Point: Universal Polyglot Automated Coding Task Factory (Track 1).
Orchestrates Features 1 through 9 across any supported language (Python, TypeScript, C++, etc.)
and accepts both local directories and remote GitHub repository URLs.
"""

import argparse
import os
import sys
import time

from src.analyzer import CodebaseAnalyzer
from src.coder_agent import CoderAgent
from src.config import Config
from src.languages import LanguageRegistry
from src.packager import TaskPackager
from src.repo_loader import resolve_repository
from src.spec_generator import SpecGenerator
from src.test_agent import TestAgent
from src.variation_controller import VariationController
from src.verifier import SandboxVerifier


def main():
    parser = argparse.ArgumentParser(
        description="InnoData Automated Coding Task Factory (Track 1)"
    )
    parser.add_argument(
        "--repo",
        type=str,
        required=True,
        help="Path or Git URL of the target codebase repository to benchmark",
    )
    parser.add_argument(
        "--language",
        type=str,
        default="auto",
        help=f"Target language override ('auto', {', '.join(LanguageRegistry.list_languages())})",
    )
    parser.add_argument(
        "--repo-cache",
        type=str,
        default="repos",
        help="Local cache directory used when cloning remote repository URLs",
    )
    parser.add_argument(
        "--update-repo",
        action="store_true",
        help="Pull latest changes if the remote repository is already cached",
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
        "--timeout",
        type=int,
        default=90,
        help="Timeout in seconds per sandbox build/test step",
    )
    parser.add_argument(
        "--mock",
        action="store_true",
        help="Run in mock mode without consuming external LLM API credits",
    )
    args = parser.parse_args()

    if args.mock:
        Config.LLM_PROVIDER = "mock"

    try:
        repo_path, repo_meta = resolve_repository(
            args.repo,
            cache_dir=args.repo_cache,
            update=args.update_repo,
        )
    except Exception as e:
        print(f"[ERROR] {e}")
        sys.exit(1)

    lang_override = None if args.language.strip().lower() == "auto" else args.language

    # 1. Initialize Pipeline & Scan Whole Repository
    analyzer = CodebaseAnalyzer(repo_path, language=lang_override)
    scan_data = analyzer.scan()
    spec_context = analyzer.get_context_prompt()
    coder_context = analyzer.get_targeted_context()

    print("=" * 65)
    print(" 🏭 INNODATA AUTOMATED CODING TASK FACTORY")
    print(f" Target Repository : {repo_path}")
    print(f" Detected Language : {analyzer.adapter.display_name} ({scan_data['test_framework']})")
    if repo_meta.get("commit_hash"):
        print(f" Base Commit       : {repo_meta['commit_hash'][:12]}")
    print(f" LLM Provider      : {Config.LLM_PROVIDER} ({Config.MODEL_NAME})")
    print(f" Task Count        : {args.count}")
    print("=" * 65)

    print("\n[Phase 1] Whole-Repository Scan Summary:")
    print(f" Source Files Found : {len(scan_data['files'])}")
    print(f" Test Files Found   : {len(scan_data['existing_tests'])}")
    print(f" Target Focus Files : {', '.join(scan_data.get('target_files') or []) or 'None'}")

    spec_gen = SpecGenerator()
    coder = CoderAgent(language_adapter=analyzer.adapter)
    tester = TestAgent(language_adapter=analyzer.adapter)
    verifier = SandboxVerifier(repo_path, timeout=args.timeout, adapter=analyzer.adapter)
    packager = TaskPackager(args.output)
    variation_ctrl = VariationController()

    categories = variation_ctrl.select_category(count=args.count)
    verified_tasks = []

    # 2. Run Generation & Verification Loop
    for idx, cat in enumerate(categories, 1):
        task_id = f"task_{int(time.time())}_{idx:03d}_{cat['id']}"
        print(f"\n[Phase 2.{idx}] Generating Task #{idx}: {cat['name']}")
        print(f" Archetype: {cat['description']}")

        # Step A: Specification (Tier 1 only - zero line number / implementation leakage)
        print("  -> Generating specification (instruction.md)...")
        instruction_md = spec_gen.generate(codebase_context=spec_context, category=cat["id"])

        # Step B: Reference Code & Tests (Tier 1 + Tier 2 numbered source)
        print("  -> Generating reference solution (solution.diff)...")
        solution_diff = coder.generate_diff(codebase_context=coder_context, instruction_md=instruction_md)

        print(f"  -> Generating test suite ({analyzer.adapter.test_file_relpath})...")
        test_code = tester.generate_tests(codebase_context=coder_context, instruction_md=instruction_md)

        # Step C: Verification with Self-Correction Retry Loop
        print("  -> Running SWE-bench 3-Step Verification Matrix...")
        verification = verifier.verify(solution_diff=solution_diff, test_code=test_code)

        max_repairs = 2
        repair_attempt = 0
        while not verification.is_valid and repair_attempt < max_repairs:
            repair_attempt += 1
            if not verification.patch_applied_cleanly:
                err_msg = verification.logs.get("patch_application", "Patch failed to apply.")
                print(f"     [Self-Healing] Patch failure detected. Requesting CoderAgent repair ({repair_attempt}/{max_repairs})...")
                solution_diff = coder.repair_diff(
                    codebase_context=coder_context,
                    instruction_md=instruction_md,
                    previous_diff=solution_diff,
                    error_message=err_msg,
                )
            elif not verification.step3_positive_pass:
                err_msg = verification.logs.get("step3_positive_check", "Tests failed.")
                print(f"     [Self-Healing] Step 3 failure detected. Requesting CoderAgent repair ({repair_attempt}/{max_repairs})...")
                solution_diff = coder.repair_diff(
                    codebase_context=coder_context,
                    instruction_md=instruction_md,
                    previous_diff=solution_diff,
                    error_message=err_msg,
                )
            elif not verification.step2_negative_fail:
                err_msg = "Negative check failed: tests passed on unpatched code (false positive)."
                print(f"     [Self-Healing] Test validity failure detected. Requesting TestAgent repair ({repair_attempt}/{max_repairs})...")
                test_code = tester.repair_tests(
                    codebase_context=coder_context,
                    instruction_md=instruction_md,
                    previous_tests=test_code,
                    error_message=err_msg,
                )

            print(f"     -> Re-verifying repaired artifacts (Attempt {repair_attempt})...")
            verification = verifier.verify(solution_diff=solution_diff, test_code=test_code)

        print(f"     [1] Baseline Existing Tests: {'PASS' if verification.step1_baseline_pass else 'FAIL'}")
        print(f"     [2] Negative Check (Fail-to-Pass): {'FAIL (Verified)' if verification.step2_negative_fail else 'PASS (Invalid: False Positive)'}")
        print(f"     [3] Positive Check (Pass-to-Pass): {'PASS' if verification.step3_positive_pass else 'FAIL'}")

        # Step D: Packaging
        package_dir = packager.package(
            task_id=task_id,
            repo_name=repo_meta.get("repo_name") or os.path.basename(repo_path),
            category=cat["id"],
            instruction_md=instruction_md,
            solution_diff=solution_diff,
            test_code=test_code,
            verification=verification,
            language_adapter=analyzer.adapter,
            manifest=scan_data.get("manifest"),
            repo_meta=repo_meta,
        )
        print(f"  -> Packaged into: {package_dir}")
        verified_tasks.append((task_id, verification.is_valid))

    # 3. Final Summary & Variation Report
    print("\n" + "=" * 65)
    print(" 📊 FACTORY EXECUTION SUMMARY")
    print("=" * 65)
    for tid, is_valid in verified_tasks:
        status_str = "✅ VERIFIED & VALID" if is_valid else "⚠️ FAILED VERIFICATION"
        print(f" • {tid}: {status_str}")

    var_report = variation_ctrl.get_variation_report()
    print("\n[Phase 3] Meaningful Variation Report:")
    print(f" Diversity Score : {var_report['diversity_score'] * 100}%")
    print(f" Distribution    : {var_report['distribution']}")
    print("=" * 65)


if __name__ == "__main__":
    main()
