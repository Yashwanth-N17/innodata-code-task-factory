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
from unittest.mock import patch

from src.analyzer import CodebaseAnalyzer
from src.benchmark_evaluator import BenchmarkEvaluator
from src.config import Config, _generate_mock_jsoncpp_diff, _mock_llm_response
from src.languages import BaseLanguageAdapter, LanguageRegistry
from src.packager import TaskPackager
from src.repo_loader import extract_repo_slug, is_remote_repo_url, resolve_repository
from src.spec_generator import SpecGenerator
from src.verifier import SandboxVerifier
from src.languages.cpp import CppAdapter
from src.languages.python import PythonAdapter
from src.languages.typescript import TypeScriptAdapter


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


class LanguageDetectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, relative_path, content=""):
        path = os.path.join(self.tmp, relative_path)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as file:
            file.write(content)

    def test_detects_python_repository(self):
        self._write("pyproject.toml", "[project]\nname = 'fixture'\n")
        self._write("src/app.py", "def run():\n    return 1\n")

        adapter = LanguageRegistry.get_adapter(self.tmp)

        self.assertIsInstance(adapter, PythonAdapter)

    def test_detects_typescript_repository(self):
        self._write("package.json", '{"name":"fixture"}')
        self._write("tsconfig.json", '{"compilerOptions":{}}')
        self._write("src/index.ts", "export const value = 1;\n")

        adapter = LanguageRegistry.get_adapter(self.tmp)

        self.assertIsInstance(adapter, TypeScriptAdapter)

    def test_detects_cpp_repository(self):
        self._write("CMakeLists.txt", "project(fixture)\n")
        self._write("src/main.cpp", "int main() { return 0; }\n")

        adapter = LanguageRegistry.get_adapter(self.tmp)

        self.assertIsInstance(adapter, CppAdapter)

    def test_unsupported_repository_is_rejected_before_analysis(self):
        with self.assertRaisesRegex(ValueError, "Could not detect a supported language"):
            CodebaseAnalyzer(self.tmp)

    def test_ambiguous_detection_requires_explicit_language(self):
        with (
            patch.object(PythonAdapter, "detect_score", return_value=80),
            patch.object(TypeScriptAdapter, "detect_score", return_value=80),
        ):
            with self.assertRaisesRegex(ValueError, "Ambiguous language detection.*python, typescript"):
                LanguageRegistry.get_adapter(self.tmp)

    def test_explicit_language_override_works_without_detection(self):
        adapter = LanguageRegistry.get_adapter(self.tmp, language="ts")

        self.assertIsInstance(adapter, TypeScriptAdapter)


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
        v4_classic = os.path.join(repo, "packages", "zod", "src", "v4", "classic")
        v4_tests = os.path.join(repo, "packages", "zod", "src", "v4", "tests")
        os.makedirs(v4_core)
        os.makedirs(v4_classic)
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
        with open(os.path.join(v4_classic, "schemas.ts"), "w", encoding="utf-8") as f:
            f.write(
                "export class StringSchema {\n"
                "  trimValue = false;\n"
                "  minimum = 0;\n"
                "  trim() { this.trimValue = true; return this; }\n"
                "  min(length: number) { this.minimum = length; return this; }\n"
                "  safeParse(value: unknown) {\n"
                "    if (typeof value !== 'string') return { success: false };\n"
                "    const data = this.trimValue ? value.trim() : value;\n"
                "    return { success: data.length >= this.minimum, data };\n"
                "  }\n"
                "}\n"
                "export function string() { return new StringSchema(); }\n"
            )
        with open(os.path.join(v4_classic, "external.ts"), "w", encoding="utf-8") as f:
            f.write('export * from "./schemas.ts";\n')
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
                'import { expect, test } from "vitest";\n'
                'import { stringSchema } from "../core/schemas.ts";\n\n'
                'test("stringSchema accepts string", () => {\n'
                '  expect(stringSchema("abc")).toBe("abc");\n'
                '});\n'
            )
        return repo

    def _attach_local_vitest(self, repo: str) -> None:
        installed_node_modules = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "repos", "colinhacks__zod", "node_modules")
        )
        entries = (".bin", ".store", "vitest")
        if not all(os.path.isdir(os.path.join(installed_node_modules, entry)) for entry in entries):
            self.skipTest("The locally installed Zod Vitest dependency tree is unavailable.")
        for entry in entries:
            TypeScriptAdapter._create_directory_link(
                os.path.join(installed_node_modules, entry),
                os.path.join(repo, "node_modules", entry),
            )

    def _make_jsoncpp_direct_repo(self) -> str:
        repo = os.path.join(self.tmp, "jsoncpp_direct")
        inc_dir = os.path.join(repo, "include", "json")
        src_dir = os.path.join(repo, "src", "lib_json")
        os.makedirs(inc_dir)
        os.makedirs(src_dir)

        with open(os.path.join(inc_dir, "value.h"), "w", encoding="utf-8") as f:
            f.write(
                "#pragma once\n"
                "#include <string>\n"
                "#include <utility>\n"
                "namespace Json {\n"
                "class Value {\n"
                "public:\n"
                "    Value() = default;\n"
                "    explicit Value(std::string value) : m_value(std::move(value)), m_null(false) {}\n"
                "    std::string asString() const;\n"
                "    bool isNull() const { return m_null; }\n"
                "    bool isString() const { return !m_null; }\n"
                "    bool isArray() const { return false; }\n"
                "    bool isObject() const { return false; }\n"
                "    std::size_t size() const { return 0; }\n"
                "private:\n"
                "    std::string m_value;\n"
                "    bool m_null = true;\n"
                "};\n"
                "}\n"
            )
        with open(os.path.join(src_dir, "json_value.cpp"), "w", encoding="utf-8") as f:
            f.write(
                '#include "json/value.h"\n'
                "namespace Json {\n"
                "std::string Value::asString() const { return m_value; }\n"
                "}\n"
            )
        return repo

    def _make_jsoncpp_conditional_repo(self) -> str:
        repo = self._make_jsoncpp_direct_repo()
        header_path = os.path.join(repo, "include", "json", "value.h")
        with open(header_path, "r", encoding="utf-8") as f:
            header = f.read()
        header = header.replace(
            "private:\n",
            "#ifdef __NVCC__\n"
            "public:\n"
            "#else\n"
            "private:\n"
            "#endif\n",
            1,
        )
        with open(header_path, "w", encoding="utf-8") as f:
            f.write(header)
        return repo

    def _generate_mock_profile_task(self, repo: str):
        analyzer = CodebaseAnalyzer(repo)
        spec_context = analyzer.get_context_prompt()
        code_context = analyzer.get_targeted_context()
        spec = _mock_llm_response(
            prompt=(
                f"Target Codebase Context:\n{spec_context}\n\n"
                "Task Category Request: NEW_FEATURE"
            ),
            system_prompt="Feature 2 - SWE-bench Issue Specification Generator",
        )
        solution_diff = _mock_llm_response(
            prompt=(
                f"Codebase Context:\n{code_context}\n\n"
                f"Task Specification (instruction.md):\n{spec}\n"
                "Return ONLY the unified git diff patch."
            ),
            system_prompt="Feature 3 - Ground-Truth Reference Solution Coder",
        )
        test_code = _mock_llm_response(
            prompt=(
                f"Codebase Context:\n{code_context}\n\n"
                f"Task Specification (instruction.md):\n{spec}\n"
                "The tests MUST fail against the current unpatched codebase."
            ),
            system_prompt="Feature 4 - Hidden Acceptance Test Synthesizer",
        )
        self.assertTrue(SpecGenerator.validate_structure(spec)[0], spec)
        return spec, solution_diff, test_code

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
                "    bool isNull() const;\n"
                "    bool isString() const;\n"
                "    bool isArray() const;\n"
                "    bool isObject() const;\n"
                "    std::size_t size() const;\n"
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
                "bool Value::isNull() const { return m_value.empty(); }\n"
                "bool Value::isString() const { return !m_value.empty(); }\n"
                "bool Value::isArray() const { return false; }\n"
                "bool Value::isObject() const { return false; }\n"
                "std::size_t Value::size() const { return m_value.size(); }\n"
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

    def test_jsoncpp_cmake_variable_targets_resolve_and_static_is_linked_first(self):
        repo = os.path.join(self.tmp, "jsoncpp_cmake_targets")
        lib_dir = os.path.join(repo, "src", "lib_json")
        os.makedirs(lib_dir)
        with open(os.path.join(repo, "CMakeLists.txt"), "w", encoding="utf-8") as f:
            f.write("project(jsoncpp)\nadd_subdirectory(src)\n")
        with open(os.path.join(repo, "src", "CMakeLists.txt"), "w", encoding="utf-8") as f:
            f.write("add_subdirectory(lib_json)\n")
        with open(os.path.join(lib_dir, "CMakeLists.txt"), "w", encoding="utf-8") as f:
            f.write(
                "set(SHARED_LIB ${PROJECT_NAME}_lib)\n"
                "add_library(${SHARED_LIB} SHARED json_value.cpp)\n"
                "set(STATIC_LIB ${PROJECT_NAME}_static)\n"
                "add_library(${STATIC_LIB} STATIC json_value.cpp)\n"
                "add_library(JsonCpp::JsonCpp ALIAS ${STATIC_LIB})\n"
                "add_library(${PROJECT_NAME}_object OBJECT json_value.cpp)\n"
            )

        adapter = CppAdapter()
        manifest = adapter.inspect_manifest(repo)
        self.assertIn("jsoncpp_lib", manifest["cmake_targets"])
        self.assertIn("jsoncpp_static", manifest["cmake_targets"])
        self.assertIn("JsonCpp::JsonCpp", manifest["cmake_targets"])
        self.assertNotIn("jsoncpp_object", manifest["cmake_targets"])

        for stage in ("negative", "positive"):
            stage_dir = os.path.join(self.tmp, stage)
            shutil.copytree(repo, stage_dir)
            adapter._inject_cmake_test_target(stage_dir, manifest)
            with open(os.path.join(stage_dir, "CMakeLists.txt"), encoding="utf-8") as f:
                injected = f.read()
            target_loop = injected.split("foreach(_ftgt IN ITEMS ", 1)[1].split(")", 1)[0]
            self.assertEqual(target_loop.split()[0], "jsoncpp_static")
            self.assertIn(
                "target_link_libraries(factory_feature_test PRIVATE ${_ftgt})",
                injected,
            )

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
        self._attach_local_vitest(repo)
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
            'import { expect, test } from "vitest";\n'
            'import * as schemas from "../packages/zod/src/v4/core/schemas.ts";\n\n'
            'test("nonEmptyString rejects blank input", () => {\n'
            '  const nonEmptyString = Reflect.get(schemas, "nonEmptyString");\n'
            '  expect(typeof nonEmptyString).toBe("function");\n'
            '  expect(nonEmptyString("zod")).toBe("zod");\n'
            '  expect(() => nonEmptyString("   ")).toThrow(/blank/);\n'
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
        evaluator = BenchmarkEvaluator(task_dir=pkg_dir, target_repo_path=repo)
        with patch("src.benchmark_evaluator.call_llm", return_value=diff):
            benchmark_result = evaluator.evaluate_model(model_name="gpt-4o")
        self.assertTrue(benchmark_result["passed"], benchmark_result["logs"])

    @unittest.skipUnless(
        _tool_available("cmake", "--version") and _tool_available("ctest", "--version"),
        "cmake & ctest required",
    )
    def test_jsoncpp_cmake_3_step_verification_and_packaging(self):
        repo = self._make_jsoncpp_style_repo()
        diff = (
            "--- a/include/json/value.h\n"
            "+++ b/include/json/value.h\n"
            "@@ -6,10 +6,11 @@\n"
            '     explicit Value(std::string v = "");\n'
            "     std::string asString() const;\n"
            "     bool isNull() const;\n"
            "     bool isString() const;\n"
            "     bool isArray() const;\n"
            "     bool isObject() const;\n"
            "     std::size_t size() const;\n"
            "+    bool empty() const;\n"
            " private:\n"
            "     std::string m_value;\n"
            " };\n"
            "--- a/src/lib_json/json_value.cpp\n"
            "+++ b/src/lib_json/json_value.cpp\n"
            "@@ -2,9 +2,10 @@\n"
            " namespace Json {\n"
            " Value::Value(std::string v) : m_value(std::move(v)) {}\n"
            " std::string Value::asString() const { return m_value; }\n"
            " bool Value::isNull() const { return m_value.empty(); }\n"
            " bool Value::isString() const { return !m_value.empty(); }\n"
            " bool Value::isArray() const { return false; }\n"
            " bool Value::isObject() const { return false; }\n"
            " std::size_t Value::size() const { return m_value.size(); }\n"
            "+bool Value::empty() const { return m_value.empty(); }\n"
            " }\n"
        )
        test_code = (
            "#include <cassert>\n"
            "#include <type_traits>\n"
            "#include <utility>\n"
            '#include "json/value.h"\n\n'
            "template <typename T, typename = void>\n"
            "struct HasEmpty : std::false_type {};\n\n"
            "template <typename T>\n"
            "struct HasEmpty<T, std::void_t<decltype(std::declval<const T&>().empty())>> "
            ": std::true_type {};\n\n"
            "template <typename T>\n"
            "auto verify_empty(int) -> decltype(std::declval<const T&>().empty(), void()) {\n"
            "    T emptyVal;\n"
            '    T fullVal("data");\n'
            "    assert(emptyVal.empty() == true);\n"
            "    assert(fullVal.empty() == false);\n"
            "}\n\n"
            "template <typename T>\n"
            "void verify_empty(...) {\n"
            '    assert(false && "Value::empty() is not implemented");\n'
            "}\n\n"
            "int main() {\n"
            "    verify_empty<Json::Value>(0);\n"
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

    def _assert_mock_factory_cli_succeeds(self, target_repo: str, expected_lang: str, out_dir: str) -> None:
        factory_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
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
        self.assertIn("VERIFIED & VALID", res.stdout, f"Did not verify {expected_lang}:\n{res.stdout}")

    @unittest.skipUnless(_tool_available("node", "--version"), "node required")
    def test_cli_run_factory_mock_end_to_end_typescript(self):
        zod_repo = self._make_zod_style_repo()
        self._attach_local_vitest(zod_repo)
        self._assert_mock_factory_cli_succeeds(
            zod_repo,
            "typescript",
            os.path.join(self.tmp, "cli_tasks"),
        )

    @unittest.skipUnless(_tool_available("cmake", "--version"), "cmake required")
    def test_cli_run_factory_mock_end_to_end_cpp(self):
        jsoncpp_repo = self._make_jsoncpp_style_repo()
        self._assert_mock_factory_cli_succeeds(
            jsoncpp_repo,
            "cpp",
            os.path.join(self.tmp, "cli_tasks"),
        )

    def _assert_mock_profile_fails_then_passes(self, repo: str, language: str):
        spec, solution_diff, test_code = self._generate_mock_profile_task(repo)
        verifier = SandboxVerifier(repo, timeout=60, language=language)
        result = verifier.verify(solution_diff, test_code)

        self.assertEqual(
            result.step2_evidence.outcome.value,
            "FAIL",
            f"Generated test did not fail as a test on unpatched code: {result.logs}",
        )
        self.assertGreater(result.step2_evidence.executed_count, 0)
        self.assertEqual(result.step2_evidence.error_count, 0, result.logs)
        self.assertTrue(result.step2_evidence.failed_test_ids, result.step2_evidence.test_results)
        self.assertEqual(result.step3_evidence.outcome.value, "PASS", result.logs)
        self.assertTrue(result.is_valid, result.to_dict())
        return spec, solution_diff, test_code, result

    def test_mock_click_profile_is_repository_aware_and_verifies_fail_to_pass(self):
        repo = self._make_click_style_repo()
        spec, solution_diff, test_code, result = self._assert_mock_profile_fails_then_passes(
            repo, "python"
        )

        for generated in (spec, solution_diff, test_code):
            self.assertIn("get_parameter_names", generated)
        self.assertEqual(result.step1_outcome, "PASS")
        self.assertTrue(any("CommandParameterNames" in test_id for test_id in result.fail_to_pass))

    @unittest.skipUnless(_tool_available("node", "--version"), "node required")
    def test_mock_zod_profile_is_language_aware_and_verifies_fail_to_pass(self):
        repo = self._make_zod_style_repo()
        self._attach_local_vitest(repo)
        spec, solution_diff, test_code, result = self._assert_mock_profile_fails_then_passes(
            repo, "typescript"
        )

        for generated in (spec, solution_diff, test_code):
            self.assertIn("nonBlankString", generated)
        self.assertIn("string().trim().min(1)", solution_diff)
        self.assertEqual(result.step1_outcome, "PASS")

    @unittest.skipUnless(_tool_available("g++", "--version"), "g++ required")
    def test_mock_jsoncpp_profile_uses_compile_safe_fail_to_pass(self):
        repo = self._make_jsoncpp_direct_repo()
        spec, solution_diff, test_code, result = self._assert_mock_profile_fails_then_passes(
            repo, "cpp"
        )

        for generated in (spec, solution_diff, test_code):
            self.assertIn("isEmptyStringOrContainer", generated)
        self.assertIn("HasEmptyStringOrContainer", test_code)
        self.assertIn('#include "json/value.h"', test_code)
        self.assertEqual(result.step1_outcome, "NO_TESTS_FOUND")

    def test_jsoncpp_mock_method_is_inserted_before_conditional_access_section(self):
        repo = self._make_jsoncpp_conditional_repo()
        solution_diff = _generate_mock_jsoncpp_diff("include/json/value.h", repo)
        patch_path = os.path.join(self.tmp, "jsoncpp_public_api.diff")
        with open(patch_path, "w", encoding="utf-8") as f:
            f.write(solution_diff)
        subprocess.run(["git", "apply", patch_path], cwd=repo, check=True, capture_output=True)

        with open(os.path.join(repo, "include", "json", "value.h"), encoding="utf-8") as f:
            patched_header = f.read()
        method_position = patched_header.index("isEmptyStringOrContainer")
        conditional_position = patched_header.index("#ifdef __NVCC__")
        self.assertLess(method_position, conditional_position)

    @unittest.skipUnless(_tool_available("g++", "--version"), "g++ required")
    def test_jsoncpp_conditional_access_mock_proves_genuine_fail_to_pass(self):
        repo = self._make_jsoncpp_conditional_repo()
        spec, solution_diff, test_code, result = self._assert_mock_profile_fails_then_passes(
            repo, "cpp"
        )

        for generated in (spec, solution_diff, test_code):
            self.assertIn("isEmptyStringOrContainer", generated)
        self.assertNotIn("assert(", test_code)
        self.assertIn("return false;", test_code)
        self.assertEqual(result.step2_evidence.outcome.value, "FAIL")
        self.assertEqual(result.step2_evidence.error_count, 0)
        self.assertEqual(result.step3_evidence.outcome.value, "PASS")


if __name__ == "__main__":
    unittest.main()
