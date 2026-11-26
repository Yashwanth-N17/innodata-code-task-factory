"""
Java Language Adapter.
Supports Maven (`pom.xml`) and Gradle (`build.gradle`) parsing, Java class/method scanning,
and JUnit XML test verification.
"""

import glob
import os
import re
import shutil
import subprocess
from typing import Any, Dict, List, Optional, Tuple

from .base import BaseLanguageAdapter, DEFAULT_IGNORED_DIRS, parse_junit_xml


_JAVA_IMPORT_RE = re.compile(r"^\s*import\s+(?:static\s+)?([^;]+);")
_JAVA_CLASS_RE = re.compile(
    r"^\s*(?:public|protected|private)?\s*(?:abstract|final|static)?\s*(class|interface|enum)\s+([A-Za-z_][A-Za-z0-9_]*)"
    r"(?:<[^>]*>)?(?:\s+extends\s+([A-Za-z0-9_.<>]+))?"
)
_JAVA_METHOD_RE = re.compile(
    r"^\s*(?:(public|protected|private)\s+)?(?:static\s+|final\s+|synchronized\s+)*"
    r"([A-Za-z_][A-Za-z0-9_.<>\[\]]*)\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(([^)]*)\)\s*(?:throws\s+[^{;]+)?\s*(?:\{|;)"
)


class JavaAdapter(BaseLanguageAdapter):
    """Language adapter for Java (Maven / Gradle) repositories."""

    language_id = "java"
    display_name = "Java"
    source_extensions = (".java",)
    test_extensions = ("Test.java", "Tests.java")
    test_framework = "junit"
    test_file_relpath = os.path.join("src", "test", "java", "FeaturePatchTest.java")
    root_test_filename = "FeaturePatchTest.java"

    @classmethod
    def detect_score(cls, repo_path: str) -> int:
        score = 0
        for m in ("pom.xml", "build.gradle", "build.gradle.kts"):
            if os.path.isfile(os.path.join(repo_path, m)):
                score += 45
                break
        java_count, other_count = 0, 0
        for root, dirs, files in os.walk(repo_path):
            dirs[:] = [d for d in dirs if d not in DEFAULT_IGNORED_DIRS and d != "target"]
            for f in files:
                low = f.lower()
                if low.endswith(".java"):
                    java_count += 1
                elif low.endswith((".py", ".ts", ".js", ".cpp", ".h", ".go", ".rs")):
                    other_count += 1
            if java_count + other_count >= 120:
                break
        if java_count > 0:
            score += int((java_count / max(1, java_count + other_count)) * 55)
        return min(100, score)

    def inspect_manifest(self, repo_path: str) -> Dict[str, Any]:
        build_tool = "maven" if os.path.isfile(os.path.join(repo_path, "pom.xml")) else "gradle"
        manifests = [m for m in ("pom.xml", "build.gradle", "build.gradle.kts") if os.path.isfile(os.path.join(repo_path, m))]
        return {
            "language": "java",
            "build_tool": build_tool,
            "java_version": ">=17",
            "test_framework": "junit",
            "manifests": manifests,
        }

    def is_test_file(self, rel_path: str, full_path: str) -> bool:
        norm = rel_path.replace("\\", "/")
        return "/src/test/" in f"/{norm}" or norm.endswith(("Test.java", "Tests.java"))

    def scan_file(self, full_path: str, rel_path: str, summary: Dict[str, Any]) -> None:
        try:
            with open(full_path, "r", encoding="utf-8", errors="replace") as f:
                lines = f.read().splitlines()
        except Exception:
            return

        current_cls: Optional[Dict[str, Any]] = None
        for line in lines:
            im = _JAVA_IMPORT_RE.match(line)
            if im:
                summary["imports"].append(im.group(1).strip())
                continue
            cm = _JAVA_CLASS_RE.match(line)
            if cm:
                bases = [cm.group(3).strip()] if cm.group(3) else []
                current_cls = {
                    "file": rel_path,
                    "name": cm.group(2),
                    "kind": cm.group(1),
                    "docstring": "",
                    "bases": bases,
                    "methods": [],
                    "public_methods": [],
                    "private_methods": [],
                    "method_details": [],
                }
                summary["classes"].append(current_cls)
                continue
            mm = _JAVA_METHOD_RE.match(line)
            if mm and current_cls is not None:
                vis = mm.group(1) or "package"
                ret, mname, params_raw = mm.group(2), mm.group(3), mm.group(4)
                if mname in ("if", "for", "while", "switch", "catch"):
                    continue
                params = [p.strip() for p in params_raw.split(",") if p.strip()]
                sig = f"{ret} {mname}({', '.join(params)})"
                current_cls["methods"].append(mname)
                if vis == "private":
                    current_cls["private_methods"].append(sig)
                else:
                    current_cls["public_methods"].append(sig)

    def run_tests(
        self,
        work_dir: str,
        target_test: Optional[str] = None,
        timeout: int = 120,
        **kwargs,
    ) -> Tuple[int, str, Dict[str, str]]:
        if os.path.isfile(os.path.join(work_dir, "pom.xml")):
            mvn = shutil.which("mvn")
            if not mvn:
                return -1, "mvn is not installed in PATH.", {}
            cmd = [mvn, "-B", "test"]
            if target_test:
                cmd.append("-Dtest=FeaturePatchTest")
            report_glob = os.path.join(work_dir, "target", "surefire-reports", "TEST-*.xml")
        else:
            gradlew = os.path.join(work_dir, "gradlew")
            gradle = gradlew if os.path.isfile(gradlew) else shutil.which("gradle")
            if not gradle:
                return -1, "gradle is not installed in PATH.", {}
            cmd = [gradle, "test"]
            if target_test:
                cmd.extend(["--tests", "FeaturePatchTest"])
            report_glob = os.path.join(work_dir, "build", "test-results", "test", "TEST-*.xml")

        try:
            res = subprocess.run(
                cmd, cwd=work_dir, capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=timeout,
            )
            results: Dict[str, str] = {}
            for xml_file in glob.glob(report_glob):
                results.update(parse_junit_xml(xml_file))
            return res.returncode, res.stdout + "\n" + res.stderr, results
        except subprocess.TimeoutExpired:
            return -1, f"Java test execution timed out after {timeout} seconds.", {}
        except Exception as e:
            return -1, f"Java test execution failed: {e}", {}

    def is_real_failure(self, code: int, out: str) -> bool:
        return code > 0 and "is not installed in PATH" not in out

    def is_new_test(self, test_id: str) -> bool:
        return "FeaturePatchTest" in test_id

    def get_leakage_regexes(self) -> List[re.Pattern]:
        return [re.compile(r"\b[a-zA-Z0-9_\-./]+\.java:\d+(?::\d+)?\b", re.IGNORECASE)]

    def get_environment_metadata(self, repo_path: str, manifest: Dict[str, Any]) -> Dict[str, Any]:
        cmd = "mvn test -Dtest=FeaturePatchTest" if manifest.get("build_tool") == "maven" else "./gradlew test --tests FeaturePatchTest"
        return {
            "java_version": manifest.get("java_version", ">=17"),
            "build_tool": manifest.get("build_tool", "maven"),
            "test_command": cmd,
        }
