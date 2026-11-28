"""
TypeScript / JavaScript Language Adapter.
Supports single-package and monorepo workspaces (e.g., colinhacks/zod),
symbol & schema extraction, sandbox node_modules symlinking, and Vitest / Jest / Node native test runners.
"""

import glob
import json
import os
import re
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional, Set, Tuple

from .base import (
    BaseLanguageAdapter,
    DEFAULT_IGNORED_DIRS,
    SandboxPreparationError,
    TestResultMap,
    parse_junit_xml,
)


_TS_CLASS_RE = re.compile(
    r"^\s*(?:export\s+(?:default\s+)?)?(?:abstract\s+)?(class|interface)\s+([A-Za-z_$][A-Za-z0-9_$]*)"
    r"(?:<[^>]*>)?(?:\s+extends\s+([A-Za-z0-9_$.<>,\s]+?))?(?:\s+implements\s+[A-Za-z0-9_$.<>,\s]+)?\s*\{"
)
_TS_FN_RE = re.compile(
    r"^\s*(?:export\s+(?:default\s+)?)?(?:(async)\s+)?function\s+([A-Za-z_$][A-Za-z0-9_$]*)"
    r"\s*(?:<[^>]*>)?\(([^)]*)\)(?:\s*:\s*([^\{;]+))?"
)
_TS_ARROW_RE = re.compile(
    r"^\s*export\s+(?:const|let)\s+([A-Za-z_$][A-Za-z0-9_$]*)\s*(?::\s*[^=]+)?=\s*(?:(async)\s*)?\(([^)]*)\)\s*(?::\s*([^=]+))?\s*=>"
)
_TS_SCHEMA_CONST_RE = re.compile(
    r"^\s*export\s+const\s+([A-Za-z_$][A-Za-z0-9_$]*)\s*=\s*(z\.[A-Za-z0-9_$.]+\s*\([^;]*)"
)
_TS_TYPE_RE = re.compile(
    r"^\s*export\s+type\s+([A-Za-z_$][A-Za-z0-9_$]*)(?:<[^>]*>)?\s*="
)
_TS_METHOD_RE = re.compile(
    r"^\s*(?:(public|private|protected)\s+)?(?:(static)\s+)?(?:(async)\s+)?"
    r"(#?[A-Za-z_$][A-Za-z0-9_$]*)\s*(?:<[^>]*>)?\(([^)]*)\)(?:\s*:\s*([^\{;]+))?\s*\{"
)
_TS_IMPORT_RE = re.compile(r"""^\s*import\s+.*?\s+from\s+['"]([^'"]+)['"]""")

_TS_KEYWORDS_NOT_METHODS = {"if", "for", "while", "switch", "catch", "else", "constructor"}


def _strip_ts_comments(text: str) -> str:
    """Remove block and line comments without changing line count significantly."""
    text = re.sub(r"/\*[\s\S]*?\*/", "", text)
    return re.sub(r"(^|\s)//[^\n]*", r"\1", text)


def _find_ts_test_files(work_dir: str) -> List[str]:
    """Collect existing TS/JS test files inside work_dir."""
    found: List[str] = []
    adapter = TypeScriptAdapter()
    for root, dirs, files in os.walk(work_dir):
        dirs[:] = sorted([d for d in dirs if d not in DEFAULT_IGNORED_DIRS])
        for f in sorted(files):
            if not f.lower().endswith((".ts", ".tsx", ".mts", ".cts", ".js", ".jsx", ".mjs")):
                continue
            full = os.path.join(root, f)
            rel = os.path.relpath(full, work_dir)
            if adapter.is_test_file(rel, full):
                found.append(rel)
    return found


class TypeScriptAdapter(BaseLanguageAdapter):
    """Language adapter for TypeScript / JavaScript repositories (e.g., colinhacks/zod)."""

    language_id = "typescript"
    display_name = "TypeScript"
    source_extensions = (".ts", ".tsx", ".mts", ".cts", ".js", ".jsx", ".mjs", ".cjs")
    test_extensions = (".test.ts", ".spec.ts", ".test.tsx", ".spec.tsx", ".test.mts", ".test.js", ".spec.js", ".test.mjs")
    test_framework = "vitest"
    test_file_relpath = os.path.join("tests", "test_feature_patch.test.ts")
    root_test_filename = "test_patch.test.ts"

    @classmethod
    def detect_score(cls, repo_path: str) -> int:
        score = 0
        if os.path.isfile(os.path.join(repo_path, "package.json")):
            score += 30
        if os.path.isfile(os.path.join(repo_path, "tsconfig.json")):
            score += 20
        for cfg in ("vitest.config.ts", "vitest.config.mts", "vitest.config.js", "jest.config.js", "jest.config.ts"):
            if os.path.isfile(os.path.join(repo_path, cfg)):
                score += 10
                break

        ts_count, other_count = 0, 0
        for root, dirs, files in os.walk(repo_path):
            dirs[:] = [d for d in dirs if d not in DEFAULT_IGNORED_DIRS]
            for f in files:
                low = f.lower()
                if low.endswith((".ts", ".tsx", ".mts", ".cts")):
                    ts_count += 2
                elif low.endswith((".js", ".jsx", ".mjs")):
                    ts_count += 1
                elif low.endswith((".py", ".cpp", ".cc", ".cxx", ".h", ".hpp", ".go", ".rs")):
                    other_count += 1
            if ts_count + other_count >= 150:
                break

        if ts_count > 0:
            ratio = ts_count / max(1, ts_count + other_count)
            score += int(ratio * 45)
        return min(100, score)

    def inspect_manifest(self, repo_path: str) -> Dict[str, Any]:
        info: Dict[str, Any] = {
            "language": "typescript",
            "package_name": os.path.basename(os.path.abspath(repo_path)),
            "package_manager": "npm",
            "workspaces": [],
            "workspace_packages": [],
            "test_runner": "node_test",
            "test_script": "",
            "node_version": ">=18",
            "node_modules_present": os.path.isdir(os.path.join(repo_path, "node_modules")),
            "manifests": [],
        }

        pkg_path = os.path.join(repo_path, "package.json")
        if os.path.isfile(pkg_path):
            info["manifests"].append("package.json")
            try:
                with open(pkg_path, "r", encoding="utf-8") as f:
                    pkg = json.load(f)
                info["package_name"] = pkg.get("name") or info["package_name"]
                if pkg.get("packageManager"):
                    info["package_manager"] = pkg["packageManager"]
                elif os.path.isfile(os.path.join(repo_path, "pnpm-lock.yaml")):
                    info["package_manager"] = "pnpm"
                elif os.path.isfile(os.path.join(repo_path, "yarn.lock")):
                    info["package_manager"] = "yarn"

                scripts = pkg.get("scripts") or {}
                info["test_script"] = scripts.get("test", "")

                all_deps = {}
                all_deps.update(pkg.get("dependencies") or {})
                all_deps.update(pkg.get("devDependencies") or {})

                if "vitest" in all_deps or "vitest" in info["test_script"] or any(
                    os.path.isfile(os.path.join(repo_path, c))
                    for c in ("vitest.config.ts", "vitest.config.mts", "vitest.config.js")
                ):
                    info["test_runner"] = "vitest"
                elif "jest" in all_deps or "jest" in info["test_script"] or any(
                    os.path.isfile(os.path.join(repo_path, c))
                    for c in ("jest.config.ts", "jest.config.js", "jest.config.mjs")
                ):
                    info["test_runner"] = "jest"

                workspaces = pkg.get("workspaces") or []
                if isinstance(workspaces, dict):
                    workspaces = workspaces.get("packages") or []
                # Also support pnpm-workspace.yaml
                pnpm_ws = os.path.join(repo_path, "pnpm-workspace.yaml")
                # parse pnpm-workspace.yaml packages manually (simple yaml)
                if os.path.isfile(pnpm_ws):
                    try:
                        with open(pnpm_ws, "r", encoding="utf-8") as f:
                            for line in f:
                                line = line.strip()
                                if line.startswith("- "):
                                    w = line[2:].strip().strip('"').strip("'")
                                    if w and w not in workspaces:
                                        workspaces.append(w)
                    except Exception:
                        pass
                info["workspaces"] = list(workspaces)

                for pattern in workspaces:
                    for match in sorted(glob.glob(os.path.join(repo_path, pattern))):
                        sub_pkg = os.path.join(match, "package.json")
                        if os.path.isfile(sub_pkg):
                            try:
                                with open(sub_pkg, "r", encoding="utf-8") as sf:
                                    sdata = json.load(sf)
                                info["workspace_packages"].append({
                                    "name": sdata.get("name", os.path.basename(match)),
                                    "path": os.path.relpath(match, repo_path),
                                    "main": sdata.get("main", ""),
                                    "exports": sdata.get("exports", {}),
                                })
                            except Exception:
                                pass
            except Exception:
                pass

        for cfg in ("tsconfig.json", "vitest.config.ts", "vitest.config.mts", "jest.config.js"):
            if os.path.isfile(os.path.join(repo_path, cfg)):
                info["manifests"].append(cfg)

        return info

    def is_test_file(self, rel_path: str, full_path: str) -> bool:
        norm = rel_path.replace("\\", "/")
        parts = norm.split("/")
        fname = parts[-1].lower()

        if any(fname.endswith(ext) for ext in self.test_extensions):
            return True
        if any(part in ("__tests__", "tests", "test") for part in parts[:-1]):
            return True
        return False

    def scan_file(self, full_path: str, rel_path: str, summary: Dict[str, Any]) -> None:
        try:
            with open(full_path, "r", encoding="utf-8", errors="replace") as f:
                raw = f.read()
        except Exception:
            return

        cleaned = _strip_ts_comments(raw)
        lines = cleaned.splitlines()
        n = len(lines)
        i = 0

        while i < n:
            line = lines[i]

            imp_m = _TS_IMPORT_RE.match(line)
            if imp_m:
                summary["imports"].append(imp_m.group(1))
                i += 1
                continue

            cls_m = _TS_CLASS_RE.match(line)
            if cls_m:
                kind = cls_m.group(1)
                cls_name = cls_m.group(2)
                bases_raw = (cls_m.group(3) or "").strip()
                bases = [b.strip() for b in bases_raw.split(",") if b.strip()] if bases_raw else []

                # Collect body until matching brace depth returns to 0
                depth = line.count("{") - line.count("}")
                body_lines: List[str] = []
                j = i + 1
                while j < n and depth > 0:
                    cur = lines[j]
                    depth += cur.count("{") - cur.count("}")
                    if depth >= 1:
                        body_lines.append(cur)
                    j += 1

                public_methods: List[str] = []
                private_methods: List[str] = []
                all_methods: List[str] = []
                method_details: List[Dict[str, Any]] = []

                for bl in body_lines:
                    mm = _TS_METHOD_RE.match(bl)
                    if not mm:
                        continue
                    vis = mm.group(1) or "public"
                    is_async = bool(mm.group(3))
                    mname = mm.group(4)
                    if mname in _TS_KEYWORDS_NOT_METHODS:
                        continue
                    params = [p.strip() for p in mm.group(5).split(",") if p.strip()]
                    ret = (mm.group(6) or "").strip() or None
                    is_priv = (vis in ("private", "protected")) or mname.startswith("#") or mname.startswith("_")
                    prefix = "async " if is_async else ""
                    ret_s = f": {ret}" if ret else ""
                    sig = f"{prefix}{mname}({', '.join(params)}){ret_s}"
                    detail = {
                        "name": mname,
                        "signature": sig,
                        "params": params,
                        "return_type": ret,
                        "is_private": is_priv,
                        "is_async": is_async,
                    }
                    method_details.append(detail)
                    all_methods.append(mname)
                    if is_priv:
                        private_methods.append(sig)
                    else:
                        public_methods.append(sig)

                summary["classes"].append({
                    "file": rel_path,
                    "name": cls_name,
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

            fn_m = _TS_FN_RE.match(line) or _TS_ARROW_RE.match(line)
            if fn_m:
                if fn_m.re is _TS_FN_RE:
                    is_async = bool(fn_m.group(1))
                    fname = fn_m.group(2)
                    params_str = fn_m.group(3)
                    ret = (fn_m.group(4) or "").strip() or None
                else:
                    fname = fn_m.group(1)
                    is_async = bool(fn_m.group(2))
                    params_str = fn_m.group(3)
                    ret = (fn_m.group(4) or "").strip() or None

                params = [p.strip() for p in params_str.split(",") if p.strip()]
                prefix = "async " if is_async else ""
                ret_s = f": {ret}" if ret else ""
                sig = f"{prefix}{fname}({', '.join(params)}){ret_s}"
                summary["functions"].append({
                    "file": rel_path,
                    "name": fname,
                    "docstring": "",
                    "params": params,
                    "return_type": ret,
                    "signature": sig,
                    "is_private": fname.startswith("_"),
                    "is_async": is_async,
                    "decorators": [],
                })
                i += 1
                continue

            schema_m = _TS_SCHEMA_CONST_RE.match(line)
            if schema_m:
                sname = schema_m.group(1)
                summary["functions"].append({
                    "file": rel_path,
                    "name": sname,
                    "docstring": f"Exported Zod schema: {schema_m.group(2).strip()[:80]}",
                    "params": [],
                    "return_type": "ZodSchema",
                    "signature": f"const {sname}",
                    "is_private": False,
                    "is_async": False,
                    "decorators": [],
                })
                i += 1
                continue

            type_m = _TS_TYPE_RE.match(line)
            if type_m:
                tname = type_m.group(1)
                summary.setdefault("types", []).append({"file": rel_path, "name": tname})

            i += 1

    def select_targets(
        self,
        repo_path: str,
        scan_data: Dict[str, Any],
        max_files: int = 3,
        target_offset: int = 0,
    ) -> List[str]:
        files = list(scan_data.get("files") or [])
        if not files:
            return []

        file_scores: Dict[str, int] = {f: 0 for f in files}
        for cls in scan_data.get("classes") or []:
            f = cls.get("file", "")
            if f in file_scores:
                file_scores[f] += 3 + len(cls.get("public_methods") or [])
        for fn in scan_data.get("functions") or []:
            f = fn.get("file", "")
            if f in file_scores:
                file_scores[f] += 2

        for f in files:
            norm = f.replace("\\", "/").lower()
            if "/src/" in norm or norm.startswith("src/"):
                file_scores[f] += 4
            if "packages/zod/src/v4/" in norm:
                file_scores[f] += 5
            if any(skip in norm for skip in ("bench", "docs", "scripts", "example", ".config.")):
                file_scores[f] -= 6

        ranked = sorted(files, key=lambda f: (-file_scores.get(f, 0), f))
        if target_offset and len(ranked) > 1:
            shift = target_offset % len(ranked)
            ranked = ranked[shift:] + ranked[:shift]
        return ranked[:max(1, max_files)]

    def _link_node_modules_isolated(
        self,
        src_nm: str,
        dst_nm: str,
        work_dir: str,
        ws_map: Dict[str, str],
    ) -> None:
        """Stage dependencies while redirecting workspace packages into work_dir."""
        if not os.path.isdir(src_nm) or os.path.lexists(dst_nm):
            return
        if not ws_map:
            self._create_directory_link(src_nm, dst_nm)
            return

        try:
            os.makedirs(dst_nm, exist_ok=True)
        except OSError as exc:
            raise SandboxPreparationError(
                f"Unable to create dependency staging directory '{dst_nm}': {exc}"
            ) from exc
        scoped_dirs: Set[str] = {k.split("/", 1)[0] for k in ws_map if "/" in k}
        try:
            entries = os.listdir(src_nm)
        except OSError as exc:
            raise SandboxPreparationError(
                f"Unable to inspect installed dependencies in '{src_nm}': {exc}"
            ) from exc

        for entry in entries:
            src_entry = os.path.join(src_nm, entry)
            dst_entry = os.path.join(dst_nm, entry)
            if entry in ws_map:
                target_pkg = os.path.join(work_dir, ws_map[entry])
                if not os.path.isdir(target_pkg):
                    raise SandboxPreparationError(
                        f"Workspace dependency '{entry}' is missing from sandbox: '{target_pkg}'"
                    )
                self._create_directory_link(target_pkg, dst_entry)
                continue
            if entry in scoped_dirs and os.path.isdir(src_entry):
                sub_map = {k.split("/", 1)[1]: v for k, v in ws_map.items() if k.startswith(entry + "/")}
                self._link_node_modules_isolated(src_entry, dst_entry, work_dir, sub_map)
                continue
            if os.path.isdir(src_entry):
                self._create_directory_link(src_entry, dst_entry)
            else:
                try:
                    shutil.copy2(src_entry, dst_entry)
                except OSError as exc:
                    raise SandboxPreparationError(
                        f"Unable to stage dependency file '{src_entry}' at '{dst_entry}': {exc}"
                    ) from exc

    @staticmethod
    def _create_directory_link(source: str, destination: str) -> None:
        """Use Windows junctions (no elevation required) and POSIX symlinks for directories."""
        source = os.path.realpath(source)
        try:
            os.makedirs(os.path.dirname(destination), exist_ok=True)
            if os.name != "nt":
                os.symlink(source, destination, target_is_directory=True)
                return

            comspec = os.environ.get("COMSPEC", "cmd.exe")
            command = f'mklink /J "{destination}" "{source}"'
            launch_command = subprocess.list2cmdline([comspec, "/d", "/c"]) + " " + command
            result = subprocess.run(
                launch_command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            if result.returncode == 0 and os.path.isdir(destination):
                return
            details = (result.stdout + result.stderr).strip()
            raise OSError(
                f"mklink /J exited with code {result.returncode}"
                + (f": {details}" if details else "")
            )
        except OSError as exc:
            raise SandboxPreparationError(
                f"Unable to stage dependency directory '{source}' at '{destination}': {exc}"
            ) from exc

    def prepare_sandbox(self, repo_path: str, work_dir: str) -> None:
        """Stage installed dependencies and redirect workspace packages into patched copies."""
        manifest = self.inspect_manifest(repo_path)
        ws_map: Dict[str, str] = {
            pkg["name"]: pkg["path"]
            for pkg in (manifest.get("workspace_packages") or [])
            if pkg.get("name") and pkg.get("path")
        }

        candidates = [os.path.join(repo_path, "node_modules")]
        packages_dir = os.path.join(repo_path, "packages")
        if os.path.isdir(packages_dir):
            try:
                package_entries = os.listdir(packages_dir)
            except OSError as exc:
                raise SandboxPreparationError(
                    f"Unable to inspect workspace packages in '{packages_dir}': {exc}"
                ) from exc
            for entry in package_entries:
                nm = os.path.join(packages_dir, entry, "node_modules")
                if os.path.isdir(nm):
                    candidates.append(nm)

        for src_nm in candidates:
            if not os.path.isdir(src_nm):
                continue
            rel = os.path.relpath(src_nm, repo_path)
            dst_nm = os.path.join(work_dir, rel)
            self._link_node_modules_isolated(src_nm, dst_nm, work_dir, ws_map)

        for package_name, package_relpath in ws_map.items():
            alias = os.path.join(work_dir, "node_modules", *package_name.split("/"))
            target = os.path.join(work_dir, package_relpath)
            if not os.path.isdir(target):
                raise SandboxPreparationError(
                    f"Workspace package '{package_name}' is missing from sandbox: '{target}'"
                )
            if os.path.lexists(alias):
                if os.path.realpath(alias) != os.path.realpath(target):
                    raise SandboxPreparationError(
                        f"Workspace package '{package_name}' does not resolve to its sandbox copy: '{alias}'"
                    )
                continue
            self._create_directory_link(target, alias)

    @staticmethod
    def _package_runner_entrypoint(work_dir: str, name: str) -> Optional[str]:
        package_dir = os.path.join(work_dir, "node_modules", name)
        manifest_path = os.path.join(package_dir, "package.json")
        if not os.path.isfile(manifest_path):
            return None
        try:
            with open(manifest_path, "r", encoding="utf-8") as manifest_file:
                package = json.load(manifest_file)
        except (OSError, json.JSONDecodeError):
            return None

        binary = package.get("bin")
        if isinstance(binary, dict):
            binary = binary.get(name)
        if not isinstance(binary, str) or not binary.strip():
            return None
        entrypoint = os.path.join(package_dir, binary)
        if os.path.isfile(entrypoint) and os.path.splitext(entrypoint)[1].lower() in (
            ".js",
            ".mjs",
            ".cjs",
        ):
            return os.path.abspath(entrypoint)
        return None

    def _find_local_bin(self, work_dir: str, name: str) -> Optional[str]:
        if os.name == "nt":
            entrypoint = self._package_runner_entrypoint(work_dir, name)
            if entrypoint:
                return entrypoint
            bin_path = os.path.join(work_dir, "node_modules", ".bin", name + ".exe")
            if os.path.isfile(bin_path):
                return bin_path
            path_entry = shutil.which(name)
            if path_entry and os.path.splitext(path_entry)[1].lower() == ".exe":
                return path_entry
            return None

        bin_path = os.path.join(work_dir, "node_modules", ".bin", name)
        if os.path.isfile(bin_path):
            return bin_path
        return shutil.which(name)

    @staticmethod
    def _runner_command(runner: str, args: List[str]) -> List[str]:
        if os.name != "nt":
            return [runner, *args]
        extension = os.path.splitext(runner)[1].lower()
        if extension in (".js", ".mjs", ".cjs"):
            node = shutil.which("node")
            if not node:
                raise FileNotFoundError("Node.js is required to launch the JavaScript test runner.")
            return [node, runner, *args]
        if extension == ".exe":
            return [runner, *args]
        if not extension:
            return [runner, *args]
        raise FileNotFoundError(
            f"No supported Windows test-runner entry point was found for '{runner}'."
        )

    def run_tests(
        self,
        work_dir: str,
        target_test: Optional[str] = None,
        timeout: int = 60,
        **kwargs,
    ) -> Tuple[int, str, Dict[str, str]]:
        configured_runner = self.inspect_manifest(work_dir).get("test_runner")
        vitest_bin = self._find_local_bin(work_dir, "vitest")
        jest_bin = self._find_local_bin(work_dir, "jest")

        if configured_runner == "vitest":
            if vitest_bin and os.path.isdir(os.path.join(work_dir, "node_modules")):
                return self._run_vitest(work_dir, vitest_bin, target_test, timeout)
            return -1, "Vitest is configured but no local runnable Vitest entry point is available.", {}
        if configured_runner == "jest":
            if jest_bin and os.path.isdir(os.path.join(work_dir, "node_modules")):
                return self._run_jest(work_dir, jest_bin, target_test, timeout)
            return -1, "Jest is configured but no local runnable Jest entry point is available.", {}
        if vitest_bin and os.path.isdir(os.path.join(work_dir, "node_modules")):
            return self._run_vitest(work_dir, vitest_bin, target_test, timeout)
        if jest_bin and os.path.isdir(os.path.join(work_dir, "node_modules")):
            return self._run_jest(work_dir, jest_bin, target_test, timeout)
        return self._run_node_test(work_dir, target_test, timeout)

    def _run_vitest(
        self,
        work_dir: str,
        vitest_bin: str,
        target_test: Optional[str],
        timeout: int,
    ) -> Tuple[int, str, Dict[str, str]]:
        report_paths: List[str] = []

        def create_report_path() -> str:
            fd, path = tempfile.mkstemp(suffix=".xml", prefix="vitest_")
            os.close(fd)
            report_paths.append(path)
            return path

        active_report_path = create_report_path()
        runner_args = [
            "run",
            "--reporter=junit",
            f"--outputFile={active_report_path}",
        ]
        rel_t = None
        if target_test:
            rel_t = (
                os.path.relpath(target_test, work_dir)
                if os.path.isabs(target_test)
                else target_test
            ).replace("\\", "/")
            runner_args.append(rel_t)
        tmp_cfg = None
        try:
            cmd = self._runner_command(vitest_bin, runner_args)
            res = subprocess.run(
                cmd, cwd=work_dir, capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=timeout,
            )
            out = res.stdout + "\n" + res.stderr
            parsed = parse_junit_xml(
                active_report_path,
                vitest_compatibility=True,
            )

            # Monorepo Vitest configs using `projects` / custom `include` can skip root `tests/`
            no_tests_reported = (
                "No test files found" in out
                or (
                    parsed.report_present
                    and parsed.discovered_count == 0
                )
            )
            if target_test and res.returncode != 0 and no_tests_reported:
                tmp_cfg = os.path.join(work_dir, "vitest.factory.config.mjs")
                with open(tmp_cfg, "w", encoding="utf-8") as cf:
                    cf.write(f'export default {{ test: {{ include: [{json.dumps(rel_t)}] }} }};\n')
                active_report_path = create_report_path()
                retry_args = [
                    "run", "--config", tmp_cfg,
                    "--reporter=junit", f"--outputFile={active_report_path}", rel_t,
                ]
                retry_cmd = self._runner_command(vitest_bin, retry_args)
                res = subprocess.run(
                    retry_cmd, cwd=work_dir, capture_output=True, text=True,
                    encoding="utf-8", errors="replace", timeout=timeout,
                )
                out = res.stdout + "\n" + res.stderr

                parsed = parse_junit_xml(
                    active_report_path,
                    vitest_compatibility=True,
                )
            if parsed.report_warnings:
                out += "\n" + "\n".join(
                    f"JUnit warning: {warning}"
                    for warning in parsed.report_warnings
                )
            self._normalize_report_ids(parsed, work_dir)
            self._remove_file_level_results(
                active_report_path,
                work_dir,
                [target_test] if target_test else _find_ts_test_files(work_dir),
                parsed,
            )
            if (
                target_test is None
                and os.path.isfile(os.path.join(work_dir, self.test_file_relpath))
                and not any(self.is_new_test(k) for k in parsed)
            ):
                c2, o2, p2 = self._run_vitest(work_dir, vitest_bin, self.test_file_relpath, timeout)
                parsed_count = (
                    parsed.discovered_count
                    if parsed.discovered_count is not None
                    else len(parsed)
                )
                fallback_count = (
                    p2.discovered_count
                    if getattr(p2, "discovered_count", None) is not None
                    else len(p2)
                )
                parsed.update(p2)
                parsed.discovered_count = parsed_count + fallback_count
                parsed.report_present = (
                    parsed.report_present or getattr(p2, "report_present", False)
                )
                parsed.report_error = (
                    parsed.report_error or getattr(p2, "report_error", None)
                )
                parsed.report_warnings.extend(
                    getattr(p2, "report_warnings", [])
                )
                out = out + "\n" + o2
                code = res.returncode if res.returncode != 0 else c2
                return code, out, parsed

            return res.returncode, out, parsed
        except subprocess.TimeoutExpired:
            return -1, f"Vitest execution timed out after {timeout} seconds.", {}
        except Exception as e:
            return -1, f"Vitest execution failed: {e}", {}
        finally:
            if tmp_cfg and os.path.isfile(tmp_cfg):
                try:
                    os.remove(tmp_cfg)
                except OSError:
                    pass
            for report_path in report_paths:
                try:
                    os.remove(report_path)
                except OSError:
                    pass

    def _run_jest(
        self,
        work_dir: str,
        jest_bin: str,
        target_test: Optional[str],
        timeout: int,
    ) -> Tuple[int, str, Dict[str, str]]:
        fd, json_path = tempfile.mkstemp(suffix=".json", prefix="jest_")
        os.close(fd)
        runner_args = ["--json", f"--outputFile={json_path}"]
        if target_test:
            rel_t = os.path.relpath(target_test, work_dir) if os.path.isabs(target_test) else target_test
            runner_args.append(rel_t)
        try:
            cmd = self._runner_command(jest_bin, runner_args)
            res = subprocess.run(
                cmd, cwd=work_dir, capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=timeout,
            )
            out = res.stdout + "\n" + res.stderr
            return res.returncode, out, self.parse_jest_report(json_path, work_dir)
        except subprocess.TimeoutExpired:
            return -1, f"Jest execution timed out after {timeout} seconds.", {}
        except Exception as e:
            return -1, f"Jest execution failed: {e}", {}
        finally:
            try:
                os.remove(json_path)
            except OSError:
                pass

    @staticmethod
    def parse_jest_report(json_path: str, work_dir: str) -> TestResultMap:
        parsed = TestResultMap()
        try:
            with open(json_path, "r", encoding="utf-8") as report:
                data = json.load(report)
            parsed.report_present = True
            reported_count = data.get("numTotalTests")
            if isinstance(reported_count, int):
                parsed.discovered_count = reported_count
            for suite in data.get("testResults") or []:
                suite_name = str(suite.get("name") or "").strip()
                if not suite_name:
                    parsed.report_error = "Jest JSON report contains a suite without an identity."
                    continue
                relative_suite = os.path.relpath(suite_name, work_dir)
                for assertion in suite.get("assertionResults") or []:
                    assertion_name = str(
                        assertion.get("fullName") or assertion.get("title") or ""
                    ).strip()
                    if not assertion_name:
                        parsed.report_error = (
                            "Jest JSON report contains an assertion without a nonblank name."
                        )
                        continue
                    test_id = f"{relative_suite}::{assertion_name}"
                    raw_status = assertion.get("status", "failed")
                    status = {
                        "passed": "passed",
                        "failed": "failed",
                        "pending": "skipped",
                        "skipped": "skipped",
                        "todo": "skipped",
                    }.get(raw_status, "error")
                    parsed[test_id] = status
            if parsed.discovered_count is None:
                parsed.discovered_count = len(parsed)
            elif parsed.discovered_count != len(parsed):
                parsed.report_error = (
                    f"Jest JSON report declares {parsed.discovered_count} tests but "
                    f"contains {len(parsed)} assertion results."
                )
        except (OSError, json.JSONDecodeError, TypeError, AttributeError) as exc:
            parsed.report_error = f"Jest JSON report is missing or malformed: {exc}"
        return parsed

    def _run_node_test_single(
        self,
        work_dir: str,
        node_bin: str,
        test_files: List[str],
        prefix: str,
        timeout: int,
    ) -> Tuple[int, str, Dict[str, str]]:
        fd, xml_path = tempfile.mkstemp(suffix=".xml", prefix="nodetest_")
        os.close(fd)
        cmd = [
            node_bin, "--test", "--test-reporter=junit",
            f"--test-reporter-destination={xml_path}", *test_files,
        ]
        try:
            res = subprocess.run(
                cmd, cwd=work_dir, capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=timeout,
            )
            out = res.stdout + "\n" + res.stderr
            raw_results = parse_junit_xml(xml_path)
            self._remove_file_level_results(xml_path, work_dir, test_files, raw_results)

            prefixed = TestResultMap()
            prefixed.report_present = raw_results.report_present
            prefixed.report_error = raw_results.report_error
            prefixed.discovered_count = raw_results.discovered_count
            prefixed.no_tests_found = raw_results.no_tests_found
            for k, v in raw_results.items():
                name = k.split("::", 1)[-1] if "::" in k else k
                prefixed[f"{prefix}::{name}"] = v
            return res.returncode, out, prefixed
        except subprocess.TimeoutExpired:
            return -1, f"Node test execution timed out after {timeout} seconds.", {}
        except Exception as e:
            return -1, f"Node test execution failed: {e}", {}
        finally:
            try:
                os.remove(xml_path)
            except OSError:
                pass

    def _remove_file_level_results(
        self,
        report_path: str,
        work_dir: str,
        test_files: List[str],
        results: TestResultMap,
    ) -> None:
        file_level_ids = set()
        try:
            root = ET.parse(report_path).getroot()
            for case in root.iter("testcase"):
                name = (case.get("name") or "").strip()
                reported_file = (case.get("file") or "").strip()
                if not name:
                    continue
                is_file_level = (
                    self._same_test_file(name, reported_file, test_files, work_dir)
                    if reported_file
                    else any(
                        self._same_test_file(name, name, [test_file], work_dir)
                        for test_file in test_files
                    )
                )
                if not is_file_level:
                    continue
                classname = (case.get("classname") or "").strip()
                raw_id = f"{classname}::{name}" if classname else name
                file_level_ids.add(raw_id)
        except (ET.ParseError, OSError):
            return

        file_level_statuses = [
            results.get(file_level_id) for file_level_id in file_level_ids
        ]
        for file_level_id in file_level_ids:
            results.pop(file_level_id, None)
        if file_level_ids:
            count = results.discovered_count
            results.discovered_count = max(
                0, (len(results) + len(file_level_ids)) if count is None else count - len(file_level_ids)
            )
            if any(status != "passed" for status in file_level_statuses):
                results.report_error = (
                    "Test runner reported a test-file failure before individual test cases ran."
                )
            elif not results:
                results.no_tests_found = True

    @staticmethod
    def _normalize_report_ids(results: TestResultMap, work_dir: str) -> None:
        root = os.path.abspath(work_dir)
        root_key = os.path.normcase(root)
        normalized = {}
        for test_id, status in results.items():
            classname, separator, name = test_id.partition("::")
            if os.path.isabs(classname):
                try:
                    inside_work_dir = (
                        os.path.commonpath([root_key, os.path.normcase(classname)]) == root_key
                    )
                except ValueError:
                    inside_work_dir = False
                if inside_work_dir:
                    classname = os.path.relpath(classname, root).replace("\\", "/")
            normalized_id = f"{classname}::{name}" if separator else classname
            normalized[normalized_id] = status
        results.clear()
        results.update(normalized)

    @staticmethod
    def _same_test_file(name: str, reported_file: str, test_files: List[str], work_dir: str) -> bool:
        def resolved(path: str) -> str:
            return os.path.abspath(path if os.path.isabs(path) else os.path.join(work_dir, path))

        try:
            if not os.path.samefile(resolved(name), resolved(reported_file)):
                return False
            return any(os.path.samefile(resolved(reported_file), resolved(test_file)) for test_file in test_files)
        except OSError:
            normalized_name = os.path.normcase(resolved(name))
            normalized_file = os.path.normcase(resolved(reported_file))
            if normalized_name != normalized_file:
                return False
            return normalized_file in {
                os.path.normcase(resolved(test_file)) for test_file in test_files
            }

    def _run_node_test(
        self,
        work_dir: str,
        target_test: Optional[str],
        timeout: int,
    ) -> Tuple[int, str, Dict[str, str]]:
        node_bin = shutil.which("node")
        if not node_bin:
            return -1, "node is not installed in the system PATH.", {}

        if target_test:
            rel_t = os.path.relpath(target_test, work_dir) if os.path.isabs(target_test) else target_test
            return self._run_node_test_single(work_dir, node_bin, [rel_t], rel_t.replace("\\", "/"), timeout)

        all_tests = _find_ts_test_files(work_dir)
        if not all_tests:
            results = TestResultMap()
            results.discovered_count = 0
            results.no_tests_found = True
            return 0, "No existing TypeScript/JavaScript test suite found.", results

        new_rel = self.test_file_relpath.replace("\\", "/")
        existing = [t for t in all_tests if t.replace("\\", "/") != new_rel]
        has_new = any(t.replace("\\", "/") == new_rel for t in all_tests)

        combined_results = TestResultMap()
        combined_results.discovered_count = 0
        logs: List[str] = []
        worst_code = 0

        if existing:
            c1, o1, r1 = self._run_node_test_single(work_dir, node_bin, existing, "existing_tests", timeout)
            combined_results.update(r1)
            r1_count = getattr(r1, "discovered_count", None)
            combined_results.discovered_count += len(r1) if r1_count is None else r1_count
            combined_results.report_error = combined_results.report_error or getattr(r1, "report_error", None)
            combined_results.no_tests_found = combined_results.no_tests_found or getattr(
                r1, "no_tests_found", False
            )
            logs.append(o1)
            if c1 != 0:
                worst_code = c1

        if has_new:
            c2, o2, r2 = self._run_node_test_single(work_dir, node_bin, [new_rel], new_rel, timeout)
            combined_results.update(r2)
            r2_count = getattr(r2, "discovered_count", None)
            combined_results.discovered_count += len(r2) if r2_count is None else r2_count
            combined_results.report_error = combined_results.report_error or getattr(r2, "report_error", None)
            combined_results.no_tests_found = combined_results.no_tests_found or getattr(
                r2, "no_tests_found", False
            )
            logs.append(o2)
            if c2 != 0:
                worst_code = c2

        return worst_code, "\n".join(logs), combined_results

    def is_real_failure(self, code: int, out: str) -> bool:
        if code <= 0:
            return False
        if "node is not installed" in out:
            return False
        if "ERR_UNKNOWN_FILE_EXTENSION" in out:
            return False
        if "Cannot find package 'vitest'" in out or "Cannot find module 'vitest'" in out:
            return False
        return True

    def has_no_tests_evidence(self, exit_code: int, diagnostics: str, results: Dict[str, str]) -> bool:
        if getattr(results, "no_tests_found", False) and not results:
            return True
        lower = diagnostics.lower()
        return (
            not results
            and any(message in lower for message in (
                "no test files found",
                "no tests found",
                "no existing typescript/javascript test suite found",
            ))
        )

    def is_new_test(self, test_id: str) -> bool:
        norm = test_id.replace("\\", "/")
        return "test_feature_patch" in norm

    def get_leakage_regexes(self) -> List[re.Pattern]:
        return [
            re.compile(r"\b[a-zA-Z0-9_\-./]+\.(?:ts|tsx|js|jsx|mts|mjs):\d+(?::\d+)?\b", re.IGNORECASE),
            re.compile(r"\bthis\.#[a-zA-Z0-9_]+\b"),
        ]

    def get_prompt_context(self) -> Dict[str, str]:
        return {
            "language": "TypeScript",
            "test_framework": "vitest / node:test",
            "test_file": "tests/test_feature_patch.test.ts",
            "coder_rules": (
                "Ensure all TypeScript types, imports (ESM `.js` extensions if used by repo), "
                "and exports are 100% valid."
            ),
            "test_rules": (
                "Write a comprehensive TypeScript test file (`tests/test_feature_patch.test.ts`). "
                "Match the repository's test runner (Vitest `describe`/`it`/`expect` if vitest is configured, "
                "or `node:test` + `node:assert/strict` for zero-dependency TS repos)."
            ),
        }

    def get_environment_metadata(self, repo_path: str, manifest: Dict[str, Any]) -> Dict[str, Any]:
        runner = manifest.get("test_runner", "vitest")
        cmd = "npx vitest run tests/test_feature_patch.test.ts" if runner == "vitest" else "node --test tests/test_feature_patch.test.ts"
        return {
            "node_version": manifest.get("node_version", ">=18"),
            "package_manager": manifest.get("package_manager", "npm"),
            "test_runner": runner,
            "test_command": cmd,
        }
