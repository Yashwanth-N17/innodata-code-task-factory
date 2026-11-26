"""
C++ Language Adapter.
Supports CMake / CTest / custom jsontest.h / g++ repositories (e.g., open-source-parsers/jsoncpp),
paired header/implementation (.h + .cpp) symbol analysis, and build-then-test verification.
"""

import os
import re
import shutil
import subprocess
import tempfile
from typing import Any, Dict, List, Optional, Tuple

from .base import BaseLanguageAdapter, DEFAULT_IGNORED_DIRS, parse_junit_xml


_CPP_INCLUDE_RE = re.compile(r'^\s*#\s*include\s+[<"]([^>"]+)[>"]')
_CPP_NS_RE = re.compile(r"^\s*namespace\s+([A-Za-z_][A-Za-z0-9_:]*)\s*\{")
_CPP_CLASS_RE = re.compile(
    r"^\s*(?:template\s*<[^>]+>\s*)?(class|struct)\s+(?:[A-Z0-9_]+_API\s+)?([A-Za-z_][A-Za-z0-9_]*)"
    r"(?:\s+final)?(?:\s*:\s*([^{;]+))?\s*\{"
)
_CPP_ACCESS_RE = re.compile(r"^\s*(public|private|protected)\s*:")
_CPP_METHOD_RE = re.compile(
    r"^\s*(?:\[\[[^\]]+\]\]\s*)*(?:(?:virtual|static|inline|explicit|constexpr)\s+)*"
    r"([A-Za-z_~][A-Za-z0-9_:<>&*\s]*?)\s+"
    r"(operator\s*(?:\[\]|\(\)|[=<>!+\-*/&|^~]+)|[A-Za-z_~][A-Za-z0-9_]*)\s*\(([^;{}]*)\)\s*"
    r"(?:const)?\s*(?:noexcept)?\s*(?:override|final)?\s*(?:=\s*0)?\s*(?:;|{)"
)
_CPP_FREE_FN_RE = re.compile(
    r"^\s*(?:(?:static|inline|constexpr|extern)\s+)*"
    r"([A-Za-z_][A-Za-z0-9_:<>&*\s]+?)\s+([A-Za-z_][A-Za-z0-9_:]*)\s*\(([^;{}]*)\)\s*(?:const)?\s*(?:\{|;)"
)

_CPP_CONTROL_WORDS = {"if", "for", "while", "switch", "catch", "else", "return", "sizeof", "alignof"}
_INJECT_MARKER = "# __FACTORY_TEST_INJECTED__"


def _strip_cpp_comments(text: str) -> str:
    text = re.sub(r"/\*[\s\S]*?\*/", "", text)
    return re.sub(r"//[^\n]*", "", text)


class CppAdapter(BaseLanguageAdapter):
    """Language adapter for C++ repositories (e.g., open-source-parsers/jsoncpp)."""

    language_id = "cpp"
    display_name = "C++"
    source_extensions = (".cpp", ".cc", ".cxx", ".c++", ".h", ".hpp", ".hh", ".hxx", ".inl")
    test_extensions = (".cpp", ".cc", ".cxx")
    test_framework = "ctest"
    test_file_relpath = os.path.join("tests", "test_feature_patch.cpp")
    root_test_filename = "test_patch.cpp"

    @classmethod
    def detect_score(cls, repo_path: str) -> int:
        score = 0
        if os.path.isfile(os.path.join(repo_path, "CMakeLists.txt")):
            score += 35
        elif os.path.isfile(os.path.join(repo_path, "meson.build")) or os.path.isfile(os.path.join(repo_path, "Makefile")):
            score += 20

        if os.path.isdir(os.path.join(repo_path, "include")):
            score += 10

        cpp_count, other_count = 0, 0
        for root, dirs, files in os.walk(repo_path):
            dirs[:] = [d for d in dirs if d not in DEFAULT_IGNORED_DIRS]
            for f in files:
                low = f.lower()
                if low.endswith((".cpp", ".cc", ".cxx", ".hpp", ".hh", ".h")):
                    cpp_count += 1
                elif low.endswith((".py", ".ts", ".tsx", ".js", ".go", ".rs")):
                    other_count += 1
            if cpp_count + other_count >= 150:
                break

        if cpp_count > 0:
            ratio = cpp_count / max(1, cpp_count + other_count)
            score += int(ratio * 55)
        return min(100, score)

    def inspect_manifest(self, repo_path: str) -> Dict[str, Any]:
        info: Dict[str, Any] = {
            "language": "cpp",
            "package_name": os.path.basename(os.path.abspath(repo_path)),
            "build_system": "g++",
            "cpp_standard": "c++17",
            "cmake_targets": [],
            "test_options": [],
            "test_framework": "ctest",
            "jsontest_detected": False,
            "include_dirs": [],
            "manifests": [],
        }

        for inc in ("include", "src"):
            if os.path.isdir(os.path.join(repo_path, inc)):
                info["include_dirs"].append(inc)

        # Walk up to depth 2 for CMakeLists.txt & jsontest.h
        cmake_files: List[str] = []
        for root, dirs, files in os.walk(repo_path):
            dirs[:] = [d for d in dirs if d not in DEFAULT_IGNORED_DIRS]
            depth = os.path.relpath(root, repo_path).count(os.sep)
            if "CMakeLists.txt" in files and depth <= 2:
                cmake_files.append(os.path.join(root, "CMakeLists.txt"))
            if "jsontest.h" in files:
                info["jsontest_detected"] = True
                info["test_framework"] = "ctest_jsontest"

        if cmake_files:
            info["build_system"] = "cmake"
            info["manifests"] = [os.path.relpath(p, repo_path) for p in sorted(cmake_files)]

        for cm in cmake_files:
            try:
                with open(cm, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read()
                proj_m = re.search(r"project\s*\(\s*([A-Za-z0-9_.-]+)", content, re.IGNORECASE)
                if proj_m and cm == os.path.join(repo_path, "CMakeLists.txt"):
                    info["package_name"] = proj_m.group(1)

                std_m = re.search(r"CMAKE_CXX_STANDARD\s+(\d+)", content)
                if std_m:
                    info["cpp_standard"] = f"c++{std_m.group(1)}"

                for lib_m in re.finditer(r"add_library\s*\(\s*([A-Za-z0-9_.-]+)(?:\s+(STATIC|SHARED|OBJECT|INTERFACE))?", content, re.IGNORECASE):
                    tname = lib_m.group(1)
                    tkind = (lib_m.group(2) or "").upper()
                    if tkind != "OBJECT" and not tname.startswith("${") and tname not in info["cmake_targets"]:
                        info["cmake_targets"].append(tname)

                for opt_m in re.finditer(r"option\s*\(\s*([A-Z0-9_]*TEST[A-Z0-9_]*)\b", content):
                    opt = opt_m.group(1)
                    if "POST_BUILD" in opt:
                        if opt not in info.setdefault("disable_options", []):
                            info["disable_options"].append(opt)
                    elif opt not in info["test_options"]:
                        info["test_options"].append(opt)
            except Exception:
                pass

        return info

    def is_test_file(self, rel_path: str, full_path: str) -> bool:
        norm = rel_path.replace("\\", "/")
        parts = [p.lower() for p in norm.split("/")]
        fname = parts[-1]

        if any(
            p in ("tests", "test", "unittest", "unittests")
            or p.startswith("test_")
            or p.endswith("_test")
            for p in parts[:-1]
        ):
            return True
        if fname.startswith("test_") or fname.endswith(("_test.cpp", "_test.cc", "_unittest.cpp")) or fname in ("jsontest.cpp", "jsontest.h"):
            return True
        return False

    def scan_file(self, full_path: str, rel_path: str, summary: Dict[str, Any]) -> None:
        try:
            with open(full_path, "r", encoding="utf-8", errors="replace") as f:
                raw = f.read()
        except Exception:
            return

        cleaned = _strip_cpp_comments(raw)
        lines = cleaned.splitlines()
        n = len(lines)
        i = 0
        active_ns: List[str] = []

        while i < n:
            line = lines[i]

            inc_m = _CPP_INCLUDE_RE.match(line)
            if inc_m:
                summary["imports"].append(inc_m.group(1))
                i += 1
                continue

            # Support Allman-style braces on next line for namespace/class/struct declarations
            combined_line = line
            step_offset = 1
            if "{" not in line and ";" not in line and i + 1 < n and lines[i + 1].lstrip().startswith("{"):
                combined_line = line + " " + lines[i + 1].lstrip()
                step_offset = 2

            ns_m = _CPP_NS_RE.match(combined_line)
            if ns_m:
                active_ns.append(ns_m.group(1))
                i += step_offset
                continue

            cls_m = _CPP_CLASS_RE.match(combined_line)
            if cls_m:
                kind = cls_m.group(1)
                raw_name = cls_m.group(2)
                full_name = "::".join([*active_ns, raw_name]) if active_ns else raw_name
                bases_raw = (cls_m.group(3) or "").strip()
                bases = []
                if bases_raw:
                    for b in bases_raw.split(","):
                        b_clean = re.sub(r"\b(public|protected|private|virtual)\b", "", b).strip()
                        if b_clean:
                            bases.append(b_clean)

                depth = combined_line.count("{") - combined_line.count("}")
                body_lines: List[str] = []
                j = i + step_offset
                while j < n and depth > 0:
                    cur = lines[j]
                    depth += cur.count("{") - cur.count("}")
                    if depth >= 1:
                        body_lines.append(cur)
                    j += 1

                current_access = "public" if kind == "struct" else "private"
                public_methods: List[str] = []
                private_methods: List[str] = []
                all_methods: List[str] = []
                method_details: List[Dict[str, Any]] = []

                # Join multi-line method signatures inside class body up to ';' or '{'
                logical_lines: List[str] = []
                buf = ""
                for bl in body_lines:
                    s = bl.strip()
                    if not s or s.startswith("#"):
                        continue
                    acc_m = _CPP_ACCESS_RE.match(s)
                    if acc_m:
                        if buf:
                            logical_lines.append(buf)
                            buf = ""
                        logical_lines.append(s)
                        continue
                    buf = f"{buf} {s}".strip() if buf else s
                    if s.endswith((";", "{", "}")):
                        logical_lines.append(buf)
                        buf = ""
                if buf:
                    logical_lines.append(buf)

                for bl in logical_lines:
                    acc_m = _CPP_ACCESS_RE.match(bl)
                    if acc_m:
                        current_access = acc_m.group(1)
                        continue
                    mm = _CPP_METHOD_RE.match(bl)
                    if not mm:
                        continue
                    ret_type = mm.group(1).strip()
                    mname = mm.group(2).strip()
                    if mname in _CPP_CONTROL_WORDS or ret_type in _CPP_CONTROL_WORDS:
                        continue
                    params = [p.strip() for p in mm.group(3).split(",") if p.strip()]
                    is_priv = current_access in ("private", "protected")
                    sig = f"{ret_type} {mname}({', '.join(params)})"
                    method_details.append({
                        "name": mname,
                        "signature": sig,
                        "params": params,
                        "return_type": ret_type,
                        "is_private": is_priv,
                        "is_async": False,
                    })
                    all_methods.append(mname)
                    if is_priv:
                        private_methods.append(sig)
                    else:
                        public_methods.append(sig)

                summary["classes"].append({
                    "file": rel_path,
                    "name": full_name,
                    "short_name": raw_name,
                    "kind": kind,
                    "docstring": "",
                    "bases": bases,
                    "methods": all_methods,
                    "public_methods": public_methods,
                    "private_methods": private_methods,
                    "method_details": method_details,
                })
                i = max(i + 1, j)
                continue

            fn_m = _CPP_FREE_FN_RE.match(line)
            if fn_m:
                ret_type = fn_m.group(1).strip()
                fname = fn_m.group(2).strip()
                if fname not in _CPP_CONTROL_WORDS and ret_type not in _CPP_CONTROL_WORDS and "::" not in fname:
                    params = [p.strip() for p in fn_m.group(3).split(",") if p.strip()]
                    sig = f"{ret_type} {fname}({', '.join(params)})"
                    summary["functions"].append({
                        "file": rel_path,
                        "name": fname,
                        "docstring": "",
                        "params": params,
                        "return_type": ret_type,
                        "signature": sig,
                        "is_private": fname.startswith("_"),
                        "is_async": False,
                        "decorators": [],
                    })

            if "}" in line and active_ns:
                # Approximate exit of top-level namespace scope
                if line.strip().startswith("}"):
                    active_ns.pop()

            i += 1

    def select_targets(
        self,
        repo_path: str,
        scan_data: Dict[str, Any],
        max_files: int = 3,
        target_offset: int = 0,
    ) -> List[str]:
        """Select core files and automatically pair .h declarations with .cpp/.inl implementations."""
        ranked = super().select_targets(repo_path, scan_data, max_files=max_files, target_offset=target_offset)
        all_files = scan_data.get("files") or []
        paired: List[str] = []

        for f in ranked:
            if f not in paired:
                paired.append(f)
            stem = os.path.splitext(os.path.basename(f))[0]
            # Pair header <-> cpp implementation (e.g. value.h <-> json_value.cpp)
            for candidate in all_files:
                if candidate in paired:
                    continue
                c_stem = os.path.splitext(os.path.basename(candidate))[0]
                if (
                    c_stem == stem
                    or c_stem.endswith(f"_{stem}")
                    or stem.endswith(f"_{c_stem}")
                    or (candidate.endswith(".inl") and stem in c_stem)
                ):
                    paired.append(candidate)
                if len(paired) >= max_files + 2:
                    break
        return paired[:max_files + 2]

    def _inject_cmake_test_target(self, work_dir: str, manifest: Dict[str, Any]) -> None:
        cmake_path = os.path.join(work_dir, "CMakeLists.txt")
        if not os.path.isfile(cmake_path):
            return
        with open(cmake_path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
        if _INJECT_MARKER in content:
            return

        targets = list(manifest.get("cmake_targets") or [])
        targets_str = " ".join(targets)
        link_block = ""
        if targets_str:
            link_block = f"""
    foreach(_ftgt IN ITEMS {targets_str})
        if(TARGET ${{_ftgt}})
            target_link_libraries(factory_feature_test PRIVATE ${{_ftgt}})
            break()
        endif()
    endforeach()"""

        snippet = f"""
{_INJECT_MARKER}
if(EXISTS "${{CMAKE_CURRENT_SOURCE_DIR}}/tests/test_feature_patch.cpp")
    enable_testing()
    add_executable(factory_feature_test "${{CMAKE_CURRENT_SOURCE_DIR}}/tests/test_feature_patch.cpp")
    {link_block}
    target_include_directories(factory_feature_test PRIVATE
        "${{CMAKE_CURRENT_SOURCE_DIR}}/include"
        "${{CMAKE_BINARY_DIR}}/include"
        "${{CMAKE_CURRENT_SOURCE_DIR}}/src"
        "${{CMAKE_CURRENT_SOURCE_DIR}}")
    add_test(NAME factory_feature_test COMMAND factory_feature_test)
endif()
"""
        with open(cmake_path, "w", encoding="utf-8") as f:
            f.write(content.rstrip() + "\n" + snippet)

    def run_tests(
        self,
        work_dir: str,
        target_test: Optional[str] = None,
        timeout: int = 90,
        **kwargs,
    ) -> Tuple[int, str, Dict[str, str]]:
        if os.path.isfile(os.path.join(work_dir, "CMakeLists.txt")):
            return self._run_cmake_ctest(work_dir, target_test, timeout)
        return self._run_gpp_direct(work_dir, target_test, timeout)

    def _run_cmake_ctest(
        self,
        work_dir: str,
        target_test: Optional[str],
        timeout: int,
    ) -> Tuple[int, str, Dict[str, str]]:
        cmake_bin = shutil.which("cmake")
        ctest_bin = shutil.which("ctest")
        if not cmake_bin or not ctest_bin:
            return -1, "cmake or ctest is not installed in the system PATH.", {}

        manifest = self.inspect_manifest(work_dir)
        new_cpp = os.path.join(work_dir, self.test_file_relpath)
        if os.path.isfile(new_cpp):
            self._inject_cmake_test_target(work_dir, manifest)

        build_dir = os.path.join(work_dir, "_factory_build")
        configure_cmd = [cmake_bin, "-S", work_dir, "-B", build_dir]
        for opt in manifest.get("test_options") or []:
            configure_cmd.append(f"-D{opt}=ON")
        for opt in manifest.get("disable_options") or []:
            configure_cmd.append(f"-D{opt}=OFF")
        if shutil.which("ccache"):
            configure_cmd.append("-DCMAKE_CXX_COMPILER_LAUNCHER=ccache")

        logs: List[str] = []
        try:
            cfg = subprocess.run(
                configure_cmd, cwd=work_dir, capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=timeout,
            )
            logs.append(cfg.stdout + "\n" + cfg.stderr)
            if cfg.returncode != 0:
                return cfg.returncode, "\n".join(logs), {}

            build_cmd = [cmake_bin, "--build", build_dir, "-j"]
            if target_test:
                build_cmd.extend(["--target", "factory_feature_test"])
            bld = subprocess.run(
                build_cmd, cwd=work_dir, capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=timeout,
            )
            logs.append(bld.stdout + "\n" + bld.stderr)
            if bld.returncode != 0:
                return bld.returncode, "\n".join(logs), {}

            fd, xml_path = tempfile.mkstemp(suffix=".xml", prefix="ctest_")
            os.close(fd)
            try:
                ctest_cmd = [ctest_bin, "--test-dir", build_dir, "--output-on-failure", f"--output-junit={xml_path}"]
                if target_test:
                    ctest_cmd.extend(["-R", "^factory_feature_test$"])
                tst = subprocess.run(
                    ctest_cmd, cwd=work_dir, capture_output=True, text=True,
                    encoding="utf-8", errors="replace", timeout=timeout,
                )
                logs.append(tst.stdout + "\n" + tst.stderr)
                return tst.returncode, "\n".join(logs), parse_junit_xml(xml_path)
            finally:
                try:
                    os.remove(xml_path)
                except OSError:
                    pass
        except subprocess.TimeoutExpired:
            return -1, f"CMake/CTest timed out after {timeout} seconds.\n" + "\n".join(logs), {}
        except Exception as e:
            return -1, f"CMake/CTest execution failed: {e}", {}

    def _run_gpp_direct(
        self,
        work_dir: str,
        target_test: Optional[str],
        timeout: int,
    ) -> Tuple[int, str, Dict[str, str]]:
        compiler = shutil.which("g++") or shutil.which("clang++")
        if not compiler:
            return -1, "C++ compiler (g++ or clang++) not found in PATH.", {}

        prod_sources: List[str] = []
        existing_tests: List[str] = []
        new_rel = self.test_file_relpath.replace("\\", "/")

        for root, dirs, files in os.walk(work_dir):
            dirs[:] = sorted([d for d in dirs if d not in DEFAULT_IGNORED_DIRS])
            for f in sorted(files):
                if not f.lower().endswith((".cpp", ".cc", ".cxx")):
                    continue
                full = os.path.join(root, f)
                rel = os.path.relpath(full, work_dir).replace("\\", "/")
                if rel == new_rel:
                    continue
                if self.is_test_file(rel, full):
                    existing_tests.append(full)
                else:
                    prod_sources.append(full)

        tests_to_run: List[Tuple[str, str]] = []
        if target_test:
            tests_to_run.append(("factory_feature_test", target_test))
        else:
            for idx, et in enumerate(existing_tests):
                tests_to_run.append((f"existing_cpp_{idx}", et))
            new_abs = os.path.join(work_dir, new_rel)
            if os.path.isfile(new_abs):
                tests_to_run.append(("factory_feature_test", new_abs))

        if not tests_to_run:
            return 0, "No existing C++ test suite found; baseline clean.", {}

        inc_flags = ["-I", work_dir]
        for inc in ("include", "src"):
            p = os.path.join(work_dir, inc)
            if os.path.isdir(p):
                inc_flags.extend(["-I", p])

        logs: List[str] = []
        results: Dict[str, str] = {}
        worst_code = 0

        for label, test_src in tests_to_run:
            bin_path = os.path.join(work_dir, f"_bin_{label}")
            cmd = [compiler, "-std=c++17", *inc_flags, *prod_sources, test_src, "-o", bin_path]
            try:
                comp = subprocess.run(
                    cmd, cwd=work_dir, capture_output=True, text=True,
                    encoding="utf-8", errors="replace", timeout=timeout,
                )
                logs.append(comp.stdout + "\n" + comp.stderr)
                if comp.returncode != 0:
                    results[f"{label}::compile"] = "failed"
                    worst_code = comp.returncode
                    continue

                run = subprocess.run(
                    [bin_path], cwd=work_dir, capture_output=True, text=True,
                    encoding="utf-8", errors="replace", timeout=timeout,
                )
                logs.append(run.stdout + "\n" + run.stderr)
                results[f"{label}::run"] = "passed" if run.returncode == 0 else "failed"
                if run.returncode != 0:
                    worst_code = run.returncode
            except subprocess.TimeoutExpired:
                return -1, f"C++ test timed out after {timeout} seconds.", results
            except Exception as e:
                return -1, f"C++ execution failed: {e}", results

        return worst_code, "\n".join(logs), results

    def is_real_failure(self, code: int, out: str) -> bool:
        if code <= 0:
            return False
        if "is not installed" in out or "not found in PATH" in out:
            return False
        if "timed out after" in out:
            return False
        return True

    def is_new_test(self, test_id: str) -> bool:
        return "factory_feature_test" in test_id or "test_feature_patch" in test_id

    def get_leakage_regexes(self) -> List[re.Pattern]:
        return [
            re.compile(r"\b[a-zA-Z0-9_\-./]+\.(?:cpp|cc|cxx|h|hpp|hh|inl):\d+(?::\d+)?\b", re.IGNORECASE),
        ]

    def get_prompt_context(self) -> Dict[str, str]:
        return {
            "language": "C++",
            "test_framework": "CTest / standalone main() or jsontest.h",
            "test_file": "tests/test_feature_patch.cpp",
            "coder_rules": (
                "Ensure all C++ headers, declarations (`.h`/`.hpp`), definitions (`.cpp`), "
                "namespaces, and CMake compatibility are 100% valid C++."
            ),
            "test_rules": (
                "Write a self-contained C++ test file (`tests/test_feature_patch.cpp`) with a standard "
                "`int main()` returning 0 on pass and non-zero (or throwing/asserting) on failure, "
                "including the repository public headers (e.g. `#include \"json/value.h\"`)."
            ),
        }

    def get_environment_metadata(self, repo_path: str, manifest: Dict[str, Any]) -> Dict[str, Any]:
        build_sys = manifest.get("build_system", "cmake")
        return {
            "cpp_standard": manifest.get("cpp_standard", "c++17"),
            "build_system": build_sys,
            "build_command": "cmake -B build && cmake --build build" if build_sys == "cmake" else "g++ -std=c++17",
            "test_command": "ctest --test-dir build --output-on-failure" if build_sys == "cmake" else "./_bin_factory_feature_test",
        }
