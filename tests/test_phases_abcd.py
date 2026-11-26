"""
Comprehensive Tests for Phases A through D:
- Phase A: TS monorepo workspace re-linking, C++ multi-line/operator/.inl/POST_BUILD,
           per-repo venv detection, module rotation, variant adaptation, Step 3 failure triage
- Phase B: Anthropic provider, LLM usage telemetry, context-aware BenchmarkEvaluator & Leaderboard
- Phase C: Tree-sitter optional fallback, Go/Rust/Java adapters, Docker sandbox command & guard
- Phase D: SWE-bench JSONL exporter, test mutation rigor auditor, end-to-end CLI flags
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from run_factory import _is_test_side_failure
from src.analyzer import CodebaseAnalyzer
from src.benchmark_evaluator import BatchBenchmarkRunner, BenchmarkEvaluator, infer_provider
from src.config import Config, call_llm, get_usage_stats, reset_usage_stats
from src.exporter import check_test_mutation_rigor, export_swebench_jsonl
from src.languages import CppAdapter, GoAdapter, JavaAdapter, LanguageRegistry, PythonAdapter, RustAdapter, TypeScriptAdapter
from src.languages.treesitter_helper import extract_symbols_with_treesitter, is_treesitter_available
from src.packager import TaskPackager
from src.sandbox_docker import DockerSandboxRunner
from src.spec_generator import SpecGenerator
from src.variation_controller import VariationController
from src.verifier import SandboxVerifier


def _tool_available(*cmd) -> bool:
    try:
        res = subprocess.run(list(cmd), capture_output=True, text=True)
        return res.returncode == 0
    except Exception:
        return False


class PhaseALiveHardeningTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_ts_monorepo_sandbox_relinks_workspaces_into_work_dir(self):
        repo = os.path.join(self.tmp, "zod_mono")
        pkg_zod = os.path.join(repo, "packages", "zod")
        pkg_mini = os.path.join(repo, "packages", "mini")
        nm_root = os.path.join(repo, "node_modules")
        nm_scoped = os.path.join(nm_root, "@zod")
        nm_vitest = os.path.join(nm_root, "vitest")
        os.makedirs(pkg_zod)
        os.makedirs(pkg_mini)
        os.makedirs(nm_scoped)
        os.makedirs(nm_vitest)

        with open(os.path.join(repo, "package.json"), "w", encoding="utf-8") as f:
            json.dump({"name": "zod-mono", "workspaces": ["packages/*"]}, f)
        with open(os.path.join(pkg_zod, "package.json"), "w", encoding="utf-8") as f:
            json.dump({"name": "zod"}, f)
        with open(os.path.join(pkg_mini, "package.json"), "w", encoding="utf-8") as f:
            json.dump({"name": "@zod/mini"}, f)

        # Simulate pnpm/npm workspace symlinks inside repo/node_modules
        os.symlink(pkg_zod, os.path.join(nm_root, "zod"), target_is_directory=True)
        os.symlink(pkg_mini, os.path.join(nm_scoped, "mini"), target_is_directory=True)

        work_dir = os.path.join(self.tmp, "sandbox_step3")
        shutil.copytree(repo, work_dir, ignore=shutil.ignore_patterns("node_modules"))
        TypeScriptAdapter().prepare_sandbox(repo, work_dir)

        # Workspace symlinks MUST point inside work_dir, while 3rd-party deps link to repo
        self.assertEqual(os.path.realpath(os.path.join(work_dir, "node_modules", "zod")), os.path.realpath(os.path.join(work_dir, "packages", "zod")))
        self.assertEqual(os.path.realpath(os.path.join(work_dir, "node_modules", "@zod", "mini")), os.path.realpath(os.path.join(work_dir, "packages", "mini")))
        self.assertEqual(os.path.realpath(os.path.join(work_dir, "node_modules", "vitest")), os.path.realpath(nm_vitest))

    def test_cpp_multiline_operator_inl_and_post_build_option(self):
        repo = os.path.join(self.tmp, "jsoncpp_adv")
        inc = os.path.join(repo, "include", "json")
        lib = os.path.join(repo, "src", "lib_json")
        os.makedirs(inc)
        os.makedirs(lib)

        with open(os.path.join(repo, "CMakeLists.txt"), "w", encoding="utf-8") as f:
            f.write(
                "project(jsoncpp)\n"
                'option(JSONCPP_WITH_TESTS "Tests" ON)\n'
                'option(JSONCPP_WITH_POST_BUILD_UNITTEST "Post build" ON)\n'
            )
        with open(os.path.join(inc, "value.h"), "w", encoding="utf-8") as f:
            f.write(
                "namespace Json {\n"
                "class JSON_API Value {\n"
                "public:\n"
                "    Value& operator[](const char* key);\n"
                "    bool isMember(\n"
                "        const char* key,\n"
                "        const char* end\n"
                "    ) const;\n"
                "};\n"
                "}\n"
            )
        with open(os.path.join(lib, "json_value.cpp"), "w", encoding="utf-8") as f:
            f.write('#include "json/value.h"\n')
        with open(os.path.join(lib, "json_valueiterator.inl"), "w", encoding="utf-8") as f:
            f.write("// inline iterator helpers\n")

        az = CodebaseAnalyzer(repo)
        data = az.scan()
        self.assertEqual(data["language"], "cpp")
        self.assertIn("JSONCPP_WITH_TESTS", data["manifest"]["test_options"])
        self.assertIn("JSONCPP_WITH_POST_BUILD_UNITTEST", data["manifest"]["disable_options"])

        cls = [c for c in data["classes"] if c["name"] == "Json::Value"][0]
        self.assertTrue(any("operator[]" in m for m in cls["public_methods"]), cls["public_methods"])
        self.assertTrue(any("isMember(" in m for m in cls["public_methods"]), cls["public_methods"])

        # .inl should be paired automatically alongside value.h and json_value.cpp
        targets = CppAdapter().select_targets(repo, data, max_files=1)
        self.assertTrue(any("json_valueiterator.inl" in t for t in targets), targets)

    def test_python_venv_detection_and_module_rotation(self):
        repo = os.path.join(self.tmp, "py_multi")
        os.makedirs(os.path.join(repo, ".venv", "bin"))
        fake_py = os.path.join(repo, ".venv", "bin", "python")
        with open(fake_py, "w", encoding="utf-8") as f:
            f.write("#!/bin/sh\n")

        for mod_name, cls_name in [("alpha.py", "Alpha"), ("beta.py", "Beta"), ("gamma.py", "Gamma")]:
            with open(os.path.join(repo, mod_name), "w", encoding="utf-8") as f:
                f.write(f"class {cls_name}:\n    def run(self) -> int:\n        return 1\n")

        verifier = SandboxVerifier(repo)
        self.assertEqual(verifier.python_executable, fake_py)

        az = CodebaseAnalyzer(repo)
        ctx0 = az.get_targeted_context(max_files=1, target_offset=0)
        ctx1 = az.get_targeted_context(max_files=1, target_offset=1)
        self.assertIn("### File: alpha.py", ctx0)
        self.assertIn("### File: beta.py", ctx1)

    def test_variation_adaptation_spec_retry_and_step3_triage(self):
        vc = VariationController()
        ts_picks = vc.select_category(4, language="typescript")
        self.assertTrue(any("null/undefined" in p["variant"] or "Map or Set" in p["variant"] for p in ts_picks))

        vc.reset()
        cpp_picks = vc.select_category(4, language="cpp")
        self.assertTrue(any("std::unordered_map" in p["variant"] or "std::invalid_argument" in p["variant"] for p in cpp_picks))

        # Test SpecGenerator structure validation retry with mocked call_llm
        gen = SpecGenerator()
        incomplete = "# Draft Title\nOnly problem description."
        complete = (
            "# Feature\n## Problem Description\nx\n"
            "## Business Requirements\n- r1\n"
            "## Input & Output Schema\n- s1\n"
            "## Specification & Acceptance Criteria\n- c1\n"
            "## Edge Cases & Error Handling\n- e1\n- e2\n- e3\n"
        )
        with patch("src.spec_generator.call_llm", side_effect=[incomplete, complete]) as mocked_llm:
            out = gen.generate("ctx", category="new_feature", variant="Add method")
            self.assertEqual(mocked_llm.call_count, 2)
            self.assertTrue(gen.validate_structure(out)[0])

        # Test Step 3 error triage
        self.assertTrue(_is_test_side_failure("ImportError while importing test module 'tests/test_feature_patch.py'"))
        self.assertTrue(_is_test_side_failure("tests/test_feature_patch.cpp:4:1: error: 'foo' was not declared"))
        self.assertFalse(_is_test_side_failure("AssertionError: expected 5 got 3 in src/click/core.py"))


class PhaseBBenchmarkAndAnthropicTests(unittest.TestCase):
    def setUp(self):
        self.orig_provider = Config.LLM_PROVIDER
        self.orig_anth_key = Config.ANTHROPIC_API_KEY
        self.tmp = tempfile.mkdtemp()
        reset_usage_stats()

    def tearDown(self):
        Config.LLM_PROVIDER = self.orig_provider
        Config.ANTHROPIC_API_KEY = self.orig_anth_key
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_anthropic_provider_and_usage_stats(self):
        mock_anthropic_mod = MagicMock()
        mock_client = MagicMock()
        mock_anthropic_mod.Anthropic.return_value = mock_client
        mock_block = MagicMock(type="text", text="claude diff output")
        mock_client.messages.create.return_value = MagicMock(content=[mock_block])

        with patch.dict(sys.modules, {"anthropic": mock_anthropic_mod}):
            out = call_llm(
                prompt="Generate diff",
                system_prompt="System rule",
                provider="anthropic",
                model="claude-3-5-sonnet-20241022",
                temperature=0.1,
                max_tokens=1024,
                api_key="anth_test_key",
                initial_delay=0.0,
            )

        self.assertEqual(out, "claude diff output")
        mock_anthropic_mod.Anthropic.assert_called_once_with(api_key="anth_test_key", timeout=60.0)
        mock_client.messages.create.assert_called_once_with(
            model="claude-3-5-sonnet-20241022",
            max_tokens=1024,
            messages=[{"role": "user", "content": "Generate diff"}],
            system="System rule",
            temperature=0.1,
        )

        stats = get_usage_stats()
        self.assertEqual(stats["total_calls"], 1)
        self.assertIn("anthropic:claude-3-5-sonnet-20241022", stats["by_model"])

    def test_infer_provider_respects_live_and_mock_modes(self):
        Config.LLM_PROVIDER = "gemini"
        self.assertEqual(infer_provider("claude-3-5-sonnet"), "anthropic")
        self.assertEqual(infer_provider("gpt-4o"), "openai")
        self.assertEqual(infer_provider("gemini-2.0-flash"), "gemini")
        self.assertEqual(infer_provider("deepseek-chat"), "deepseek")
        Config.LLM_PROVIDER = "mock"
        self.assertEqual(infer_provider("claude-3-5-sonnet", explicit_provider="mock"), "mock")
        self.assertEqual(infer_provider("claude-3-5-sonnet"), "anthropic")

    @unittest.skipUnless(_tool_available(sys.executable, "-m", "pytest", "--version"), "pytest required")
    def test_benchmark_evaluator_and_batch_leaderboard(self):
        Config.LLM_PROVIDER = "mock"
        repo = os.path.join(self.tmp, "repo")
        os.makedirs(os.path.join(repo, "tests"))
        with open(os.path.join(repo, "service.py"), "w", encoding="utf-8") as f:
            f.write("class PaymentProcessor:\n    def charge(self, amt: float) -> bool:\n        return amt > 0\n")
        with open(os.path.join(repo, "tests", "test_svc.py"), "w", encoding="utf-8") as f:
            f.write("from service import PaymentProcessor\ndef test_c():\n    assert PaymentProcessor().charge(1)\n")
        with open(os.path.join(repo, "pytest.ini"), "w", encoding="utf-8") as f:
            f.write("[pytest]\npythonpath = .\n")

        diff = (
            "--- a/service.py\n+++ b/service.py\n@@ -1,3 +1,6 @@\n"
            " class PaymentProcessor:\n"
            "     def charge(self, amt: float) -> bool:\n"
            "         return amt > 0\n"
            "+\n"
            "+    def validate_state(self) -> bool:\n"
            "+        return True\n"
        )
        test_code = (
            "from service import PaymentProcessor\n"
            "def test_val():\n    assert PaymentProcessor().validate_state() is True\n"
        )
        verifier = SandboxVerifier(repo)
        res = verifier.verify(diff, test_code)
        self.assertTrue(res.is_valid)

        tasks_dir = os.path.join(self.tmp, "tasks")
        tdir = TaskPackager(tasks_dir).package(
            task_id="task_bench_001",
            repo_name="repo",
            category="new_feature",
            instruction_md="# Feature\nAdd `validate_state()` to `PaymentProcessor` in `service.py`.\n",
            solution_diff=diff,
            test_code=test_code,
            verification=res,
        )

        # Verify evaluate_model forwards codebase context + model_name to call_llm
        ev = BenchmarkEvaluator(tdir, repo)
        with patch("src.benchmark_evaluator.call_llm", return_value=diff) as mocked_call:
            single = ev.evaluate_model(model_name="claude-3-5-sonnet")
            self.assertTrue(single["passed"])
            called_prompt = mocked_call.call_args.kwargs["prompt"]
            self.assertIn("Target Codebase Context:", called_prompt)
            self.assertIn("class PaymentProcessor", called_prompt)
            self.assertEqual(mocked_call.call_args.kwargs["model"], "claude-3-5-sonnet")

        out_dir = os.path.join(self.tmp, "bench_out")
        runner = BatchBenchmarkRunner(repo)
        with patch("src.benchmark_evaluator.call_llm", return_value=diff):
            report = runner.run([tdir], models=["claude-3-5-sonnet", "gpt-4o"], output_dir=out_dir)

        self.assertEqual(len(report["leaderboard"]), 2)
        self.assertEqual(report["leaderboard"][0]["pass_at_1_pct"], 100.0)
        self.assertTrue(os.path.isfile(os.path.join(out_dir, "leaderboard.json")))
        self.assertTrue(os.path.isfile(os.path.join(out_dir, "leaderboard.md")))


class PhaseCUniversalLanguagesAndDockerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_treesitter_optional_graceful_fallback(self):
        # Should return bool / Optional without throwing even if tree-sitter packages aren't installed
        avail = is_treesitter_available("python")
        self.assertIsInstance(avail, bool)
        out = extract_symbols_with_treesitter("nonexistent_lang_xyz", "def f(): pass")
        self.assertIsNone(out)

    def test_go_rust_java_detection_and_scanning(self):
        # Go
        go_repo = os.path.join(self.tmp, "go_app")
        os.makedirs(go_repo)
        with open(os.path.join(go_repo, "go.mod"), "w", encoding="utf-8") as f:
            f.write("module example.com/app\ngo 1.22\n")
        with open(os.path.join(go_repo, "calc.go"), "w", encoding="utf-8") as f:
            f.write('package main\nimport "fmt"\ntype Calc struct {}\nfunc (c *Calc) Add(a int, b int) int {\n return a + b\n}\nfunc Helper() {}\n')
        go_az = CodebaseAnalyzer(go_repo)
        self.assertEqual(go_az.language, "go")
        gdata = go_az.scan()
        self.assertEqual(gdata["manifest"]["module_name"], "example.com/app")
        self.assertEqual(gdata["classes"][0]["name"], "Calc")
        self.assertIn("Add", gdata["classes"][0]["methods"])

        # Rust
        rs_repo = os.path.join(self.tmp, "rs_app")
        os.makedirs(os.path.join(rs_repo, "src"))
        with open(os.path.join(rs_repo, "Cargo.toml"), "w", encoding="utf-8") as f:
            f.write('[package]\nname = "rs_app"\nedition = "2021"\n')
        with open(os.path.join(rs_repo, "src", "lib.rs"), "w", encoding="utf-8") as f:
            f.write("pub struct Engine {}\npub fn compute(x: i32) -> i32 { x + 1 }\n")
        rs_az = CodebaseAnalyzer(rs_repo)
        self.assertEqual(rs_az.language, "rust")
        rdata = rs_az.scan()
        self.assertEqual(rdata["classes"][0]["name"], "Engine")
        self.assertEqual(rdata["functions"][0]["name"], "compute")

        # Java
        java_repo = os.path.join(self.tmp, "java_app")
        os.makedirs(os.path.join(java_repo, "src", "main", "java"))
        with open(os.path.join(java_repo, "pom.xml"), "w", encoding="utf-8") as f:
            f.write("<project></project>\n")
        with open(os.path.join(java_repo, "src", "main", "java", "Router.java"), "w", encoding="utf-8") as f:
            f.write("public class Router {\n    public String route(String path) { return path; }\n}\n")
        java_az = CodebaseAnalyzer(java_repo)
        self.assertEqual(java_az.language, "java")
        jdata = java_az.scan()
        self.assertEqual(jdata["classes"][0]["name"], "Router")
        self.assertIn("route", jdata["classes"][0]["methods"])

    def test_docker_sandbox_command_and_unavailable_guard(self):
        with patch("src.sandbox_docker.DockerSandboxRunner.is_available", return_value=False):
            with self.assertRaises(RuntimeError):
                SandboxVerifier(self.tmp, sandbox="docker")

        with patch("src.sandbox_docker.shutil.which", return_value="/usr/bin/docker"), \
             patch("src.sandbox_docker.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
            code, _, _ = DockerSandboxRunner.run_tests(self.tmp, PythonAdapter())
            self.assertEqual(code, 0)
            called_cmd = mock_run.call_args.args[0]
            self.assertIn("--network=none", called_cmd)
            self.assertIn("python:3.12-slim", called_cmd)


class PhaseDExporterAndRigorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    @unittest.skipUnless(_tool_available(sys.executable, "-m", "pytest", "--version"), "pytest required")
    def test_mutation_rigor_and_swebench_jsonl_export(self):
        repo = os.path.join(self.tmp, "repo")
        os.makedirs(os.path.join(repo, "tests"))
        with open(os.path.join(repo, "auth.py"), "w", encoding="utf-8") as f:
            f.write("def ping():\n    return 'pong'\n")
        with open(os.path.join(repo, "tests", "test_ping.py"), "w", encoding="utf-8") as f:
            f.write("from auth import ping\ndef test_p():\n    assert ping() == 'pong'\n")
        with open(os.path.join(repo, "pytest.ini"), "w", encoding="utf-8") as f:
            f.write("[pytest]\npythonpath = .\n")

        diff = (
            "--- a/auth.py\n+++ b/auth.py\n@@ -1,2 +1,5 @@\n"
            " def ping():\n     return 'pong'\n+\n"
            "+def is_active():\n+    return True\n"
        )
        test_code = "from auth import is_active\ndef test_active():\n    assert is_active() is True\n"

        verifier = SandboxVerifier(repo)
        res = verifier.verify(diff, test_code)
        self.assertTrue(res.is_valid)

        rigor = check_test_mutation_rigor(verifier, diff, test_code)
        self.assertTrue(rigor["checked"])
        self.assertTrue(rigor["mutant_killed"])

        tasks_dir = os.path.join(self.tmp, "tasks")
        TaskPackager(tasks_dir).package(
            task_id="task_rigor_001",
            repo_name="repo",
            category="new_feature",
            instruction_md="# Add is_active",
            solution_diff=diff,
            test_code=test_code,
            verification=res,
            repo_meta={"repo_url": "https://github.com/example/repo", "commit_hash": "abc1234"},
        )

        out_jsonl = os.path.join(self.tmp, "export", "swe.jsonl")
        exp = export_swebench_jsonl(tasks_dir, out_jsonl, verified_only=True)
        self.assertEqual(exp["exported_count"], 1)
        with open(out_jsonl, "r", encoding="utf-8") as f:
            rec = json.loads(f.readline())
        self.assertEqual(rec["instance_id"], "task_rigor_001")
        self.assertEqual(rec["base_commit"], "abc1234")
        self.assertEqual(rec["language"], "python")
        self.assertIn("tests.test_feature_patch::test_active", rec["FAIL_TO_PASS"])


if __name__ == "__main__":
    unittest.main()
