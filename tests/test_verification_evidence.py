import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from src.languages import (
    CppAdapter,
    GoAdapter,
    JavaAdapter,
    PythonAdapter,
    RustAdapter,
    TestResultMap as ResultMap,
    TestRunEvidence as RunEvidence,
    TestRunOutcome as RunOutcome,
    TypeScriptAdapter,
    parse_junit_xml,
)
from src.languages.base import SandboxPreparationError
from src.verifier import SandboxVerifier, VerificationResult


_PATCH = """--- a/subject.txt
+++ b/subject.txt
@@ -1 +1 @@
-before
+after
"""

_ADAPTER_CASES = (
    (PythonAdapter, "tests.test_feature_patch::test_generated", "no tests ran"),
    (TypeScriptAdapter, "tests/test_feature_patch.test.ts::test generated", "No existing TypeScript/JavaScript test suite found."),
    (CppAdapter, "factory_feature_test::run", "No existing C++ test suite found."),
    (GoAdapter, "example.com/fixture::TestFeaturePatch", "[no test files]"),
    (RustAdapter, "tests/test_feature_patch.rs::test_generated", "running 0 tests"),
    (JavaAdapter, "FeaturePatchTest::testGenerated", "No tests to run."),
)


def _result_map(results=None, discovered_count=None):
    result = ResultMap()
    result.update(results or {})
    result.discovered_count = (
        len(result) if discovered_count is None else discovered_count
    )
    result.report_present = bool(result)
    return result


class AdapterEvidenceTests(unittest.TestCase):
    def test_all_adapters_distinguish_empty_passing_and_failing_runs(self):
        for adapter_type, _, no_tests_output in _ADAPTER_CASES:
            with self.subTest(adapter=adapter_type.language_id):
                adapter = adapter_type()
                no_tests = _result_map(discovered_count=0)
                if adapter.language_id == "typescript":
                    no_tests.no_tests_found = True
                if adapter.language_id == "python":
                    no_tests_exit = 5
                else:
                    no_tests_exit = 0
                no_tests_evidence = adapter.evidence_from_test_run(
                    no_tests_exit, no_tests_output, no_tests
                )
                self.assertEqual(no_tests_evidence.outcome, RunOutcome.NO_TESTS_FOUND)
                self.assertEqual(no_tests_evidence.discovered_count, 0)
                self.assertEqual(no_tests_evidence.executed_count, 0)

                passed = adapter.evidence_from_test_run(
                    0, "native test runner completed",
                    _result_map({"existing::test_ok": "passed"}),
                )
                self.assertEqual(passed.outcome, RunOutcome.PASS)
                self.assertEqual(passed.discovered_count, 1)
                self.assertEqual(passed.executed_count, 1)
                self.assertEqual(passed.passed_count, 1)

                failed = adapter.evidence_from_test_run(
                    1, "native test runner reported a failed assertion",
                    _result_map({"existing::test_bad": "failed"}),
                )
                self.assertEqual(failed.outcome, RunOutcome.FAIL)
                self.assertEqual(failed.failed_count, 1)
                self.assertEqual(failed.failed_test_ids, ["existing::test_bad"])

    def test_empty_results_without_explicit_discovery_are_not_no_tests(self):
        for adapter_type, _, _ in _ADAPTER_CASES:
            with self.subTest(adapter=adapter_type.language_id):
                evidence = adapter_type().evidence_from_test_run(0, "", {})
                self.assertEqual(evidence.outcome, RunOutcome.ERROR)

    def test_runner_errors_collection_errors_and_timeouts_are_not_red_results(self):
        for adapter_type, _, _ in _ADAPTER_CASES:
            adapter = adapter_type()
            for code, diagnostics in (
                (-1, "test runner is not installed"),
                (-1, "test execution timed out"),
                (2, "collection error: test module could not be imported"),
            ):
                with self.subTest(adapter=adapter.language_id, diagnostics=diagnostics):
                    evidence = adapter.evidence_from_test_run(code, diagnostics, {})
                    self.assertEqual(evidence.outcome, RunOutcome.ERROR)
                    self.assertEqual(evidence.executed_count, 0)

    def test_malformed_and_missing_junit_reports_are_errors(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            malformed_path = os.path.join(temp_dir, "TEST-malformed.xml")
            with open(malformed_path, "w", encoding="utf-8") as report:
                report.write("<testsuite><testcase")
            malformed = parse_junit_xml(malformed_path)
            self.assertTrue(malformed.report_error)
            self.assertEqual(
                PythonAdapter().evidence_from_test_run(0, "", malformed).outcome,
                RunOutcome.ERROR,
            )

            empty_path = os.path.join(temp_dir, "TEST-empty.xml")
            with open(empty_path, "wb"):
                pass
            empty = parse_junit_xml(empty_path)
            self.assertEqual(empty.report_error, "JUnit XML report is empty.")
            self.assertEqual(
                PythonAdapter().evidence_from_test_run(0, "", empty).outcome,
                RunOutcome.ERROR,
            )

        missing = parse_junit_xml(os.path.join(tempfile.gettempdir(), "missing-test-report.xml"))
        self.assertIn("missing", missing.report_error.lower())
        self.assertEqual(
            PythonAdapter().evidence_from_test_run(0, "", missing).outcome,
            RunOutcome.ERROR,
        )

    def test_vitest_junit_preserves_nested_and_unnamed_testcases_with_counts(self):
        source = "packages/zod/src/v3/tests/language-server.test.ts"
        with tempfile.TemporaryDirectory() as temp_dir:
            report_path = os.path.join(temp_dir, "vitest.xml")
            with open(report_path, "w", encoding="utf-8") as report:
                report.write(
                    '<testsuites name="vitest tests" tests="3" failures="1">'
                    f'<testsuite name="{source}" tests="2">'
                    f'<testcase classname="{source}" name="named test"/>'
                    f'<testcase classname="{source}" name=""/>'
                    "</testsuite>"
                    f'<testsuite name="{source}" tests="1">'
                    f'<testcase classname="{source}" name=""><failure/></testcase>'
                    "</testsuite></testsuites>"
                )
            parsed = parse_junit_xml(
                report_path,
                vitest_compatibility=True,
            )

        self.assertIsNone(parsed.report_error)
        self.assertEqual(parsed.discovered_count, 3)
        self.assertEqual(len(parsed), 3)
        self.assertEqual(parsed[f"{source}::named test"], "passed")
        self.assertEqual(parsed[f"{source}::[unnamed testcase 1]"], "passed")
        self.assertEqual(parsed[f"{source}::[unnamed testcase 2]"], "failed")
        self.assertEqual(len(parsed.report_warnings), 2)

        evidence = TypeScriptAdapter().evidence_from_test_run(1, "", parsed)
        self.assertEqual(evidence.outcome, RunOutcome.FAIL)
        self.assertEqual(evidence.discovered_count, 3)
        self.assertEqual(evidence.failed_count, 1)

    def test_vitest_junit_preserves_repeated_testcases_without_losing_counts(self):
        source = "packages/zod/src/v3/tests/repeated.test.ts"
        with tempfile.TemporaryDirectory() as temp_dir:
            report_path = os.path.join(temp_dir, "vitest-duplicates.xml")
            with open(report_path, "w", encoding="utf-8") as report:
                report.write(
                    '<testsuites tests="3"><testsuite name="project">'
                    f'<testcase classname="{source}" name="same test"/>'
                    f'<testcase classname="{source}" name="same test"><failure/></testcase>'
                    f'<testcase classname="{source}" name="same test"/>'
                    "</testsuite></testsuites>"
                )
            strict = parse_junit_xml(report_path)
            parsed = parse_junit_xml(
                report_path,
                vitest_compatibility=True,
            )

        self.assertIn("duplicate test identity", strict.report_error)
        self.assertEqual(len(strict), 1)
        self.assertIsNone(parsed.report_error)
        self.assertEqual(parsed.discovered_count, 3)
        self.assertEqual(len(parsed), 3)
        self.assertEqual(parsed[f"{source}::same test"], "passed")
        self.assertEqual(
            parsed[f"{source}::same test [duplicate testcase 2]"],
            "failed",
        )
        self.assertEqual(
            parsed[f"{source}::same test [duplicate testcase 3]"],
            "passed",
        )
        self.assertEqual(len(parsed.report_warnings), 1)

    def test_junit_requires_nonblank_testcase_names_and_preserves_valid_ids(self):
        adapter = JavaAdapter()
        with tempfile.TemporaryDirectory() as temp_dir:
            reports = {
                "empty": '<testcase classname="FeaturePatchTest" name=""/>',
                "whitespace": '<testcase classname="FeaturePatchTest" name="   "/>',
                "missing": '<testcase classname="FeaturePatchTest"/>',
                "name_only": '<testcase name="testGenerated[param=1]"/>',
                "named": (
                    '<testcase classname="FeaturePatchTest" '
                    'name="testGenerated[param=1]"/>'
                ),
                "failed": (
                    '<testcase classname="FeaturePatchTest" '
                    'name="testGenerated[param=1]"><failure/></testcase>'
                ),
            }
            parsed = {}
            for label, testcase in reports.items():
                report_path = os.path.join(temp_dir, f"{label}.xml")
                with open(report_path, "w", encoding="utf-8") as report:
                    report.write(f"<testsuite tests=\"1\">{testcase}</testsuite>")
                parsed[label] = parse_junit_xml(report_path)

        for label in ("empty", "whitespace", "missing"):
            with self.subTest(report=label):
                self.assertTrue(parsed[label].report_error)
                self.assertFalse(parsed[label])
                self.assertEqual(
                    adapter.evidence_from_test_run(0, "", parsed[label]).outcome,
                    RunOutcome.ERROR,
                )

        valid_id = "FeaturePatchTest::testGenerated[param=1]"
        self.assertEqual(parsed["name_only"], {"testGenerated[param=1]": "passed"})
        self.assertEqual(parsed["named"], {valid_id: "passed"})
        self.assertEqual(parsed["failed"], {valid_id: "failed"})
        failed_evidence = adapter.evidence_from_test_run(1, "", parsed["failed"])
        passed_evidence = adapter.evidence_from_test_run(0, "", parsed["named"])
        self.assertEqual(failed_evidence.outcome, RunOutcome.FAIL)
        self.assertEqual(passed_evidence.outcome, RunOutcome.PASS)
        self.assertEqual(failed_evidence.failed_test_ids, [valid_id])
        self.assertEqual(passed_evidence.passed_test_ids, [valid_id])

    def test_python_junit_preserves_distinct_whitespace_parameter_ids(self):
        test_ids = (
            "test_boolean_flag_envvar[SHOUT-       -False]",
            "test_boolean_flag_envvar[SHOUT- -False]",
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            report_path = os.path.join(temp_dir, "pytest.xml")
            with open(report_path, "w", encoding="utf-8") as report:
                report.write(
                    '<testsuite tests="2">'
                    '<testcase classname="tests.test_options" '
                    'name="test_boolean_flag_envvar[SHOUT-       -False]"/>'
                    '<testcase classname="tests.test_options" '
                    'name="test_boolean_flag_envvar[SHOUT- -False]"/>'
                    "</testsuite>"
                )
            parsed = parse_junit_xml(report_path)

        evidence = PythonAdapter().evidence_from_test_run(0, "", parsed)
        expected_ids = {
            f"tests.test_options::{test_id}" for test_id in test_ids
        }
        self.assertEqual(evidence.outcome, RunOutcome.PASS)
        self.assertEqual(evidence.discovered_count, 2)
        self.assertEqual(evidence.executed_count, 2)
        self.assertEqual(set(evidence.test_results), expected_ids)

    def test_class_only_junit_identity_cannot_satisfy_fail_to_pass(self):
        adapter = JavaAdapter()
        with tempfile.TemporaryDirectory() as repo, tempfile.TemporaryDirectory() as reports:
            with open(os.path.join(repo, "subject.txt"), "w", encoding="utf-8") as source:
                source.write("before\n")

            class_only_path = os.path.join(reports, "class-only.xml")
            with open(class_only_path, "w", encoding="utf-8") as report:
                report.write(
                    '<testsuite tests="1"><testcase classname="FeaturePatchTest" '
                    'name=""><failure/></testcase></testsuite>'
                )
            class_only = parse_junit_xml(class_only_path)

            valid_path = os.path.join(reports, "valid.xml")
            with open(valid_path, "w", encoding="utf-8") as report:
                report.write(
                    '<testsuite tests="1"><testcase classname="FeaturePatchTest" '
                    'name="testGenerated"/></testsuite>'
                )
            valid = parse_junit_xml(valid_path)

            def fake_run_tests(work_dir, target_test=None, **kwargs):
                step = os.path.basename(work_dir)
                if step == "step1_baseline":
                    empty = _result_map(discovered_count=0)
                    return 0, "No tests to run.", empty
                if step == "step2_negative":
                    return 1, "generated test report", class_only
                return 0, "generated test report", valid

            with patch.object(adapter, "run_tests", side_effect=fake_run_tests):
                result = SandboxVerifier(repo, adapter=adapter).verify(
                    _PATCH, "public class FeaturePatchTest {}"
                )

        self.assertEqual(result.step2_evidence.outcome, RunOutcome.ERROR)
        self.assertEqual(result.step2_failed_test_ids, [])
        self.assertFalse(result.step2_negative_fail)
        self.assertFalse(result.is_valid)

    def test_cpp_compile_failure_is_an_error_not_a_failed_test(self):
        evidence = CppAdapter().evidence_from_test_run(
            1, "compiler error",
            _result_map({"factory_feature_test::compile": "failed"}),
        )
        self.assertEqual(evidence.outcome, RunOutcome.ERROR)
        self.assertEqual(evidence.executed_count, 0)
        self.assertEqual(evidence.error_count, 1)

    def test_skipped_discovered_tests_are_not_mistaken_for_a_passing_run(self):
        evidence = GoAdapter().evidence_from_test_run(
            0, "ok",
            _result_map({"example.com/fixture::TestSkipped": "skipped"}),
        )
        self.assertEqual(evidence.outcome, RunOutcome.ERROR)
        self.assertEqual(evidence.discovered_count, 1)
        self.assertEqual(evidence.executed_count, 0)
        self.assertIn("All discovered tests were skipped", evidence.diagnostics)


class NativeRunnerParserTests(unittest.TestCase):
    def test_python_finds_root_level_tests_and_parses_pytest_report(self):
        with tempfile.TemporaryDirectory() as repo:
            with open(os.path.join(repo, "test_root.py"), "w", encoding="utf-8") as test_file:
                test_file.write("def test_root_level():\n    assert True\n")
            code, diagnostics, results = PythonAdapter().run_tests(
                repo, python_executable=sys.executable
            )
        evidence = PythonAdapter().evidence_from_test_run(code, diagnostics, results)
        self.assertEqual(evidence.outcome, RunOutcome.PASS)
        self.assertEqual(evidence.discovered_count, 1)
        self.assertTrue(any("test_root_level" in test_id for test_id in evidence.passed_test_ids))

    def test_python_collection_error_is_not_a_valid_red_test(self):
        adapter = PythonAdapter()
        with tempfile.TemporaryDirectory() as repo:
            test_path = os.path.join(repo, "tests", "test_feature_patch.py")
            os.makedirs(os.path.dirname(test_path))
            with open(test_path, "w", encoding="utf-8") as test_file:
                test_file.write("from missing_package import missing_symbol\n")

            code, diagnostics, results = adapter.run_tests(
                repo, target_test=test_path, python_executable=sys.executable
            )

        evidence = adapter.evidence_from_test_run(code, diagnostics, results)
        self.assertEqual(evidence.outcome, RunOutcome.ERROR)
        self.assertEqual(evidence.failed_count, 0)
        self.assertEqual(evidence.error_count, 1)
        self.assertEqual(evidence.executed_count, 0)
        self.assertIn("ERROR collecting", diagnostics)

    def test_typescript_node_junit_output_is_parsed(self):
        adapter = TypeScriptAdapter()
        with tempfile.TemporaryDirectory() as repo:
            test_path = os.path.join(repo, "tests", "test_feature_patch.test.ts")
            os.makedirs(os.path.dirname(test_path))

            def fake_run(command, **kwargs):
                report_path = next(
                    arg.split("=", 1)[1]
                    for arg in command
                    if arg.startswith("--test-reporter-destination=")
                )
                with open(report_path, "w", encoding="utf-8") as report:
                    report.write(
                        '<testsuites tests="1"><testsuite tests="1">'
                        '<testcase classname="generated" name="test generated"/>'
                        "</testsuite></testsuites>"
                    )
                return subprocess.CompletedProcess(command, 0, "", "")

            with (
                patch("src.languages.typescript.shutil.which", return_value="node"),
                patch("src.languages.typescript.subprocess.run", side_effect=fake_run),
            ):
                code, diagnostics, results = adapter.run_tests(repo, target_test=test_path)

        evidence = adapter.evidence_from_test_run(code, diagnostics, results)
        self.assertEqual(evidence.outcome, RunOutcome.PASS)
        self.assertEqual(evidence.discovered_count, 1)
        self.assertTrue(any(adapter.is_new_test(test_id) for test_id in evidence.passed_test_ids))

    def test_typescript_node_file_import_failure_is_not_a_test_failure(self):
        adapter = TypeScriptAdapter()
        with tempfile.TemporaryDirectory() as repo:
            relative_test = os.path.join("tests", "test_feature_patch.test.ts")
            test_path = os.path.join(repo, relative_test)
            os.makedirs(os.path.dirname(test_path))
            with open(test_path, "w", encoding="utf-8") as test_file:
                test_file.write("import { missing } from './missing.js';\n")

            def fake_run(command, **kwargs):
                report_path = next(
                    arg.split("=", 1)[1]
                    for arg in command
                    if arg.startswith("--test-reporter-destination=")
                )
                with open(report_path, "w", encoding="utf-8") as report:
                    report.write(
                        "<testsuites><testcase "
                        f'name="{test_path}" file="{test_path}" classname="test">'
                        '<failure type="testCodeFailure" message="test failed">'
                        "module import failed</failure>"
                        "</testcase></testsuites>"
                    )
                return subprocess.CompletedProcess(command, 1, "", "")

            with patch("src.languages.typescript.subprocess.run", side_effect=fake_run):
                code, diagnostics, results = adapter._run_node_test_single(
                    repo, "node", [relative_test], relative_test, 10
                )

        evidence = adapter.evidence_from_test_run(code, diagnostics, results)
        self.assertEqual(evidence.outcome, RunOutcome.ERROR)
        self.assertEqual(evidence.discovered_count, 0)
        self.assertEqual(evidence.executed_count, 0)
        self.assertEqual(evidence.failed_count, 0)
        self.assertIn("file failure", evidence.diagnostics)

    def test_typescript_node_file_without_test_cases_is_no_tests_found(self):
        adapter = TypeScriptAdapter()
        with tempfile.TemporaryDirectory() as repo:
            relative_test = os.path.join("tests", "test_feature_patch.test.ts")
            test_path = os.path.join(repo, relative_test)
            os.makedirs(os.path.dirname(test_path))
            with open(test_path, "w", encoding="utf-8") as test_file:
                test_file.write("const helper = true;\n")

            def fake_run(command, **kwargs):
                report_path = next(
                    arg.split("=", 1)[1]
                    for arg in command
                    if arg.startswith("--test-reporter-destination=")
                )
                with open(report_path, "w", encoding="utf-8") as report:
                    report.write(
                        "<testsuites><testcase "
                        f'name="{test_path}" file="{test_path}" classname="test"/>'
                        "</testsuites>"
                    )
                return subprocess.CompletedProcess(command, 0, "", "")

            with patch("src.languages.typescript.subprocess.run", side_effect=fake_run):
                code, diagnostics, results = adapter._run_node_test_single(
                    repo, "node", [relative_test], relative_test, 10
                )

        evidence = adapter.evidence_from_test_run(code, diagnostics, results)
        self.assertEqual(evidence.outcome, RunOutcome.NO_TESTS_FOUND)
        self.assertEqual(evidence.discovered_count, 0)
        self.assertEqual(evidence.executed_count, 0)

    def test_vitest_generated_test_fallback_preserves_report_counts(self):
        adapter = TypeScriptAdapter()
        with tempfile.TemporaryDirectory() as repo:
            test_path = os.path.join(repo, adapter.test_file_relpath)
            os.makedirs(os.path.dirname(test_path))
            with open(test_path, "w", encoding="utf-8") as generated_test:
                generated_test.write("test('generated', () => {});\n")

            calls = []

            def fake_run(command, **kwargs):
                calls.append(command)
                report_path = next(
                    arg.split("=", 1)[1]
                    for arg in command
                    if arg.startswith("--outputFile=")
                )
                name = "existing test" if len(calls) == 1 else "generated test"
                classname = "existing" if len(calls) == 1 else "tests.test_feature_patch"
                with open(report_path, "w", encoding="utf-8") as report:
                    report.write(
                        '<testsuites tests="1"><testsuite tests="1">'
                        f'<testcase classname="{classname}" name="{name}"/>'
                        "</testsuite></testsuites>"
                    )
                return subprocess.CompletedProcess(command, 0, "", "")

            with patch("src.languages.typescript.subprocess.run", side_effect=fake_run):
                code, diagnostics, results = adapter._run_vitest(
                    repo, "vitest", None, 10
                )

        evidence = adapter.evidence_from_test_run(code, diagnostics, results)
        self.assertEqual(len(calls), 2)
        self.assertEqual(evidence.outcome, RunOutcome.PASS)
        self.assertEqual(evidence.discovered_count, 2)
        self.assertEqual(evidence.executed_count, 2)

    def test_vitest_identity_is_stable_across_sandbox_directories(self):
        adapter = TypeScriptAdapter()
        with tempfile.TemporaryDirectory() as temp_dir:
            ids = []
            for step in ("step2_negative", "step3_positive"):
                repo = os.path.join(temp_dir, step)
                relative_test = os.path.join("tests", "test_feature_patch.test.ts")
                test_path = os.path.join(repo, relative_test)
                os.makedirs(os.path.dirname(test_path))
                with open(test_path, "w", encoding="utf-8") as test_file:
                    test_file.write("test('generated behavior', () => {});\n")

                def fake_run(command, **kwargs):
                    report_path = next(
                        arg.split("=", 1)[1]
                        for arg in command
                        if arg.startswith("--outputFile=")
                    )
                    source_path = os.path.join(kwargs["cwd"], relative_test)
                    with open(report_path, "w", encoding="utf-8") as report:
                        report.write(
                            '<testsuites tests="1"><testsuite tests="1">'
                            f'<testcase classname="{source_path}" name="generated behavior"/>'
                            "</testsuite></testsuites>"
                        )
                    return subprocess.CompletedProcess(command, 0, "", "")

                with patch("src.languages.typescript.subprocess.run", side_effect=fake_run):
                    _, _, results = adapter._run_vitest(
                        repo, "vitest", relative_test, 10
                    )
                ids.append(next(iter(results)))

        self.assertEqual(ids[0], ids[1])
        self.assertEqual(ids[0], "tests/test_feature_patch.test.ts::generated behavior")

    def test_typescript_jest_json_report_is_parsed(self):
        adapter = TypeScriptAdapter()
        with tempfile.TemporaryDirectory() as repo:
            def fake_run(command, **kwargs):
                report_path = next(
                    arg.split("=", 1)[1] for arg in command if arg.startswith("--outputFile=")
                )
                with open(report_path, "w", encoding="utf-8") as report:
                    json.dump({
                        "numTotalTests": 1,
                        "testResults": [{
                            "name": os.path.join(repo, "tests", "sample.test.js"),
                            "assertionResults": [{
                                "fullName": "sample passes",
                                "status": "passed",
                            }],
                        }],
                    }, report)
                return subprocess.CompletedProcess(command, 0, "", "")

            with patch("src.languages.typescript.subprocess.run", side_effect=fake_run):
                code, diagnostics, results = adapter._run_jest(
                    repo, "jest", None, 10
                )

        evidence = adapter.evidence_from_test_run(code, diagnostics, results)
        self.assertEqual(evidence.outcome, RunOutcome.PASS)
        self.assertEqual(evidence.passed_count, 1)

    def test_malformed_jest_report_is_an_error(self):
        adapter = TypeScriptAdapter()
        with tempfile.TemporaryDirectory() as repo:
            def fake_run(command, **kwargs):
                report_path = next(
                    arg.split("=", 1)[1] for arg in command if arg.startswith("--outputFile=")
                )
                with open(report_path, "w", encoding="utf-8") as report:
                    report.write("{malformed")
                return subprocess.CompletedProcess(command, 0, "", "")

            with patch("src.languages.typescript.subprocess.run", side_effect=fake_run):
                code, diagnostics, results = adapter._run_jest(repo, "jest", None, 10)

        evidence = adapter.evidence_from_test_run(code, diagnostics, results)
        self.assertEqual(evidence.outcome, RunOutcome.ERROR)

    def test_cpp_ctest_junit_report_and_build_failure(self):
        adapter = CppAdapter()
        with tempfile.TemporaryDirectory() as repo:
            with open(os.path.join(repo, "CMakeLists.txt"), "w", encoding="utf-8") as cmake:
                cmake.write("project(fixture)\n")

            def fake_run(command, **kwargs):
                if "--test-dir" in command:
                    report_path = next(
                        arg.split("=", 1)[1]
                        for arg in command
                        if arg.startswith("--output-junit=")
                    )
                    with open(report_path, "w", encoding="utf-8") as report:
                        report.write(
                            '<testsuites tests="1"><testsuite tests="1">'
                            '<testcase classname="factory" name="factory_feature_test"/>'
                            "</testsuite></testsuites>"
                        )
                return subprocess.CompletedProcess(command, 0, "", "")

            with (
                patch("src.languages.cpp.shutil.which", side_effect=lambda name: name),
                patch("src.languages.cpp.subprocess.run", side_effect=fake_run),
            ):
                code, diagnostics, results = adapter._run_cmake_ctest(repo, None, 10)

            evidence = adapter.evidence_from_test_run(code, diagnostics, results)
            self.assertEqual(evidence.outcome, RunOutcome.PASS)
            self.assertEqual(evidence.passed_count, 1)

            def build_failure(command, **kwargs):
                return subprocess.CompletedProcess(
                    command, 1 if "--build" in command else 0, "", "compiler error"
                )

            with (
                patch("src.languages.cpp.shutil.which", side_effect=lambda name: name),
                patch("src.languages.cpp.subprocess.run", side_effect=build_failure),
            ):
                code, diagnostics, results = adapter._run_cmake_ctest(repo, None, 10)
            failed_build = adapter.evidence_from_test_run(code, diagnostics, results)
            self.assertEqual(failed_build.outcome, RunOutcome.ERROR)
            self.assertEqual(failed_build.executed_count, 0)

    def test_go_json_events_and_malformed_line_are_distinguished(self):
        adapter = GoAdapter()
        output = "\n".join((
            '{"Action":"run","Package":"example.com/fixture","Test":"TestA"}',
            '{"Action":"pass","Package":"example.com/fixture","Test":"TestA"}',
        ))
        with (
            patch("src.languages.go.shutil.which", return_value="go"),
            patch(
                "src.languages.go.subprocess.run",
                return_value=subprocess.CompletedProcess([], 0, output, ""),
            ),
        ):
            code, diagnostics, results = adapter.run_tests("unused")
        evidence = adapter.evidence_from_test_run(code, diagnostics, results)
        self.assertEqual(evidence.outcome, RunOutcome.PASS)
        self.assertEqual(evidence.passed_count, 1)

        with (
            patch("src.languages.go.shutil.which", return_value="go"),
            patch(
                "src.languages.go.subprocess.run",
                return_value=subprocess.CompletedProcess([], 0, "not-json\n", ""),
            ),
        ):
            code, diagnostics, results = adapter.run_tests("unused")
        malformed = adapter.evidence_from_test_run(code, diagnostics, results)
        self.assertEqual(malformed.outcome, RunOutcome.ERROR)

    def test_rust_cargo_output_records_suite_identity_and_results(self):
        adapter = RustAdapter()
        output = (
            "Running tests/test_feature_patch.rs (target/debug/deps/test_feature_patch-abc)\n"
            "running 2 tests\n"
            "test test_generated ... ok\n"
            "test test_other ... FAILED\n"
        )
        with (
            patch("src.languages.rust.shutil.which", return_value="cargo"),
            patch(
                "src.languages.rust.subprocess.run",
                return_value=subprocess.CompletedProcess([], 1, output, ""),
            ),
        ):
            code, diagnostics, results = adapter.run_tests("unused", target_test="test")
        evidence = adapter.evidence_from_test_run(code, diagnostics, results)
        self.assertEqual(evidence.outcome, RunOutcome.FAIL)
        self.assertEqual(evidence.discovered_count, 2)
        self.assertIn(
            "tests/test_feature_patch.rs::test_generated",
            evidence.passed_test_ids,
        )

    def test_java_clears_stale_reports_before_parsing_current_run(self):
        adapter = JavaAdapter()
        with tempfile.TemporaryDirectory() as repo:
            pom_path = os.path.join(repo, "pom.xml")
            with open(pom_path, "w", encoding="utf-8") as pom:
                pom.write("<project/>")
            report_dir = os.path.join(repo, "module", "target", "surefire-reports")
            os.makedirs(report_dir)
            stale_path = os.path.join(report_dir, "TEST-Stale.xml")
            with open(stale_path, "w", encoding="utf-8") as report:
                report.write(
                    '<testsuite tests="1"><testcase classname="FeaturePatchTest" '
                    'name="stale"/></testsuite>'
                )

            def fake_run(command, **kwargs):
                self.assertFalse(os.path.exists(stale_path))
                fresh_path = os.path.join(report_dir, "TEST-Fresh.xml")
                with open(fresh_path, "w", encoding="utf-8") as report:
                    report.write(
                        '<testsuite tests="1"><testcase classname="FeaturePatchTest" '
                        'name="fresh"/></testsuite>'
                    )
                return subprocess.CompletedProcess(command, 0, "", "")

            with (
                patch("src.languages.java.shutil.which", return_value="mvn"),
                patch("src.languages.java.subprocess.run", side_effect=fake_run),
            ):
                code, diagnostics, results = adapter.run_tests(repo)

        evidence = adapter.evidence_from_test_run(code, diagnostics, results)
        self.assertEqual(evidence.outcome, RunOutcome.PASS)
        self.assertEqual(evidence.discovered_count, 1)
        self.assertEqual(evidence.passed_test_ids, ["FeaturePatchTest::fresh"])

    def test_java_clears_stale_gradle_reports_before_parsing_current_run(self):
        adapter = JavaAdapter()
        with tempfile.TemporaryDirectory() as repo:
            with open(os.path.join(repo, "build.gradle"), "w", encoding="utf-8") as gradle:
                gradle.write("plugins { id 'java' }\n")
            with open(os.path.join(repo, "gradlew"), "w", encoding="utf-8") as wrapper:
                wrapper.write("gradle wrapper fixture\n")
            report_dir = os.path.join(repo, "module", "build", "test-results", "test")
            os.makedirs(report_dir)
            stale_path = os.path.join(report_dir, "TEST-Stale.xml")
            with open(stale_path, "w", encoding="utf-8") as report:
                report.write(
                    '<testsuite tests="1"><testcase classname="FeaturePatchTest" '
                    'name="stale"/></testsuite>'
                )

            def fake_run(command, **kwargs):
                self.assertFalse(os.path.exists(stale_path))
                fresh_path = os.path.join(report_dir, "TEST-Fresh.xml")
                with open(fresh_path, "w", encoding="utf-8") as report:
                    report.write(
                        '<testsuite tests="1"><testcase classname="FeaturePatchTest" '
                        'name="fresh"/></testsuite>'
                    )
                return subprocess.CompletedProcess(command, 0, "", "")

            with patch("src.languages.java.subprocess.run", side_effect=fake_run):
                code, diagnostics, results = adapter.run_tests(repo)

        evidence = adapter.evidence_from_test_run(code, diagnostics, results)
        self.assertEqual(evidence.outcome, RunOutcome.PASS)
        self.assertEqual(evidence.passed_test_ids, ["FeaturePatchTest::fresh"])


class TypeScriptSandboxWindowsTests(unittest.TestCase):
    @staticmethod
    def _write_json(path, value):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as file:
            json.dump(value, file)

    def _create_workspace_repo(self, repo):
        self._write_json(
            os.path.join(repo, "package.json"),
            {
                "name": "workspace-fixture",
                "type": "module",
                "workspaces": ["packages/*"],
            },
        )
        self._write_json(
            os.path.join(repo, "packages", "workspace", "package.json"),
            {
                "name": "@fixture/workspace",
                "type": "module",
                "exports": "./index.mjs",
            },
        )
        with open(
            os.path.join(repo, "packages", "workspace", "index.mjs"),
            "w",
            encoding="utf-8",
        ) as file:
            file.write('export const origin = "upstream";\n')

        alias_dir = os.path.join(repo, "node_modules", "@fixture", "workspace")
        self._write_json(
            os.path.join(alias_dir, "package.json"),
            {
                "name": "@fixture/workspace",
                "type": "module",
                "exports": "./index.mjs",
            },
        )
        with open(os.path.join(alias_dir, "index.mjs"), "w", encoding="utf-8") as file:
            file.write('export const origin = "upstream alias";\n')

        dependency_dir = os.path.join(repo, "node_modules", "external-fixture")
        self._write_json(
            os.path.join(dependency_dir, "package.json"),
            {
                "name": "external-fixture",
                "type": "module",
                "exports": "./index.mjs",
            },
        )
        with open(
            os.path.join(dependency_dir, "index.mjs"),
            "w",
            encoding="utf-8",
        ) as file:
            file.write('export const available = "yes";\n')

    def test_windows_runner_discovery_prefers_node_javascript_entrypoint(self):
        adapter = TypeScriptAdapter()
        with tempfile.TemporaryDirectory() as work_dir:
            package_dir = os.path.join(work_dir, "node_modules", "vitest")
            self._write_json(
                os.path.join(package_dir, "package.json"),
                {"name": "vitest", "bin": {"vitest": "./vitest.mjs"}},
            )
            entrypoint = os.path.join(package_dir, "vitest.mjs")
            with open(entrypoint, "w", encoding="utf-8") as file:
                file.write("process.exit(0);\n")
            bin_dir = os.path.join(work_dir, "node_modules", ".bin")
            os.makedirs(bin_dir)
            with open(os.path.join(bin_dir, "vitest"), "w", encoding="utf-8") as file:
                file.write("#!/bin/sh\n")
            with open(
                os.path.join(bin_dir, "vitest.cmd"),
                "w",
                encoding="utf-8",
            ) as file:
                file.write("@echo off\r\n")

            with patch("src.languages.typescript.os.name", "nt"):
                discovered = adapter._find_local_bin(work_dir, "vitest")

        self.assertEqual(os.path.normcase(discovered), os.path.normcase(entrypoint))

    @unittest.skipUnless(os.name == "nt" and shutil.which("node"), "Requires Windows Node.js")
    def test_windows_vitest_javascript_entrypoint_launches_and_reports_tests(self):
        adapter = TypeScriptAdapter()
        with tempfile.TemporaryDirectory() as work_dir:
            self._write_json(
                os.path.join(work_dir, "package.json"),
                {"devDependencies": {"vitest": "4.1.5"}},
            )
            package_dir = os.path.join(work_dir, "node_modules", "vitest")
            self._write_json(
                os.path.join(package_dir, "package.json"),
                {
                    "name": "vitest",
                    "type": "module",
                    "bin": {"vitest": "./vitest.mjs"},
                },
            )
            with open(
                os.path.join(package_dir, "vitest.mjs"),
                "w",
                encoding="utf-8",
            ) as file:
                file.write(
                    "import fs from 'node:fs';\n"
                    "const report = process.argv.find((arg) => arg.startsWith('--outputFile='));\n"
                    "if (!report) process.exit(2);\n"
                    "fs.writeFileSync(report.slice('--outputFile='.length), "
                    "'<testsuites tests=\"1\"><testsuite tests=\"1\">"
                    "<testcase classname=\"tests/sample.test.ts\" name=\"sample passes\"/>"
                    "</testsuite></testsuites>');\n"
                    "fs.writeFileSync('vitest-launch.txt', 'node');\n"
                )
            bin_dir = os.path.join(work_dir, "node_modules", ".bin")
            os.makedirs(bin_dir)
            with open(
                os.path.join(bin_dir, "vitest.cmd"),
                "w",
                encoding="utf-8",
            ) as file:
                file.write("@echo off\r\nexit /b 99\r\n")

            code, diagnostics, results = adapter.run_tests(work_dir, timeout=15)

            self.assertTrue(os.path.isfile(os.path.join(work_dir, "vitest-launch.txt")))

        evidence = adapter.evidence_from_test_run(code, diagnostics, results)
        self.assertEqual(evidence.outcome, RunOutcome.PASS, diagnostics)
        self.assertEqual(evidence.passed_test_ids, ["tests/sample.test.ts::sample passes"])

    def test_vitest_retry_uses_a_fresh_junit_report_path(self):
        adapter = TypeScriptAdapter()
        with tempfile.TemporaryDirectory() as work_dir:
            paths = []
            commands = []
            retry_configs = []

            def fake_run(command, **kwargs):
                commands.append(command)
                report_arg = next(
                    argument
                    for argument in command
                    if argument.startswith("--outputFile=")
                )
                report_path = report_arg.split("=", 1)[1]
                paths.append(report_path)
                if len(paths) == 1:
                    with open(report_path, "w", encoding="utf-8") as report:
                        report.write("<testsuites tests=\"0\"></testsuites>")
                    return subprocess.CompletedProcess(
                        command,
                        1,
                        "JUNIT report written",
                        "",
                    )

                config_index = command.index("--config")
                with open(command[config_index + 1], encoding="utf-8") as config:
                    retry_configs.append(config.read())
                with open(report_path, "w", encoding="utf-8") as report:
                    report.write(
                        '<testsuites tests="1"><testsuite tests="1">'
                        '<testcase classname="tests/test_feature_patch.test.ts" '
                        'name="generated behavior"/>'
                        "</testsuite></testsuites>"
                    )
                return subprocess.CompletedProcess(command, 0, "", "")

            with (
                patch.object(
                    adapter,
                    "_runner_command",
                    side_effect=lambda runner, args: [runner, *args],
                ),
                patch(
                    "src.languages.typescript.subprocess.run",
                    side_effect=fake_run,
                ),
            ):
                code, diagnostics, results = adapter._run_vitest(
                    work_dir,
                    "vitest",
                    os.path.join(work_dir, "tests", "test_feature_patch.test.ts"),
                    15,
                )

        self.assertEqual(code, 0, diagnostics)
        self.assertEqual(len(paths), 2)
        self.assertNotEqual(paths[0], paths[1])
        self.assertFalse(any(os.path.exists(path) for path in paths))
        self.assertEqual(
            commands[0][-1],
            "tests/test_feature_patch.test.ts",
        )
        self.assertEqual(
            commands[1][-1],
            "tests/test_feature_patch.test.ts",
        )
        self.assertEqual(
            retry_configs,
            [
                'export default { test: { include: ["tests/test_feature_patch.test.ts"] } };\n'
            ],
        )
        self.assertEqual(
            results,
            {"tests/test_feature_patch.test.ts::generated behavior": "passed"},
        )
        self.assertIsNone(results.report_error)

    @unittest.skipUnless(shutil.which("node"), "Requires Node.js")
    def test_dependency_staging_runs_with_sandbox_workspace_copy(self):
        adapter = TypeScriptAdapter()
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = os.path.join(temp_dir, "repo")
            self._create_workspace_repo(repo)
            work_dir = os.path.join(temp_dir, "sandbox")
            shutil.copytree(
                repo,
                work_dir,
                ignore=shutil.ignore_patterns("node_modules"),
            )
            with open(
                os.path.join(work_dir, "packages", "workspace", "index.mjs"),
                "w",
                encoding="utf-8",
            ) as file:
                file.write('export const origin = "sandbox";\n')
            os.makedirs(os.path.join(work_dir, "tests"))
            test_path = os.path.join(work_dir, "tests", "dependency-check.mjs")
            with open(test_path, "w", encoding="utf-8") as file:
                file.write(
                    'import { origin } from "@fixture/workspace";\n'
                    'import { available } from "external-fixture";\n'
                    'if (origin !== "sandbox" || available !== "yes") process.exit(1);\n'
                )

            if os.name == "nt":
                with patch(
                    "src.languages.typescript.os.symlink",
                    side_effect=OSError("symbolic links are unavailable"),
                ):
                    adapter.prepare_sandbox(repo, work_dir)
            else:
                adapter.prepare_sandbox(repo, work_dir)

            sandbox_alias = os.path.join(
                work_dir, "node_modules", "@fixture", "workspace"
            )
            expected_workspace = os.path.join(work_dir, "packages", "workspace")
            self.assertEqual(
                os.path.realpath(sandbox_alias),
                os.path.realpath(expected_workspace),
            )
            result = subprocess.run(
                [shutil.which("node"), test_path],
                cwd=work_dir,
                capture_output=True,
                text=True,
                timeout=15,
            )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_staging_failure_becomes_structured_error_evidence(self):
        adapter = TypeScriptAdapter()
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = os.path.join(temp_dir, "repo")
            self._create_workspace_repo(repo)
            with patch.object(
                adapter,
                "_create_directory_link",
                side_effect=SandboxPreparationError("junction creation denied"),
            ), patch.object(adapter, "run_tests_detailed") as run_tests:
                result = SandboxVerifier(repo, adapter=adapter).verify("", "")

        run_tests.assert_not_called()
        self.assertEqual(result.step1_evidence.outcome, RunOutcome.ERROR)
        self.assertEqual(result.step1_evidence.executed_count, 0)
        self.assertIn("Sandbox preparation failed", result.step1_evidence.diagnostics)
        self.assertIn("junction creation denied", result.step1_evidence.diagnostics)
        self.assertFalse(result.is_valid)

    def test_declared_vitest_never_falls_back_to_node_test(self):
        adapter = TypeScriptAdapter()
        with tempfile.TemporaryDirectory() as work_dir:
            self._write_json(
                os.path.join(work_dir, "package.json"),
                {"devDependencies": {"vitest": "4.1.5"}},
            )
            with (
                patch.object(adapter, "_find_local_bin", return_value=None),
                patch.object(adapter, "_run_node_test") as node_test,
            ):
                code, diagnostics, results = adapter.run_tests(work_dir)

        node_test.assert_not_called()
        self.assertEqual(code, -1)
        self.assertIn("no local runnable vitest entry point", diagnostics)
        self.assertEqual(results, {})

    def test_posix_runner_command_keeps_existing_launcher_behavior(self):
        runner = "/workspace/node_modules/.bin/vitest"
        with patch("src.languages.typescript.os.name", "posix"):
            command = TypeScriptAdapter._runner_command(runner, ["run", "--reporter=junit"])
        self.assertEqual(command, [runner, "run", "--reporter=junit"])


class VerifierFailToPassTests(unittest.TestCase):
    def _no_tests_result(self, adapter, diagnostic):
        results = _result_map(discovered_count=0)
        if adapter.language_id == "typescript":
            results.no_tests_found = True
        code = 5 if adapter.language_id == "python" else 0
        return code, diagnostic, results

    def _generated_result(self, test_id, status):
        return (0 if status == "passed" else 1, "generated test " + status,
                _result_map({test_id: status}))

    def test_all_six_adapters_prove_no_tests_baseline_fail_to_pass(self):
        for adapter_type, test_id, no_tests_output in _ADAPTER_CASES:
            with self.subTest(adapter=adapter_type.language_id):
                adapter = adapter_type()
                with tempfile.TemporaryDirectory() as repo:
                    with open(os.path.join(repo, "subject.txt"), "w", encoding="utf-8") as source:
                        source.write("before\n")

                    def fake_run_tests(work_dir, target_test=None, **kwargs):
                        if os.path.basename(work_dir) == "step1_baseline":
                            return self._no_tests_result(adapter, no_tests_output)
                        if os.path.basename(work_dir) == "step2_negative":
                            return self._generated_result(test_id, "failed")
                        return self._generated_result(test_id, "passed")

                    with patch.object(adapter, "run_tests", side_effect=fake_run_tests):
                        result = SandboxVerifier(repo, adapter=adapter).verify(_PATCH, "generated test")

                self.assertEqual(result.step1_outcome, "NO_TESTS_FOUND")
                self.assertFalse(result.step1_baseline_pass)
                self.assertTrue(result.step2_negative_fail)
                self.assertTrue(result.step3_positive_pass)
                self.assertEqual(result.fail_to_pass, [adapter.canonical_test_id(test_id)])
                self.assertTrue(result.is_valid)
                self.assertEqual(result.step2_evidence.executed_count, 1)
                self.assertEqual(result.step3_evidence.executed_count, 1)

    def test_generated_test_that_passes_before_patch_is_rejected(self):
        for adapter_type, test_id, no_tests_output in _ADAPTER_CASES:
            with self.subTest(adapter=adapter_type.language_id):
                adapter = adapter_type()
                with tempfile.TemporaryDirectory() as repo:
                    with open(os.path.join(repo, "subject.txt"), "w", encoding="utf-8") as source:
                        source.write("before\n")

                    def fake_run_tests(work_dir, target_test=None, **kwargs):
                        if os.path.basename(work_dir) == "step1_baseline":
                            return self._no_tests_result(adapter, no_tests_output)
                        return self._generated_result(test_id, "passed")

                    with patch.object(adapter, "run_tests", side_effect=fake_run_tests):
                        result = SandboxVerifier(repo, adapter=adapter).verify(_PATCH, "generated test")

                self.assertFalse(result.step2_negative_fail)
                self.assertFalse(result.is_valid)

    def test_existing_failing_or_untrusted_baselines_are_not_acceptable(self):
        for adapter_type, test_id, _ in _ADAPTER_CASES:
            for baseline_outcome in ("failed", "error"):
                with self.subTest(adapter=adapter_type.language_id, baseline=baseline_outcome):
                    adapter = adapter_type()
                    with tempfile.TemporaryDirectory() as repo:
                        with open(os.path.join(repo, "subject.txt"), "w", encoding="utf-8") as source:
                            source.write("before\n")

                        def fake_run_tests(work_dir, target_test=None, **kwargs):
                            step = os.path.basename(work_dir)
                            if step == "step1_baseline" and baseline_outcome == "failed":
                                return 1, "existing test failed", _result_map(
                                    {"existing::test_old": "failed"}
                                )
                            if step == "step1_baseline":
                                return -1, "runner error", {}
                            if step == "step2_negative":
                                return self._generated_result(test_id, "failed")
                            return self._generated_result(test_id, "passed")

                        with patch.object(adapter, "run_tests", side_effect=fake_run_tests):
                            result = SandboxVerifier(repo, adapter=adapter).verify(
                                _PATCH, "generated test"
                            )

                    self.assertFalse(result.step1_baseline_pass)
                    self.assertFalse(result.is_valid)

    def test_step_two_and_three_must_match_canonical_test_identity(self):
        adapter = TypeScriptAdapter()
        with tempfile.TemporaryDirectory() as repo:
            with open(os.path.join(repo, "subject.txt"), "w", encoding="utf-8") as source:
                source.write("before\n")

            def fake_run_tests(work_dir, target_test=None, **kwargs):
                step = os.path.basename(work_dir)
                if step == "step1_baseline":
                    return self._no_tests_result(adapter, "No existing TypeScript/JavaScript test suite found.")
                if step == "step2_negative":
                    return self._generated_result(
                        "tests/test_feature_patch.test.ts::generated fails", "failed"
                    )
                return self._generated_result(
                    "tests/test_feature_patch.test.ts::a different test passes", "passed"
                )

            with patch.object(adapter, "run_tests", side_effect=fake_run_tests):
                result = SandboxVerifier(repo, adapter=adapter).verify(_PATCH, "generated test")

        self.assertFalse(result.step3_positive_pass)
        self.assertFalse(result.is_valid)
        self.assertEqual(result.fail_to_pass, [])


class VerificationResultIntegrityTests(unittest.TestCase):
    generated_id = "tests.test_feature_patch::test_generated"

    @staticmethod
    def _evidence(outcome, results):
        statuses = list(results.values())
        return RunEvidence(
            outcome=outcome,
            discovered_count=len(results),
            executed_count=sum(status in ("passed", "failed") for status in statuses),
            passed_count=statuses.count("passed"),
            failed_count=statuses.count("failed"),
            skipped_count=statuses.count("skipped"),
            error_count=statuses.count("error"),
            test_results=results,
            exit_code=1 if outcome == RunOutcome.FAIL else 0,
        )

    def _result(self, step2_results, failed_ids, step3_results):
        adapter = PythonAdapter()
        result = VerificationResult(adapter=adapter)
        result.step1_evidence = self._evidence(
            RunOutcome.PASS, {"tests.test_existing::test_ok": "passed"}
        )
        result.step2_evidence = self._evidence(RunOutcome.FAIL, step2_results)
        result.step3_evidence = self._evidence(RunOutcome.PASS, step3_results)
        result.step2_failed_test_ids = failed_ids
        result.step1_baseline_pass = True
        result.step2_negative_fail = True
        result.step3_positive_pass = True
        return result

    def test_boolean_only_results_never_validate_and_legacy_fields_serialize(self):
        result = VerificationResult()
        result.step1_baseline_pass = True
        result.step2_negative_fail = True
        result.step3_positive_pass = True

        serialized = result.to_dict()
        self.assertFalse(result.is_valid)
        self.assertFalse(serialized["is_valid"])
        self.assertEqual(
            serialized["matrix"],
            {
                "step1_baseline": {"expected": "PASS", "passed": True},
                "step2_negative_check": {"expected": "FAIL", "passed": True},
                "step3_positive_check": {"expected": "PASS", "passed": True},
            },
        )
        self.assertEqual(
            serialized["step_evidence"],
            {
                "step1_baseline": None,
                "step2_negative_check": None,
                "step3_positive_check": None,
                "step2_failed_test_ids": [],
            },
        )

    def test_missing_any_step_evidence_fails_closed(self):
        result = self._result(
            {self.generated_id: "failed"},
            [self.generated_id],
            {self.generated_id: "passed"},
        )
        for evidence_name in (
            "step1_evidence",
            "step2_evidence",
            "step3_evidence",
        ):
            with self.subTest(missing=evidence_name):
                complete = self._result(
                    {self.generated_id: "failed"},
                    [self.generated_id],
                    {self.generated_id: "passed"},
                )
                setattr(complete, evidence_name, None)
                self.assertFalse(complete.is_valid)
        self.assertTrue(result.is_valid)

    def test_fabricated_and_unbacked_step_two_ids_fail_validation(self):
        actual_failure = "tests.test_feature_patch::test_actual"
        fabricated_id = "tests.test_feature_patch::test_fabricated"
        result = self._result(
            {actual_failure: "failed"},
            [fabricated_id],
            {fabricated_id: "passed"},
        )
        self.assertFalse(result.is_valid)

        absent_id = self.generated_id
        absent = self._result(
            {"existing::test_old": "failed"},
            [absent_id],
            {absent_id: "passed"},
        )
        self.assertFalse(absent.is_valid)

    def test_step_two_identity_must_be_failed_and_genuinely_generated(self):
        unrelated_failure = "existing::test_old"
        passed_in_step_two = self._result(
            {
                self.generated_id: "passed",
                unrelated_failure: "failed",
            },
            [self.generated_id],
            {self.generated_id: "passed"},
        )
        self.assertFalse(passed_in_step_two.is_valid)

        nongenerated_failure = "tests.test_existing::test_old"
        nongenerated = self._result(
            {nongenerated_failure: "failed"},
            [nongenerated_failure],
            {nongenerated_failure: "passed"},
        )
        self.assertFalse(nongenerated.is_valid)

        skipped = self._result(
            {
                self.generated_id: "skipped",
                unrelated_failure: "failed",
            },
            [self.generated_id],
            {self.generated_id: "passed"},
        )
        self.assertFalse(skipped.is_valid)

        empty_id = self._result(
            {self.generated_id: "failed"},
            [""],
            {self.generated_id: "passed"},
        )
        self.assertFalse(empty_id.is_valid)

    def test_genuine_step_two_failure_and_same_step_three_pass_remains_valid(self):
        result = self._result(
            {self.generated_id: "failed"},
            [self.generated_id],
            {self.generated_id: "passed"},
        )
        self.assertTrue(result.is_valid)


if __name__ == "__main__":
    unittest.main()
