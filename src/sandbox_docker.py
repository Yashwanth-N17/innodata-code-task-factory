"""
Containerized Docker Sandbox Runner.
Provides network-disabled, ephemeral Docker container execution for the 3-step
SWE-bench verification matrix across Python, TypeScript, C++, Go, Rust, and Java.
"""

import os
import shlex
import shutil
import subprocess
from typing import Dict, List, Optional, Tuple

from .languages.base import (
    DEFAULT_IGNORED_DIRS,
    BaseLanguageAdapter,
    TestResultMap,
    parse_junit_xml,
)


DEFAULT_DOCKER_IMAGES: Dict[str, str] = {
    "python": "python:3.12-slim",
    "typescript": "node:22-slim",
    "cpp": "gcc:14",
    "go": "golang:1.22",
    "rust": "rust:1.80",
    "java": "maven:3.9-eclipse-temurin-21",
}
DEFAULT_GRADLE_DOCKER_IMAGE = "gradle:8-jdk21"


class DockerSandboxRunner:
    """Executes verification commands inside ephemeral `--network=none` Docker containers."""

    @staticmethod
    def is_available() -> bool:
        docker_bin = shutil.which("docker")
        if not docker_bin:
            return False
        try:
            res = subprocess.run([docker_bin, "info"], capture_output=True, text=True, timeout=5)
            return res.returncode == 0
        except Exception:
            return False

    @classmethod
    def get_image(cls, language_id: str, override_image: Optional[str] = None) -> str:
        if override_image and override_image.strip():
            return override_image.strip()
        return DEFAULT_DOCKER_IMAGES.get(language_id, "ubuntu:24.04")

    @staticmethod
    def _typescript_mode(work_dir: str) -> str:
        local_bin = os.path.join(work_dir, "node_modules", ".bin")
        if os.path.isfile(os.path.join(local_bin, "vitest")):
            return "typescript-vitest"
        if os.path.isfile(os.path.join(local_bin, "jest")):
            return "typescript-jest"
        return "typescript-node"

    @staticmethod
    def _java_mode(work_dir: str) -> str:
        return "java-maven" if os.path.isfile(os.path.join(work_dir, "pom.xml")) else "java-gradle"

    @classmethod
    def _build_container_command(
        cls,
        adapter: BaseLanguageAdapter,
        work_dir: str,
        target_test: Optional[str],
        junit_rel: str,
        json_rel: str = "_factory_jest.json",
    ) -> str:
        lang = adapter.language_id
        rel_target = (
            os.path.relpath(target_test, work_dir).replace("\\", "/")
            if target_test else ""
        )
        target_arg = shlex.quote(rel_target) if rel_target else ""

        if lang == "python":
            args = ["pytest", "-q", "-p", "no:cacheprovider", f"--junitxml={junit_rel}"]
            if target_arg:
                args.append(target_arg)
            return "PYTHONPATH=src:. " + " ".join(args)
        if lang == "typescript":
            mode = cls._typescript_mode(work_dir)
            if mode == "typescript-vitest":
                args = [
                    "./node_modules/.bin/vitest", "run", "--reporter=junit",
                    f"--outputFile={junit_rel}",
                ]
                if target_arg:
                    args.append(target_arg)
                return " ".join(args)
            if mode == "typescript-jest":
                args = [
                    "./node_modules/.bin/jest", "--json",
                    f"--outputFile={json_rel}",
                ]
                if target_arg:
                    args.append(target_arg)
                return " ".join(args)
            args = ["node", "--test", "--test-reporter=junit",
                    f"--test-reporter-destination={junit_rel}"]
            if target_arg:
                args.append(target_arg)
            return " ".join(args)
        if lang == "cpp":
            if os.path.isfile(os.path.join(work_dir, "CMakeLists.txt")):
                flt = "-R '^factory_feature_test$'" if rel_target else ""
                bld_tgt = "--target factory_feature_test" if rel_target else ""
                return (
                    "cmake -S . -B _factory_build && "
                    f"cmake --build _factory_build -j {bld_tgt} && "
                    f"ctest --test-dir _factory_build --output-on-failure "
                    f"--output-junit={junit_rel} {flt}"
                ).strip()
            production_sources, test_sources = cls._cpp_source_groups(
                work_dir, adapter
            )
            if rel_target:
                test_sources = [rel_target]
            if not test_sources:
                return "echo 'No existing C++ test suite found.'"
            commands = []
            include_flags = "-Iinclude -Isrc -I."
            production_args = " ".join(
                shlex.quote(source) for source in production_sources
            )
            for index, source in enumerate(test_sources):
                source_args = " ".join(
                    part for part in (production_args, shlex.quote(source)) if part
                )
                binary = f"_factory_direct_test_{index}"
                commands.append(
                    f"g++ -std=c++17 {include_flags} {source_args} "
                    f"-o {binary} && ./{binary}"
                )
            return " && ".join(commands)
        if lang == "go":
            package = "./..."
            if rel_target:
                package_dir = os.path.dirname(rel_target).replace("\\", "/") or "."
                package = f"./{package_dir}" if package_dir != "." else "."
            return f"go test -json {package}"
        if lang == "rust":
            target = "--test test_feature_patch" if rel_target else ""
            return f"cargo test {target}".strip()
        if lang == "java":
            if cls._java_mode(work_dir) == "java-maven":
                target = "-Dtest=FeaturePatchTest" if rel_target else ""
                return f"mvn -B test {target}".strip()
            target = "--tests FeaturePatchTest" if rel_target else ""
            if os.path.isfile(os.path.join(work_dir, "gradlew")):
                return f"sh ./gradlew test {target}".strip()
            return f"gradle test {target}".strip()
        return "echo 'Unsupported container language'"

    @staticmethod
    def _report_error(message: str) -> TestResultMap:
        results = TestResultMap()
        results.discovered_count = 0
        results.report_error = message
        return results

    @staticmethod
    def _remove_stale_file(path: str) -> Optional[str]:
        try:
            if os.path.isfile(path):
                os.remove(path)
            elif os.path.exists(path):
                return f"Test report path is not a file: {path}"
        except OSError as exc:
            return f"Could not clear stale test report {path}: {exc}"
        return None

    @staticmethod
    def _has_cpp_test_sources(work_dir: str, adapter: BaseLanguageAdapter) -> bool:
        _, tests = DockerSandboxRunner._cpp_source_groups(work_dir, adapter)
        return bool(tests)

    @staticmethod
    def _cpp_source_groups(
        work_dir: str,
        adapter: BaseLanguageAdapter,
    ) -> Tuple[List[str], List[str]]:
        production_sources = []
        test_sources = []
        for root, dirs, files in os.walk(work_dir):
            dirs[:] = [name for name in dirs if name not in DEFAULT_IGNORED_DIRS]
            for filename in files:
                if not filename.lower().endswith((".cpp", ".cc", ".cxx")):
                    continue
                full_path = os.path.join(root, filename)
                rel_path = os.path.relpath(full_path, work_dir).replace("\\", "/")
                if adapter.is_test_file(rel_path, full_path):
                    test_sources.append(rel_path)
                else:
                    production_sources.append(rel_path)
        return production_sources, test_sources

    @staticmethod
    def _typescript_test_files(
        work_dir: str,
        adapter: BaseLanguageAdapter,
    ) -> List[str]:
        test_files = []
        for root, dirs, files in os.walk(work_dir):
            dirs[:] = [name for name in dirs if name not in DEFAULT_IGNORED_DIRS]
            for filename in files:
                full_path = os.path.join(root, filename)
                rel_path = os.path.relpath(full_path, work_dir).replace("\\", "/")
                if adapter.is_test_file(rel_path, full_path):
                    test_files.append(rel_path)
        return test_files

    @staticmethod
    def _normalize_container_junit_ids(results: TestResultMap) -> None:
        normalized = {}
        for test_id, status in results.items():
            classname, separator, name = test_id.partition("::")
            prefix = "/workspace/"
            if classname.startswith(prefix):
                classname = classname[len(prefix):]
            normalized_id = f"{classname}::{name}" if separator else classname
            normalized[normalized_id] = status
        results.clear()
        results.update(normalized)

    @classmethod
    def run_tests(
        cls,
        work_dir: str,
        adapter: BaseLanguageAdapter,
        target_test: Optional[str] = None,
        timeout: int = 120,
        image: Optional[str] = None,
    ) -> Tuple[int, str, Dict[str, str]]:
        docker_bin = shutil.which("docker")
        if not docker_bin:
            return -1, "Docker CLI is not installed in PATH.", {}

        language = adapter.language_id
        mode = cls._typescript_mode(work_dir) if language == "typescript" else language
        is_cpp_direct = (
            language == "cpp"
            and not os.path.isfile(os.path.join(work_dir, "CMakeLists.txt"))
        )
        if is_cpp_direct:
            if target_test is None and not cls._has_cpp_test_sources(work_dir, adapter):
                empty = TestResultMap()
                empty.discovered_count = 0
                return 0, "No existing C++ test suite found.", empty

        junit_rel = "_factory_junit.xml"
        json_rel = "_factory_jest.json"
        junit_host = os.path.join(work_dir, junit_rel)
        json_host = os.path.join(work_dir, json_rel)
        cleanup_paths = [junit_host, json_host]
        if language == "java":
            clear_error = adapter.clear_stale_test_reports(work_dir)
            if clear_error:
                return -1, clear_error, cls._report_error(clear_error)
        for report_path in cleanup_paths:
            clear_error = cls._remove_stale_file(report_path)
            if clear_error:
                return -1, clear_error, cls._report_error(clear_error)

        inner_cmd = cls._build_container_command(
            adapter, work_dir, target_test, junit_rel, json_rel
        )
        if image and image.strip():
            chosen_image = image.strip()
        elif language == "java" and cls._java_mode(work_dir) == "java-gradle":
            chosen_image = DEFAULT_GRADLE_DOCKER_IMAGE
        else:
            chosen_image = cls.get_image(language)
        node_modules_path = os.path.join(work_dir, "node_modules")
        node_modules_link = None
        node_modules_source = None
        if language == "typescript" and os.path.islink(node_modules_path):
            candidate = os.path.realpath(node_modules_path)
            if os.path.isdir(candidate):
                node_modules_link = os.readlink(node_modules_path)
                node_modules_source = candidate
                os.remove(node_modules_path)
                os.makedirs(node_modules_path)
        cmd = [
            docker_bin, "run", "--rm",
            "--network=none",
            "--cpus=2",
            "--memory=2g",
            "-v", f"{os.path.abspath(work_dir)}:/workspace:Z",
            "-w", "/workspace",
            chosen_image,
            "sh", "-c", inner_cmd,
        ]
        if node_modules_source:
            cmd[cmd.index("-w"):cmd.index("-w")] = [
                "-v", f"{node_modules_source}:/workspace/node_modules:ro",
            ]
        try:
            res = subprocess.run(
                cmd, capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=timeout,
            )
            diagnostics = res.stdout + "\n" + res.stderr
            if is_cpp_direct:
                results = cls._report_error(
                    "Docker direct C++ mode cannot provide structured per-test evidence."
                )
                diagnostics = (
                    f"{diagnostics}\n"
                    "Direct C++ compilation/execution completed without a native "
                    "per-test report."
                ).strip()
            elif language in ("python", "cpp") or mode in (
                "typescript-vitest", "typescript-node",
            ):
                results = parse_junit_xml(junit_host)
                if mode in ("typescript-vitest", "typescript-node"):
                    cls._normalize_container_junit_ids(results)
                    adapter._normalize_report_ids(results, "/workspace")
                    rel_target = (
                        os.path.relpath(target_test, work_dir).replace("\\", "/")
                        if target_test else None
                    )
                    test_files = (
                        [rel_target] if rel_target
                        else cls._typescript_test_files(work_dir, adapter)
                    )
                    adapter._remove_file_level_results(
                        junit_host, "/workspace", test_files, results
                    )
            elif mode == "typescript-jest":
                results = adapter.parse_jest_report(json_host, "/workspace")
            elif language == "go":
                results = adapter.parse_test_output(res.stdout)
            elif language == "rust":
                results = adapter.parse_test_output(diagnostics)
            elif language == "java":
                results = adapter.collect_test_results(
                    work_dir, res.returncode, diagnostics
                )
            else:
                results = cls._report_error(
                    f"No structured Docker test parser is available for {language}."
                )
            return res.returncode, diagnostics, results
        except subprocess.TimeoutExpired:
            return -1, f"Docker sandbox timed out after {timeout} seconds.", {}
        except Exception as exc:
            return -1, f"Docker sandbox execution failed: {exc}", {}
        finally:
            for report_path in cleanup_paths:
                try:
                    if os.path.isfile(report_path):
                        os.remove(report_path)
                except OSError:
                    pass
            if node_modules_link is not None:
                try:
                    shutil.rmtree(node_modules_path)
                    os.symlink(
                        node_modules_link,
                        node_modules_path,
                        target_is_directory=True,
                    )
                except OSError as exc:
                    raise RuntimeError(
                        f"Could not restore TypeScript dependency symlink: {exc}"
                    ) from exc
