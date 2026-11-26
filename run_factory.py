#!/usr/bin/env python3
"""
Main Entry Point: Universal Polyglot Automated Coding Task Factory (Track 1).
Orchestrates Features 1 through 9 across Python, TypeScript, C++, Go, Rust, and Java,
supporting Git URL cloning, 2-tier deep analysis, local or Docker sandboxing,
mutation rigor audits, and SWE-bench JSONL export.
"""

import argparse
import os
import sys
import time
import uuid

from src.analyzer import CodebaseAnalyzer
from src.coder_agent import CoderAgent
from src.config import Config, get_usage_stats, reset_usage_stats
from src.exporter import check_test_mutation_rigor, export_swebench_jsonl
from src.languages import LanguageRegistry
from src.packager import TaskPackager
from src.repo_loader import bootstrap_environment, resolve_repository
from src.spec_generator import SpecGenerator
from src.test_agent import TestAgent
from src.variation_controller import VariationController
from src.verifier import SandboxVerifier


def _is_test_side_failure(err_msg: str) -> bool:
    """Heuristically determine whether a Step 3 failure originated in the synthesized test file."""
    lower = (err_msg or "").lower()
    test_file_mentioned = any(
        tok in lower for tok in ("test_feature_patch", "feature_patch_test", "featurepatchtest")
    )
    if not test_file_mentioned:
        return False
    return any(
        err_tok in lower for err_tok in (
            "syntaxerror", "indentationerror", "importerror", "modulenotfounderror",
            "cannot find module", "err_module_not_found", "error:", "no matching function",
            "undeclared", "has no member",
        )
    )


def main():
    parser = argparse.ArgumentParser(
        description="InnoData Automated Coding Task Factory (Track 1)"
    )
    parser.add_argument("--repo", type=str, required=True, help="Path or Git URL of the target repository")
    parser.add_argument(
        "--language",
        type=str,
        default="auto",
        help=f"Target language override ('auto', {', '.join(LanguageRegistry.list_languages())})",
    )
    parser.add_argument("--repo-cache", type=str, default="repos", help="Local cache directory for cloned Git URLs")
    parser.add_argument("--update-repo", action="store_true", help="Pull latest changes if repo is cached")
    parser.add_argument("--setup-env", action="store_true", help="Bootstrap isolated repo dependencies (.venv / npm)")
    parser.add_argument("--output", type=str, default="tasks", help="Output directory for verified task packages")
    parser.add_argument("--count", type=int, default=2, help="Number of diverse task packages to generate")
    parser.add_argument("--timeout", type=int, default=90, help="Timeout in seconds per sandbox build/test step")
    parser.add_argument("--sandbox", choices=["local", "docker"], default="local", help="Execution sandbox backend")
    parser.add_argument("--docker-image", type=str, default=None, help="Override container image when --sandbox docker")
    parser.add_argument("--check-rigor", action="store_true", help="Run mutation check on verified tests")
    parser.add_argument("--export-jsonl", type=str, default="", help="Optional path to export SWE-bench JSONL")
    parser.add_argument("--mock", action="store_true", help="Run in mock mode without consuming API credits")
    args = parser.parse_args()

    if args.mock:
        Config.LLM_PROVIDER = "mock"
    reset_usage_stats()

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

    analyzer = CodebaseAnalyzer(repo_path, language=lang_override)
    if args.setup_env:
        print(f"[Bootstrap] {bootstrap_environment(repo_path, analyzer.language)}")

    scan_data = analyzer.scan()

    print("=" * 65)
    print(" 🏭 INNODATA AUTOMATED CODING TASK FACTORY")
    print(f" Target Repository : {repo_path}")
    print(f" Detected Language : {analyzer.adapter.display_name} ({scan_data['test_framework']})")
    print(f" Sandbox Mode      : {args.sandbox}")
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
    try:
        verifier = SandboxVerifier(
            repo_path,
            timeout=args.timeout,
            adapter=analyzer.adapter,
            sandbox=args.sandbox,
            docker_image=args.docker_image,
        )
    except RuntimeError as e:
        print(f"[ERROR] {e}")
        sys.exit(1)

    packager = TaskPackager(args.output)
    variation_ctrl = VariationController()

    categories = variation_ctrl.select_category(count=args.count, language=analyzer.language)
    verified_tasks = []

    for idx, cat in enumerate(categories, 1):
        task_id = f"task_{int(time.time())}_{idx:03d}_{cat['id']}_{uuid.uuid4().hex[:6]}"
        try:
            print(f"\n[Phase 2.{idx}] Generating Task #{idx}: {cat['name']}")
            print(f" Archetype: {cat['description']}")
            print(f" Variant  : {cat.get('variant', '')}")

            # Rotate focus modules across batch tasks for maximum repo coverage
            spec_context = analyzer.get_context_prompt(target_offset=idx - 1)
            coder_context = analyzer.get_targeted_context(target_offset=idx - 1)

            # Step A: Specification (Tier 1 only - zero line number / implementation leakage)
            print("  -> Generating specification (instruction.md)...")
            instruction_md = spec_gen.generate(
                codebase_context=spec_context,
                category=cat["id"],
                variant=cat.get("variant"),
            )

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
            if not verification.step1_baseline_pass:
                print("     [Repo Setup Error] Baseline test suite failed; skipping self-healing repairs.")
            while not verification.is_valid and repair_attempt < max_repairs and verification.step1_baseline_pass:
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
                    if _is_test_side_failure(err_msg):
                        print(f"     [Self-Healing] Test-side compile/import error detected. Requesting TestAgent repair ({repair_attempt}/{max_repairs})...")
                        test_code = tester.repair_tests(
                            codebase_context=coder_context,
                            instruction_md=instruction_md,
                            previous_tests=test_code,
                            error_message=err_msg,
                        )
                    else:
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

            rigor = None
            if args.check_rigor and verification.is_valid:
                rigor = check_test_mutation_rigor(verifier, solution_diff, test_code)
                print(f"     [Rigor Audit] {rigor['note']}")

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
                rigor_check=rigor,
            )
            print(f"  -> Packaged into: {package_dir}")
            verified_tasks.append((task_id, verification.is_valid))
        except Exception as e:
            print(f"  -> [ERROR] Task {idx} failed: {e}")
            verified_tasks.append((task_id, False))
            continue

    print("\n" + "=" * 65)
    print(" 📊 FACTORY EXECUTION SUMMARY")
    print("=" * 65)
    for tid, is_valid in verified_tasks:
        status_str = "✅ VERIFIED & VALID" if is_valid else "⚠️ FAILED VERIFICATION"
        print(f" • {tid}: {status_str}")

    var_report = variation_ctrl.get_variation_report()
    usage = get_usage_stats()
    print("\n[Phase 3] Meaningful Variation & Telemetry Report:")
    print(f" Diversity Score : {var_report['diversity_score'] * 100}%")
    print(f" Distribution    : {var_report['distribution']}")
    print(f" LLM Calls       : {usage['total_calls']} (est. {usage['est_input_tokens']} in / {usage['est_output_tokens']} out tokens, {usage['total_latency_ms']} ms)")

    if args.export_jsonl:
        exp = export_swebench_jsonl(args.output, args.export_jsonl)
        print(f" Exported JSONL  : {exp['exported_count']} verified tasks -> {exp['output_path']}")
    print("=" * 65)

    valid_count = sum(1 for _, v in verified_tasks if v)
    if args.count and args.count > 0 and valid_count == 0 and len(verified_tasks) > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
