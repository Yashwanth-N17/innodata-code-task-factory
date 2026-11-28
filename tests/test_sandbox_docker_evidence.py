import json
import os
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from src.languages import (
    CppAdapter,
    GoAdapter,
    JavaAdapter,
    PythonAdapter,
    RustAdapter,
    TestRunOutcome as RunOutcome,
    TypeScriptAdapter,
)
from src.sandbox_docker import DockerSandboxRunner


class DockerCommandTests(unittest.TestCase):
    def test_commands_use_each_language_native_report_format(self):
        with tempfile.TemporaryDirectory() as work_dir:
            python_command = DockerSandboxRunner._build_container_command(
                PythonAdapter(), work_dir, None, "_factory_junit.xml"
            )
            self.assertIn("--junitxml=_factory_junit.xml", python_command)
            self.assertNotIn(" tests", python_command)

            os.makedirs(os.path.join(work_dir, "node_modules", ".bin"))
            vitest = os.path.join(work_dir, "node_modules", ".bin", "vitest")
            with open(vitest, "w", encoding="utf-8") as marker:
                marker.write("")
            command = DockerSandboxRunner._build_container_command(
                TypeScriptAdapter(), work_dir, None, "_factory_junit.xml"
            )
            self.assertIn("--reporter=junit", command)
            self.assertTrue(command.startswith("./node_modules/.bin/vitest run --reporter=junit "))

            os.remove(vitest)
            jest = os.path.join(work_dir, "node_modules", ".bin", "jest")
            with open(jest, "w", encoding="utf-8") as marker:
                marker.write("")
            command = DockerSandboxRunner._build_container_command(
                TypeScriptAdapter(), work_dir, None, "_factory_junit.xml"
            )
            self.assertIn("--json", command)
            self.assertIn("--outputFile=_factory_jest.json", command)

            os.remove(jest)
            command = DockerSandboxRunner._build_container_command(
                TypeScriptAdapter(), work_dir, None, "_factory_junit.xml"
            )
            self.assertIn("--test-reporter=junit", command)
            self.assertNotIn("test_feature_patch.test.ts", command)

            with open(os.path.join(work_dir, "CMakeLists.txt"), "w", encoding="utf-8") as cmake:
                cmake.write("project(fixture)\n")
            command = DockerSandboxRunner._build_container_command(
                CppAdapter(), work_dir, None, "_factory_junit.xml"
            )
            self.assertIn("--output-junit=_factory_junit.xml", command)

            command = DockerSandboxRunner._build_container_command(
                GoAdapter(), work_dir, None, "_factory_junit.xml"
            )
            self.assertEqual(command, "go test -json ./...")
            target_command = DockerSandboxRunner._build_container_command(
                GoAdapter(),
                work_dir,
                os.path.join(work_dir, "tests", "test_feature_patch_test.go"),
                "_factory_junit.xml",
            )
            self.assertEqual(target_command, "go test -json ./tests")
            command = DockerSandboxRunner._build_container_command(
                RustAdapter(), work_dir, None, "_factory_junit.xml"
            )
            self.assertEqual(command, "cargo test")

            with open(os.path.join(work_dir, "pom.xml"), "w", encoding="utf-8") as pom:
                pom.write("<project/>")
            command = DockerSandboxRunner._build_container_command(
                JavaAdapter(), work_dir, None, "_factory_junit.xml"
            )
            self.assertEqual(command, "mvn -B test")

            os.remove(os.path.join(work_dir, "pom.xml"))
            with open(os.path.join(work_dir, "gradlew"), "w", encoding="utf-8") as gradle:
                gradle.write("#!/bin/sh\n")
            command = DockerSandboxRunner._build_container_command(
                JavaAdapter(), work_dir, None, "_factory_junit.xml"
            )
            self.assertEqual(command, "sh ./gradlew test")

    def test_cpp_direct_mode_command_is_available_but_never_claims_unstructured_success(self):
        adapter = CppAdapter()
        with tempfile.TemporaryDirectory() as work_dir:
            command = DockerSandboxRunner._build_container_command(
                adapter,
                work_dir,
                os.path.join(work_dir, "tests", "test_feature_patch.cpp"),
                "_factory_junit.xml",
            )
            self.assertIn("g++", command)

            with (
                patch("src.sandbox_docker.shutil.which", return_value="docker"),
                patch(
                    "src.sandbox_docker.subprocess.run",
                    return_value=subprocess.CompletedProcess([], 0, "", ""),
                ) as docker_run,
            ):
                code, diagnostics, raw_results = DockerSandboxRunner.run_tests(
                    work_dir, adapter
                )
                no_tests = adapter.evidence_from_test_run(code, diagnostics, raw_results)
                code, diagnostics, raw_results = DockerSandboxRunner.run_tests(
                    work_dir,
                    adapter,
                    target_test=os.path.join(work_dir, "tests", "test_feature_patch.cpp"),
                )

        docker_run.assert_called_once()
        self.assertEqual(no_tests.outcome, RunOutcome.NO_TESTS_FOUND)
        evidence = adapter.evidence_from_test_run(code, diagnostics, raw_results)
        self.assertEqual(evidence.outcome, RunOutcome.ERROR)
        self.assertIn("per-test report", diagnostics)


class DockerEvidenceTests(unittest.TestCase):
    @staticmethod
    def _run_docker(adapter, work_dir, target_test=None, fake_run=None):
        with (
            patch("src.sandbox_docker.shutil.which", return_value="docker"),
            patch("src.sandbox_docker.subprocess.run", side_effect=fake_run),
        ):
            code, diagnostics, results = DockerSandboxRunner.run_tests(
                work_dir, adapter, target_test=target_test
            )
        return code, diagnostics, adapter.evidence_from_test_run(code, diagnostics, results)

    @staticmethod
    def _write_junit(work_dir, testcase, tests=1):
        report_path = os.path.join(work_dir, "_factory_junit.xml")
        with open(report_path, "w", encoding="utf-8") as report:
            report.write(f"<testsuites tests=\"{tests}\">{testcase}</testsuites>")

    def test_python_uses_junit_and_classifies_empty_report_as_no_tests(self):
        adapter = PythonAdapter()
        with tempfile.TemporaryDirectory() as work_dir:
            def fake_run(command, **kwargs):
                self.assertNotIn(" tests", command[-1])
                self._write_junit(work_dir, "", tests=0)
                return subprocess.CompletedProcess(command, 5, "no tests ran", "")

            code, diagnostics, evidence = self._run_docker(
                adapter, work_dir, fake_run=fake_run
            )
        self.assertEqual(evidence.outcome, RunOutcome.NO_TESTS_FOUND)
        self.assertEqual(evidence.discovered_count, 0)

    def test_typescript_vitest_and_node_junit_reports_are_classified(self):
        adapter = TypeScriptAdapter()
        with tempfile.TemporaryDirectory() as work_dir:
            os.makedirs(os.path.join(work_dir, "node_modules", ".bin"))
            with open(
                os.path.join(work_dir, "node_modules", ".bin", "vitest"),
                "w", encoding="utf-8",
            ) as marker:
                marker.write("")
            rel_test = os.path.join("tests", "test_feature_patch.test.ts")
            test_path = os.path.join(work_dir, rel_test)

            def fake_vitest(command, **kwargs):
                self._write_junit(
                    work_dir,
                    '<testcase classname="/workspace/tests/test_feature_patch.test.ts" '
                    'name="generated behavior"/>',
                )
                return subprocess.CompletedProcess(command, 0, "", "")

            _, _, vitest_evidence = self._run_docker(
                adapter, work_dir, test_path, fake_vitest
            )
            self.assertEqual(vitest_evidence.outcome, RunOutcome.PASS)
            self.assertEqual(
                vitest_evidence.passed_test_ids,
                ["tests/test_feature_patch.test.ts::generated behavior"],
            )

            os.remove(os.path.join(work_dir, "node_modules", ".bin", "vitest"))
            def fake_node(command, **kwargs):
                self._write_junit(
                    work_dir,
                    '<testcase classname="/workspace/tests/test_feature_patch.test.ts" '
                    'name="generated behavior"/>',
                )
                return subprocess.CompletedProcess(command, 0, "", "")

            _, _, node_evidence = self._run_docker(
                adapter, work_dir, test_path, fake_node
            )
        self.assertEqual(node_evidence.outcome, RunOutcome.PASS)
        self.assertEqual(node_evidence.passed_test_ids, vitest_evidence.passed_test_ids)

    def test_typescript_jest_json_report_is_classified(self):
        adapter = TypeScriptAdapter()
        with tempfile.TemporaryDirectory() as work_dir:
            bin_dir = os.path.join(work_dir, "node_modules", ".bin")
            os.makedirs(bin_dir)
            with open(os.path.join(bin_dir, "jest"), "w", encoding="utf-8") as marker:
                marker.write("")

            def fake_run(command, **kwargs):
                report_path = os.path.join(work_dir, "_factory_jest.json")
                with open(report_path, "w", encoding="utf-8") as report:
                    json.dump({
                        "numTotalTests": 1,
                        "testResults": [{
                            "name": "/workspace/tests/test_feature_patch.test.ts",
                            "assertionResults": [{
                                "fullName": "generated behavior",
                                "status": "passed",
                            }],
                        }],
                    }, report)
                return subprocess.CompletedProcess(command, 0, "", "")

            _, _, evidence = self._run_docker(adapter, work_dir, fake_run=fake_run)

        self.assertEqual(evidence.outcome, RunOutcome.PASS)
        self.assertEqual(
            evidence.passed_test_ids,
            ["tests/test_feature_patch.test.ts::generated behavior"],
        )

    def test_typescript_mounts_and_restores_sandbox_dependency_symlink(self):
        adapter = TypeScriptAdapter()
        with tempfile.TemporaryDirectory() as temp_dir:
            work_dir = os.path.join(temp_dir, "work")
            dependency_dir = os.path.join(temp_dir, "dependencies")
            os.makedirs(os.path.join(work_dir, "tests"))
            os.makedirs(os.path.join(dependency_dir, ".bin"))
            with open(
                os.path.join(dependency_dir, ".bin", "vitest"),
                "w", encoding="utf-8",
            ) as marker:
                marker.write("")
            node_modules = os.path.join(work_dir, "node_modules")
            try:
                os.symlink(dependency_dir, node_modules, target_is_directory=True)
            except OSError as exc:
                self.skipTest(f"directory symlinks are unavailable: {exc}")

            def fake_run(command, **kwargs):
                self.assertIn(
                    f"{dependency_dir}:/workspace/node_modules:ro", command
                )
                self._write_junit(
                    work_dir,
                    '<testcase classname="/workspace/tests/test_feature_patch.test.ts" '
                    'name="generated behavior"/>',
                )
                return subprocess.CompletedProcess(command, 0, "", "")

            test_path = os.path.join(work_dir, "tests", "test_feature_patch.test.ts")
            _, _, evidence = self._run_docker(
                adapter, work_dir, test_path, fake_run
            )
            self.assertTrue(os.path.islink(node_modules))

        self.assertEqual(evidence.outcome, RunOutcome.PASS)

    def test_cpp_ctest_junit_report_is_classified(self):
        adapter = CppAdapter()
        with tempfile.TemporaryDirectory() as work_dir:
            with open(os.path.join(work_dir, "CMakeLists.txt"), "w", encoding="utf-8") as cmake:
                cmake.write("project(fixture)\n")

            def fake_run(command, **kwargs):
                self._write_junit(
                    work_dir,
                    '<testcase classname="factory" name="factory_feature_test"/>',
                )
                return subprocess.CompletedProcess(command, 0, "", "")

            _, _, evidence = self._run_docker(adapter, work_dir, fake_run=fake_run)

        self.assertEqual(evidence.outcome, RunOutcome.PASS)
        self.assertEqual(evidence.passed_test_ids, ["factory::factory_feature_test"])

    def test_go_native_json_events_are_classified(self):
        adapter = GoAdapter()
        output = json.dumps({
            "Action": "pass",
            "Package": "example.com/fixture",
            "Test": "TestFeaturePatch",
        })

        def fake_run(command, **kwargs):
            return subprocess.CompletedProcess(command, 0, output, "")

        with tempfile.TemporaryDirectory() as work_dir:
            _, _, evidence = self._run_docker(adapter, work_dir, fake_run=fake_run)

        self.assertEqual(evidence.outcome, RunOutcome.PASS)
        self.assertEqual(
            evidence.passed_test_ids,
            ["example.com/fixture::TestFeaturePatch"],
        )

        mixed_output = "\n".join((
            output,
            json.dumps({
                "Action": "build-fail",
                "Package": "example.com/other",
                "Output": "compiler error",
            }),
        ))
        mixed = adapter.evidence_from_test_run(
            1, mixed_output, adapter.parse_test_output(mixed_output)
        )
        self.assertEqual(mixed.outcome, RunOutcome.ERROR)

    def test_rust_native_cargo_output_is_classified(self):
        adapter = RustAdapter()
        output = (
            "Running tests/test_feature_patch.rs (target/debug/deps/test_feature_patch-abc)\n"
            "running 1 test\n"
            "test test_generated ... ok\n"
        )

        def fake_run(command, **kwargs):
            return subprocess.CompletedProcess(command, 0, output, "")

        with tempfile.TemporaryDirectory() as work_dir:
            _, _, evidence = self._run_docker(adapter, work_dir, fake_run=fake_run)

        self.assertEqual(evidence.outcome, RunOutcome.PASS)
        self.assertEqual(evidence.passed_count, 1)

        mixed_output = output + "\nerror[E0425]: cannot find value in this scope\n"
        mixed = adapter.evidence_from_test_run(
            1, mixed_output, adapter.parse_test_output(mixed_output)
        )
        self.assertEqual(mixed.outcome, RunOutcome.ERROR)

    def test_java_maven_and_gradle_reports_are_fresh_and_classified(self):
        adapter = JavaAdapter()
        testcase = (
            '<testsuite tests="1"><testcase classname="FeaturePatchTest" '
            'name="testGenerated"/></testsuite>'
        )
        with tempfile.TemporaryDirectory() as work_dir:
            with open(os.path.join(work_dir, "pom.xml"), "w", encoding="utf-8") as pom:
                pom.write("<project/>")
            report_dir = os.path.join(work_dir, "module", "target", "surefire-reports")
            os.makedirs(report_dir)
            stale = os.path.join(report_dir, "TEST-Stale.xml")
            with open(stale, "w", encoding="utf-8") as report:
                report.write(
                    '<testsuite tests="1"><testcase classname="FeaturePatchTest" '
                    'name="stale"/></testsuite>'
                )

            def fake_maven(command, **kwargs):
                self.assertFalse(os.path.exists(stale))
                with open(os.path.join(report_dir, "TEST-Fresh.xml"), "w", encoding="utf-8") as report:
                    report.write(testcase)
                return subprocess.CompletedProcess(command, 0, "", "")

            _, _, maven_evidence = self._run_docker(
                adapter, work_dir, fake_run=fake_maven
            )
            self.assertEqual(maven_evidence.outcome, RunOutcome.PASS)
            self.assertEqual(
                maven_evidence.passed_test_ids,
                ["FeaturePatchTest::testGenerated"],
            )
            failing_report = os.path.join(report_dir, "TEST-Failing.xml")
            with open(failing_report, "w", encoding="utf-8") as report:
                report.write(
                    '<testsuite tests="1"><testcase classname="FeaturePatchTest" '
                    'name="testFailed"><failure/></testcase></testsuite>'
                )
            mixed_results = adapter.collect_test_results(
                work_dir, 1, "COMPILATION ERROR"
            )
            mixed_evidence = adapter.evidence_from_test_run(
                1, "COMPILATION ERROR", mixed_results
            )
            self.assertEqual(mixed_evidence.outcome, RunOutcome.ERROR)

            os.remove(os.path.join(work_dir, "pom.xml"))
            with open(os.path.join(work_dir, "build.gradle"), "w", encoding="utf-8") as gradle:
                gradle.write("plugins { id 'java' }\n")
            gradle_report_dir = os.path.join(
                work_dir, "module", "build", "test-results", "test"
            )
            os.makedirs(gradle_report_dir)

            def fake_gradle(command, **kwargs):
                self.assertIn("gradle:8-jdk21", command)
                with open(
                    os.path.join(gradle_report_dir, "TEST-Gradle.xml"),
                    "w", encoding="utf-8",
                ) as report:
                    report.write(testcase)
                return subprocess.CompletedProcess(command, 0, "", "")

            _, _, gradle_evidence = self._run_docker(
                adapter, work_dir, fake_run=fake_gradle
            )

        self.assertEqual(gradle_evidence.outcome, RunOutcome.PASS)
        self.assertEqual(
            gradle_evidence.passed_test_ids,
            ["FeaturePatchTest::testGenerated"],
        )

    def test_missing_structured_report_is_error_not_exit_code_pass(self):
        adapter = CppAdapter()
        with tempfile.TemporaryDirectory() as work_dir:
            with open(os.path.join(work_dir, "CMakeLists.txt"), "w", encoding="utf-8") as cmake:
                cmake.write("project(fixture)\n")
            stale = os.path.join(work_dir, "_factory_junit.xml")
            self._write_junit(
                work_dir,
                '<testcase classname="factory" name="stale"/>',
            )

            def fake_run(command, **kwargs):
                self.assertFalse(os.path.exists(stale))
                return subprocess.CompletedProcess(command, 0, "", "")

            code, diagnostics, evidence = self._run_docker(
                adapter, work_dir, fake_run=fake_run
            )

        self.assertEqual(evidence.outcome, RunOutcome.ERROR)
        self.assertIn("missing", evidence.diagnostics.lower())

    def test_java_no_tests_report_is_not_confused_with_missing_report_error(self):
        adapter = JavaAdapter()
        with tempfile.TemporaryDirectory() as work_dir:
            with open(os.path.join(work_dir, "pom.xml"), "w", encoding="utf-8") as pom:
                pom.write("<project/>")

            def fake_run(command, **kwargs):
                return subprocess.CompletedProcess(command, 0, "[INFO] No tests to run.", "")

            _, _, evidence = self._run_docker(
                adapter, work_dir, fake_run=fake_run
            )

        self.assertEqual(evidence.outcome, RunOutcome.NO_TESTS_FOUND)


if __name__ == "__main__":
    unittest.main()
