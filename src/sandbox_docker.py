"""
Containerized Docker Sandbox Runner.
Provides network-disabled, ephemeral Docker container execution for the 3-step
SWE-bench verification matrix across Python, TypeScript, C++, Go, Rust, and Java.
"""

import os
import shutil
import subprocess
from typing import Dict, Optional, Tuple

from .languages.base import BaseLanguageAdapter, parse_junit_xml


DEFAULT_DOCKER_IMAGES: Dict[str, str] = {
    "python": "python:3.12-slim",
    "typescript": "node:22-slim",
    "cpp": "gcc:14",
    "go": "golang:1.22",
    "rust": "rust:1.80",
    "java": "maven:3.9-eclipse-temurin-21",
}


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
    def _build_container_command(
        adapter: BaseLanguageAdapter,
        work_dir: str,
        target_test: Optional[str],
        junit_rel: str,
    ) -> str:
        lang = adapter.language_id
        rel_target = os.path.relpath(target_test, work_dir).replace("\\", "/") if target_test else ""

        if lang == "python":
            target_arg = rel_target if rel_target else "tests"
            return (
                "PYTHONPATH=src:. pytest -q -p no:cacheprovider "
                f"--junitxml={junit_rel} {target_arg}"
            )
        if lang == "typescript":
            if os.path.isfile(os.path.join(work_dir, "node_modules", ".bin", "vitest")):
                arg = rel_target if rel_target else ""
                return f"./node_modules/.bin/vitest run --reporter=junit --outputFile={junit_rel} {arg}".strip()
            target_arg = rel_target if rel_target else adapter.test_file_relpath.replace("\\", "/")
            return f"node --test --test-reporter=junit --test-reporter-destination={junit_rel} {target_arg}"
        if lang == "cpp":
            if os.path.isfile(os.path.join(work_dir, "CMakeLists.txt")):
                flt = "-R '^factory_feature_test$'" if rel_target else ""
                bld_tgt = "--target factory_feature_test" if rel_target else ""
                return (
                    "cmake -S . -B _factory_build && "
                    f"cmake --build _factory_build -j {bld_tgt} && "
                    f"ctest --test-dir _factory_build --output-on-failure --output-junit={junit_rel} {flt}"
                ).strip()
            return f"g++ -std=c++17 -Iinclude -Isrc -I. {rel_target or adapter.test_file_relpath} -o _bin_test && ./_bin_test"
        if lang == "go":
            return "go test -json ./..."
        if lang == "rust":
            return f"cargo test {'--test test_feature_patch' if rel_target else ''}".strip()
        if lang == "java":
            return f"mvn -B test {'-Dtest=FeaturePatchTest' if rel_target else ''}".strip()
        return "echo 'Unsupported container language'"

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

        junit_rel = "_factory_junit.xml"
        junit_host = os.path.join(work_dir, junit_rel)
        inner_cmd = cls._build_container_command(adapter, work_dir, target_test, junit_rel)
        chosen_image = cls.get_image(adapter.language_id, override_image=image)

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
        try:
            res = subprocess.run(
                cmd, capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=timeout,
            )
            results = parse_junit_xml(junit_host)
            return res.returncode, res.stdout + "\n" + res.stderr, results
        except subprocess.TimeoutExpired:
            return -1, f"Docker sandbox timed out after {timeout} seconds.", {}
        except Exception as e:
            return -1, f"Docker sandbox execution failed: {e}", {}
        finally:
            if os.path.isfile(junit_host):
                try:
                    os.remove(junit_host)
                except OSError:
                    pass
