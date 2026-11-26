"""
End-to-End Polyglot Architecture & Repo Profile Tests.
Covers remote git resolution, pluggable LanguageRegistry, 2-tier deep repo analysis,
and real 3-step SandboxVerifier execution on:
  1. Python src/-layout repos (pallets/click profile)
  2. TypeScript monorepo workspaces (colinhacks/zod profile)
  3. C++ CMake + jsontest/CTest repos (open-source-parsers/jsoncpp profile)
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

from src.analyzer import CodebaseAnalyzer
from src.config import Config
from src.languages import BaseLanguageAdapter, LanguageRegistry
from src.packager import TaskPackager
from src.repo_loader import extract_repo_slug, is_remote_repo_url, resolve_repository
from src.spec_generator import SpecGenerator
from src.verifier import SandboxVerifier


def _tool_available(*cmd) -> bool:
    try:
        res = subprocess.run(list(cmd), capture_output=True, text=True)
        return res.returncode == 0
    except Exception:
        return False


class RepoLoaderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_url_detection_and_slugs(self):
        self.assertTrue(is_remote_repo_url("https://github.com/pallets/click"))
        self.assertTrue(is_remote_repo_url("git@github.com:colinhacks/zod.git"))
        self.assertFalse(is_remote_repo_url(self.tmp))

        self.assertEqual(extract_repo_slug("https://github.com/pallets/click"), "pallets__click")
        self.assertEqual(extract_repo_slug("https://github.com/colinhacks/zod.git"), "colinhacks__zod")
        self.assertEqual(
            extract_repo_slug("https://github.com/open-source-parsers/jsoncpp"),
            "open-source-parsers__jsoncpp",
        )

    def test_resolve_local_and_git_file_url_clone(self):
        # Local resolution
        resolved, meta = resolve_repository(self.tmp)
        self.assertEqual(resolved, os.path.abspath(self.tmp))
        self.assertEqual(meta["repo_name"], os.path.basename(self.tmp))

        # Local source git repo cloned via file:// URL (tests real git clone + cache)
        origin_repo = os.path.join(self.tmp, "origin_click")
        os.makedirs(origin_repo)
        with open(os.path.join(origin_repo, "app.py"), "w", encoding="utf-8") as f:
            f.write("def hello():\n    return 'click'\n")
        for cmd in (
            ["git", "init"],
            ["git", "config", "user.name", "Test"],
            ["git", "config", "user.email", "test@example.com"],
            ["git", "add", "."],
            ["git", "commit", "-m", "init"],
        ):
            subprocess.run(cmd, cwd=origin_repo, check=True, capture_output=True)

        cache_dir = os.path.join(self.tmp, "cache")
        cloned_path, cloned_meta = resolve_repository(f"file://{origin_repo}", cache_dir=cache_dir)
        self.assertTrue(os.path.isdir(os.path.join(cloned_path, ".git")))
        self.assertTrue(os.path.isfile(os.path.join(cloned_path, "app.py")))
        self.assertTrue(len(cloned_meta["commit_hash"]) >= 7)
        self.assertEqual(cloned_meta["repo_url"], f"file://{origin_repo}")

        # Second call reuses cache cleanly
        cloned_again, _ = resolve_repository(f"file://{origin_repo}", cache_dir=cache_dir)
        self.assertEqual(cloned_path, cloned_again)


class LanguageExtensibilityTests(unittest.TestCase):
    def test_custom_adapter_registration(self):
        class DummyGoAdapter(BaseLanguageAdapter):
            language_id = "go_custom_test"
            display_name = "Go"
            source_extensions = (".go",)
            test_extensions = ("_test.go",)
            test_framework = "go test"

            @classmethod
            def detect_score(cls, repo_path: str) -> int:
                return 100 if os.path.isfile(os.path.join(repo_path, "go.mod")) else 0

            def inspect_manifest(self, repo_path):
                return {"language": "go_custom_test"}

            def is_test_file(self, rel_path, full_path):
                return rel_path.endswith("_test.go")

            def scan_file(self, full_path, rel_path, summary):
                pass

            def run_tests(self, work_dir, target_test=None, timeout=45, **kwargs):
                return 0, "", {}

            def is_real_failure(self, code, out):
                return code != 0

            def get_environment_metadata(self, repo_path, manifest):
                return {"go_version": ">=1.21", "test_command": "go test ./..."}

        LanguageRegistry.register(DummyGoAdapter, aliases=["golang_test"])
        try:
            self.assertIn("go_custom_test", LanguageRegistry.list_languages())
            tmp = tempfile.mkdtemp()
            try:
                with open(os.path.join(tmp, "go.mod"), "w", encoding="utf-8") as f:
                    f.write("module example.com/demo\n")
                adapter = LanguageRegistry.get_adapter(tmp)
                self.assertEqual(adapter.language_id, "go_custom_test")
                explicit = LanguageRegistry.get_adapter(tmp, language="golang_test")
                self.assertEqual(explicit.language_id, "go_custom_test")
            finally:
                shutil.rmtree(tmp, ignore_errors=True)
        finally:
            LanguageRegistry._ADAPTERS.pop("go_custom_test", None)
            LanguageRegistry._ALIASES.pop("golang_test", None)


class PolyglotRepoProfilesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ---- Helper Repo Fixtures ----------------------------------------------

    def _make_click_style_repo(self) -> str:
        repo = os.path.join(self.tmp, "click_like")
        os.makedirs(os.path.join(repo, "src", "click"))
        os.makedirs(os.path.join(repo, "tests"))

        with open(os.path.join(repo, "pyproject.toml"), "w", encoding="utf-8") as f:
            f.write(
                '[build-system]\nrequires = ["flit_core"]\nbuild-backend = "flit_core.buildapi"\n\n'
                '[project]\nname = "click"\nrequires-python = ">=3.10"\n\n'
                '[tool.pytest.ini_options]\ntestpaths = ["tests"]\n'
                'filterwarnings = ["error"]\naddopts = "-m \'not stress\'"\n'
                'markers = ["stress: stress tests"]\n'
            )
        with open(os.path.join(repo, "src", "click", "__init__.py"), "w", encoding="utf-8") as f:
            f.write("from .core import Command\n")
        with open(os.path.join(repo, "src", "click", "core.py"), "w", encoding="utf-8") as f:
            f.write(
                "class BaseCommand:\n"
                "    def __init__(self, name: str):\n"
                "        self.name = name\n\n"
                "class Command(BaseCommand):\n"
                "    def invoke(self, ctx: str) -> str:\n"
                "        return f'{self.name}:{ctx}'\n"
            )
        with open(os.path.join(repo, "tests", "test_core.py"), "w", encoding="utf-8") as f:
            f.write(
                "from click.core import Command\n\n"
                "def test_invoke():\n"
                "    assert Command('run').invoke('ok') == 'run:ok'\n"
            )
        return repo

    def _make_zod_style_repo(self) -> str:
        repo = os.path.join(self.tmp, "zod_like")
        v4_core = os.path.join(repo, "packages", "zod", "src", "v4", "core")
        v4_tests = os.path.join(repo, "packages", "zod", "src", "v4", "tests")
        os.makedirs(v4_core)
        os.makedirs(v4_tests)
        os.makedirs(os.path.join(repo, "node_modules"))  # tests sandbox symlink

        with open(os.path.join(repo, "package.json"), "w", encoding="utf-8") as f:
            json.dump({
                "name": "zod-monorepo",
                "private": True,
                "packageManager": "nub@0.8.3",
                "workspaces": ["packages/*"],
                "scripts": {"test": "nub exec --node vitest run"},
                "devDependencies": {"vitest": "^3.0.0", "typescript": "^5.5.0"},
            }, f)
        with open(os.path.join(repo, "tsconfig.json"), "w", encoding="utf-8") as f:
            json.dump({"compilerOptions": {"strict": True, "target": "ES2022"}}, f)
        with open(os.path.join(repo, "vitest.config.ts"), "w", encoding="utf-8") as f:
            f.write("export default {};\n")
        with open(os.path.join(repo, "packages", "zod", "package.json"), "w", encoding="utf-8") as f:
            json.dump({"name": "zod", "main": "./src/index.ts"}, f)
        with open(os.path.join(repo, "packages", "zod", "src", "index.ts"), "w", encoding="utf-8") as f:
            f.write('export * from "./v4/core/schemas.ts";\n')
        with open(os.path.join(v4_core, "schemas.ts"), "w", encoding="utf-8") as f:
            f.write(
                "export class ZodBase {\n"
                "  #tag: string = 'zod';\n"
                "  parse(value: unknown): string {\n"
                "    return String(value);\n"
                "  }\n"
                "}\n\n"
                "export function stringSchema(val: unknown): string {\n"
                "  if (typeof val !== 'string') {\n"
                "    throw new Error('Expected string');\n"
                "  }\n"
                "  return val;\n"
                "}\n"
            )
        with open(os.path.join(v4_tests, "basic.test.ts"), "w", encoding="utf-8") as f:
            f.write(
                'import test from "node:test";\n'
                'import assert from "node:assert/strict";\n'
                'import { stringSchema } from "../core/schemas.ts";\n\n'
                'test("stringSchema accepts string", () => {\n'
                '  assert.equal(stringSchema("abc"), "abc");\n'
                '});\n'
            )
        return repo

    def _make_jsoncpp_style_repo(self) -> str:
        repo = os.path.join(self.tmp, "jsoncpp_like")
        inc_dir = os.path.join(repo, "include", "json")
        lib_dir = os.path.join(repo, "src", "lib_json")
        tst_dir = os.path.join(repo, "src", "test_lib_json")
        dev_dir = os.path.join(repo, "devtools")
        os.makedirs(inc_dir)
        os.makedirs(lib_dir)
        os.makedirs(tst_dir)
        os.makedirs(dev_dir)

        # Include a distracting python helper script (jsoncpp has devtools/*.py)
        with open(os.path.join(dev_dir, "amalgamate.py"), "w", encoding="utf-8") as f:
            f.write("def amalgamate():\n    return True\n")

        with open(os.path.join(repo, "CMakeLists.txt"), "w", encoding="utf-8") as f:
            f.write(
                "cmake_minimum_required(VERSION 3.10)\n"
                "project(jsoncpp LANGUAGES CXX)\n"
                "set(CMAKE_CXX_STANDARD 17)\n"
                'option(JSONCPP_WITH_TESTS "Build and run jsoncpp tests" ON)\n'
                "enable_testing()\n"
                "add_subdirectory(src)\n"
            )
        with open(os.path.join(repo, "src", "CMakeLists.txt"), "w", encoding="utf-8") as f:
            f.write(
                "add_subdirectory(lib_json)\n"
                "if(JSONCPP_WITH_TESTS)\n"
                "  add_subdirectory(test_lib_json)\n"
                "endif()\n"
            )
        with open(os.path.join(lib_dir, "CMakeLists.txt"), "w", encoding="utf-8") as f:
            f.write(
                "add_library(jsoncpp_lib STATIC json_value.cpp)\n"
                "target_include_directories(jsoncpp_lib PUBLIC ${CMAKE_SOURCE_DIR}/include)\n"
            )
        with open(os.path.join(tst_dir, "CMakeLists.txt"), "w", encoding="utf-8") as f:
            f.write(
                "add_executable(jsoncpp_test main.cpp)\n"
                "target_link_libraries(jsoncpp_test PRIVATE jsoncpp_lib)\n"
                "add_test(NAME jsoncpp_test COMMAND jsoncpp_test)\n"
            )
        with open(os.path.join(inc_dir, "value.h"), "w", encoding="utf-8") as f:
            f.write(
                "#pragma once\n"
                "#include <string>\n"
                "namespace Json {\n"
                "class Value {\n"
                "public:\n"
                "    explicit Value(std::string v = \"\");\n"
                "    std::string asString() const;\n"
                "private:\n"
                "    std::string m_value;\n"
                "};\n"
                "}\n"
            )
        with open(os.path.join(lib_dir, "json_value.cpp"), "w", encoding="utf-8") as f:
            f.write(
                '#include "json/value.h"\n'
                "namespace Json {\n"
                "Value::Value(std::string v) : m_value(std::move(v)) {}\n"
                "std::string Value::asString() const { return m_value; }\n"
                "}\n"
            )
        with open(os.path.join(tst_dir, "jsontest.h"), "w", encoding="utf-8") as f:
            f.write(
                "#pragma once\n"
                "#include <cassert>\n"
                "#define JSONTEST_ASSERT(expr) assert(expr)\n"
            )
        with open(os.path.join(tst_dir, "main.cpp"), "w", encoding="utf-8") as f:
            f.write(
                '#include "jsontest.h"\n'
                '#include "json/value.h"\n'
                "int main() {\n"
                '    Json::Value v("hello");\n'
                '    JSONTEST_ASSERT(v.asString() == "hello");\n'
                "    return 0;\n"
                "}\n"
            )
        return repo

    # ---- Scanner & Auto-Detection Tests ------------------------------------

    def test_scan_click_zod_and_jsoncpp_profiles(self):
        click_repo = self._make_click_style_repo()
        zod_repo = self._make_zod_style_repo()
        jsoncpp_repo = self._make_jsoncpp_style_repo()

        # 1. Click (Python src/ layout)
        click_az = CodebaseAnalyzer(click_repo)
        self.assertEqual(click_az.language, "python")
        c_data = click_az.scan()
        self.assertTrue(c_data["manifest"]["src_layout"])
        self.assertEqual(c_data["manifest"]["requires_python"], ">=3.10")
        self.assertIn(os.path.join("src", "click", "core.py"), c_data["files"])
        c_tier2 = click_az.get_targeted_context()
        self.assertIn("Target Source Files", c_tier2)
        self.assertIn("class Command(BaseCommand):", c_tier2)

        # 2. Zod (TypeScript monorepo with workspaces)
        zod_az = CodebaseAnalyzer(zod_repo)
        self.assertEqual(zod_az.language, "typescript")
        z_data = zod_az.scan()
        self.assertEqual(z_data["manifest"]["test_runner"], "vitest")
        self.assertEqual(len(z_data["manifest"]["workspace_packages"]), 1)
        self.assertEqual(z_data["manifest"]["workspace_packages"][0]["name"], "zod")
        self.assertTrue(any("schemas.ts" in f for f in z_data["files"]))
        self.assertTrue(any("basic.test.ts" in t for t in z_data["existing_tests"]))
        z_cls = [c for c in z_data["classes"] if c["name"] == "ZodBase"][0]
        self.assertTrue(any("parse(" in m for m in z_cls["public_methods"]))
        z_tier2 = zod_az.get_targeted_context()
        self.assertIn("stringSchema", z_tier2)

        # 3. JsonCpp (C++ CMake + paired .h/.cpp + jsontest.h, ignoring devtools/*.py)
        cpp_az = CodebaseAnalyzer(jsoncpp_repo)
        self.assertEqual(cpp_az.language, "cpp")
        j_data = cpp_az.scan()
        self.assertEqual(j_data["manifest"]["build_system"], "cmake")
        self.assertTrue(j_data["manifest"]["jsontest_detected"])
        self.assertIn("jsoncpp_lib", j_data["manifest"]["cmake_targets"])
        self.assertIn("JSONCPP_WITH_TESTS", j_data["manifest"]["test_options"])
        j_cls = [c for c in j_data["classes"] if c["name"] == "Json::Value"][0]
        self.assertTrue(any("asString()" in m for m in j_cls["public_methods"]))
        j_tier2 = cpp_az.get_targeted_context()
        # Paired header + cpp implementation should both appear in Tier 2 context
        self.assertIn("value.h", j_tier2)
        self.assertIn("json_value.cpp", j_tier2)

    # ---- Polyglot Spec Leakage Sanitizer -----------------------------------

    def test_polyglot_leakage_auditor(self):
        bad = (
            "Update packages/zod/src/v4/core/schemas.ts:42 and src/lib_json/json_value.cpp:118. "
            "Inspect this.#tag and this->m_value around line 19."
        )
        ok, warns = SpecGenerator.validate_leakage(bad)
        self.assertFalse(ok)
        self.assertGreater(len(warns), 0)
        cleaned = SpecGenerator.sanitize_spec(bad)
        self.assertNotIn(":42", cleaned)
        self.assertNotIn(":118", cleaned)
        self.assertNotIn("this.#tag", cleaned)
        self.assertNotIn("m_value", cleaned)
        ok_after, rem = SpecGenerator.validate_leakage(cleaned)
        self.assertTrue(ok_after, f"Residual leakage: {rem}")

    # ---- Live 3-Step Matrix Verification Across All 3 Languages ------------

    @unittest.skipUnless(_tool_available(sys.executable, "-m", "pytest", "--version"), "pytest required")
    def test_click_src_layout_3_step_verification_and_packaging(self):
        repo = self._make_click_style_repo()
        diff = (
            "--- a/src/click/core.py\n"
            "+++ b/src/click/core.py\n"
            "@@ -5,3 +5,6 @@\n"
            " class Command(BaseCommand):\n"
            "     def invoke(self, ctx: str) -> str:\n"
            "         return f'{self.name}:{ctx}'\n"
            "+\n"
            "+    def has_help(self) -> bool:\n"
            "+        return True\n"
        )
        test_code = (
            "from click.core import Command\n\n"
            "def test_has_help():\n"
            "    assert Command('run').has_help() is True\n"
        )
        verifier = SandboxVerifier(repo)
        res = verifier.verify(diff, test_code)
        self.assertTrue(res.is_valid, f"Click verification failed: {res.logs}")
        self.assertIn("tests.test_feature_patch::test_has_help", res.fail_to_pass)
        self.assertIn("tests.test_core::test_invoke", res.pass_to_pass)

        out_dir = os.path.join(self.tmp, "tasks")
        pkg_dir = TaskPackager(out_dir).package(
            task_id="task_click_001",
            repo_name="click",
            category="new_feature",
            instruction_md="# Add has_help",
            solution_diff=diff,
            test_code=test_code,
            verification=res,
            language_adapter=verifier.adapter,
            manifest=CodebaseAnalyzer(repo).scan()["manifest"],
        )
        with open(os.path.join(pkg_dir, "task.toml"), encoding="utf-8") as f:
            toml_text = f.read()
        self.assertIn('language = "python"', toml_text)
        self.assertIn('pythonpath = "src"', toml_text)
        self.assertTrue(os.path.isfile(os.path.join(pkg_dir, "tests", "test_feature_patch.py")))

    @unittest.skipUnless(_tool_available("node", "--version"), "node required")
    def test_zod_typescript_3_step_verification_and_packaging(self):
        repo = self._make_zod_style_repo()
        diff = (
            "--- a/packages/zod/src/v4/core/schemas.ts\n"
            "+++ b/packages/zod/src/v4/core/schemas.ts\n"
            "@@ -10,3 +10,9 @@\n"
            "     throw new Error('Expected string');\n"
            "   }\n"
            "   return val;\n"
            " }\n"
            "+\n"
            "+export function nonEmptyString(val: unknown): string {\n"
            "+  const s = stringSchema(val);\n"
            "+  if (!s.trim()) throw new Error('String cannot be blank');\n"
            "+  return s;\n"
            "+}\n"
        )
        test_code = (
            'import test from "node:test";\n'
            'import assert from "node:assert/strict";\n'
            'import { nonEmptyString } from "../packages/zod/src/v4/core/schemas.ts";\n\n'
            'test("nonEmptyString rejects blank input", () => {\n'
            '  assert.equal(nonEmptyString("zod"), "zod");\n'
            '  assert.throws(() => nonEmptyString("   "), /blank/);\n'
            '});\n'
        )
        verifier = SandboxVerifier(repo)
        self.assertEqual(verifier.adapter.language_id, "typescript")
        res = verifier.verify(diff, test_code)
        self.assertTrue(res.is_valid, f"Zod TS verification failed: {res.logs}")
        self.assertTrue(any("test_feature_patch" in t for t in res.fail_to_pass))
        self.assertTrue(any("stringSchema accepts string" in t for t in res.pass_to_pass))

        out_dir = os.path.join(self.tmp, "tasks")
        pkg_dir = TaskPackager(out_dir).package(
            task_id="task_zod_001",
            repo_name="zod",
            category="validation_edge_cases",
            instruction_md="# Add nonEmptyString",
            solution_diff=diff,
            test_code=test_code,
            verification=res,
            language_adapter=verifier.adapter,
            manifest=CodebaseAnalyzer(repo).scan()["manifest"],
        )
        with open(os.path.join(pkg_dir, "task.toml"), encoding="utf-8") as f:
            toml_text = f.read()
        self.assertIn('language = "typescript"', toml_text)
        self.assertTrue(os.path.isfile(os.path.join(pkg_dir, "tests", "test_feature_patch.test.ts")))

    @unittest.skipUnless(
        _tool_available("cmake", "--version") and _tool_available("ctest", "--version"),
        "cmake & ctest required",
    )
    def test_jsoncpp_cmake_3_step_verification_and_packaging(self):
        repo = self._make_jsoncpp_style_repo()
        diff = (
            "--- a/include/json/value.h\n"
            "+++ b/include/json/value.h\n"
            "@@ -6,6 +6,7 @@\n"
            '     explicit Value(std::string v = "");\n'
            "     std::string asString() const;\n"
            "+    bool empty() const;\n"
            " private:\n"
            "     std::string m_value;\n"
            " };\n"
            "--- a/src/lib_json/json_value.cpp\n"
            "+++ b/src/lib_json/json_value.cpp\n"
            "@@ -2,4 +2,5 @@\n"
            " namespace Json {\n"
            " Value::Value(std::string v) : m_value(std::move(v)) {}\n"
            " std::string Value::asString() const { return m_value; }\n"
            "+bool Value::empty() const { return m_value.empty(); }\n"
            " }\n"
        )
        test_code = (
            "#include <cassert>\n"
            '#include "json/value.h"\n\n'
            "int main() {\n"
            "    Json::Value emptyVal;\n"
            '    Json::Value fullVal("data");\n'
            "    assert(emptyVal.empty() == true);\n"
            "    assert(fullVal.empty() == false);\n"
            "    return 0;\n"
            "}\n"
        )
        verifier = SandboxVerifier(repo, timeout=90)
        self.assertEqual(verifier.adapter.language_id, "cpp")
        res = verifier.verify(diff, test_code)
        self.assertTrue(res.is_valid, f"JsonCpp verification failed: {res.logs}")
        self.assertTrue(any("factory_feature_test" in t for t in res.fail_to_pass))
        self.assertTrue(any("jsoncpp_test" in t for t in res.pass_to_pass))

        out_dir = os.path.join(self.tmp, "tasks")
        pkg_dir = TaskPackager(out_dir).package(
            task_id="task_jsoncpp_001",
            repo_name="jsoncpp",
            category="new_feature",
            instruction_md="# Add Json::Value::empty()",
            solution_diff=diff,
            test_code=test_code,
            verification=res,
            language_adapter=verifier.adapter,
            manifest=CodebaseAnalyzer(repo).scan()["manifest"],
        )
        with open(os.path.join(pkg_dir, "task.toml"), encoding="utf-8") as f:
            toml_text = f.read()
        self.assertIn('language = "cpp"', toml_text)
        self.assertIn('cpp_standard = "c++17"', toml_text)
        self.assertTrue(os.path.isfile(os.path.join(pkg_dir, "tests", "test_feature_patch.cpp")))

    @unittest.skipUnless(
        _tool_available("node", "--version") and _tool_available("cmake", "--version"),
        "node and cmake required",
    )
    def test_cli_run_factory_mock_end_to_end_ts_and_cpp(self):
        zod_repo = self._make_zod_style_repo()
        jsoncpp_repo = self._make_jsoncpp_style_repo()
        out_dir = os.path.join(self.tmp, "cli_tasks")

        factory_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        for target_repo, expected_lang in [(zod_repo, "typescript"), (jsoncpp_repo, "cpp")]:
            res = subprocess.run(
                [
                    sys.executable, "run_factory.py",
                    "--repo", target_repo,
                    "--output", out_dir,
                    "--count", "1",
                    "--mock",
                ],
                cwd=factory_root,
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
            self.assertEqual(res.returncode, 0, f"CLI failed for {expected_lang}:\n{res.stdout}\n{res.stderr}")
            self.assertIn("✅ VERIFIED & VALID", res.stdout, f"Did not verify {expected_lang}:\n{res.stdout}")


if __name__ == "__main__":
    unittest.main()
