"""
Regression tests for JavaScript/TypeScript package-manager compatibility
(src/js_package_manager.py) and repository-loader bootstrap (src/repo_loader.py).

All subprocess calls are mocked; no network access or real package installation is
required.
"""

import json
import os
import subprocess
import tempfile
import unittest
from unittest.mock import patch, MagicMock

from src.js_package_manager import (
    JSBootstrapError,
    JSPackageManager,
    PackageManagerInfo,
    detect_package_manager,
    get_installed_version,
    install_command,
    parse_package_manager_spec,
    resolve_executable,
    version_satisfies,
)
from src.repo_loader import bootstrap_js_environment


def _write_json(path, value):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(value, f)


class PackageManagerSpecTests(unittest.TestCase):
    def test_parse_package_manager_spec(self):
        self.assertEqual(parse_package_manager_spec("nub@0.8.3"), ("nub", "0.8.3"))
        self.assertEqual(parse_package_manager_spec("npm@10.8.2"), ("npm", "10.8.2"))
        self.assertEqual(parse_package_manager_spec("pnpm@9.15.9"), ("pnpm", "9.15.9"))
        self.assertEqual(parse_package_manager_spec("yarn@4.5.0"), ("yarn", "4.5.0"))
        self.assertEqual(parse_package_manager_spec("nub"), ("nub", None))

    def test_version_satisfies(self):
        # Exact-match semantics: declared version must equal installed version.
        self.assertTrue(version_satisfies("0.8.3", "0.8.3"))
        self.assertFalse(version_satisfies("0.8.3", "0.9.0"))  # newer -> mismatch
        self.assertFalse(version_satisfies("0.8.3", "1.0.0"))  # newer -> mismatch
        self.assertFalse(version_satisfies("0.8.3", "0.8.2"))  # older -> mismatch
        self.assertFalse(version_satisfies("0.8.3", None))
        self.assertFalse(version_satisfies(None, "anything"))
        # Prerelease differs from release.
        self.assertFalse(version_satisfies("0.8.3", "0.8.3-rc.1"))
        self.assertFalse(version_satisfies("0.8.3-rc.1", "0.8.3"))
        self.assertTrue(version_satisfies("0.8.3-rc.1", "0.8.3-rc.1"))
        # Malformed specifications are not silently accepted.
        self.assertFalse(version_satisfies("weird", "weird"))
        self.assertFalse(version_satisfies("0.8.3", "not-a-version"))

    def test_install_command(self):
        # npm with lockfile -> ci
        self.assertEqual(
            install_command("npm", lockfile="package-lock.json"),
            ["npm", "ci"],
        )
        # npm without lockfile -> install
        self.assertEqual(
            install_command("npm", lockfile=None),
            ["npm", "install", "--no-audit", "--no-fund"],
        )
        # pnpm with lockfile -> frozen-lockfile
        self.assertEqual(
            install_command("pnpm", lockfile="pnpm-lock.yaml"),
            ["pnpm", "install", "--frozen-lockfile"],
        )
        # yarn with lockfile -> frozen-lockfile
        self.assertEqual(
            install_command("yarn", lockfile="yarn.lock"),
            ["yarn", "install", "--frozen-lockfile"],
        )
        # nub supports --frozen-lockfile (verified against nub 0.8.3 CLI help)
        self.assertEqual(
            install_command("nub", lockfile="pnpm-lock.yaml"),
            ["nub", "install", "--frozen-lockfile"],
        )
        self.assertEqual(
            install_command("nub", lockfile=None),
            ["nub", "install", "--frozen-lockfile"],
        )


class PackageManagerDetectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _repo(self, package_json=None, lockfiles=None):
        lockfiles = lockfiles or []
        if package_json is not None:
            _write_json(os.path.join(self.tmp, "package.json"), package_json)
        for lf in lockfiles:
            open(os.path.join(self.tmp, lf), "w").close()
        return self.tmp

    def test_detect_declared_nub(self):
        repo = self._repo({"packageManager": "nub@0.8.3", "name": "zod"})
        info = detect_package_manager(repo)
        self.assertIsNotNone(info)
        self.assertEqual(info.name, "nub")
        self.assertEqual(info.version, "0.8.3")
        self.assertTrue(info.declared)
        self.assertIn("package.json", info.manifests)

    def test_detect_declared_pnpm(self):
        repo = self._repo({"packageManager": "pnpm@9.15.9"})
        info = detect_package_manager(repo)
        self.assertEqual(info.name, "pnpm")
        self.assertEqual(info.version, "9.15.9")
        self.assertTrue(info.declared)

    def test_detect_declared_yarn(self):
        repo = self._repo({"packageManager": "yarn@4.5.0"})
        info = detect_package_manager(repo)
        self.assertEqual(info.name, "yarn")
        self.assertEqual(info.version, "4.5.0")

    def test_detect_falls_back_to_lockfile(self):
        repo = self._repo(package_json={}, lockfiles=["pnpm-lock.yaml"])
        info = detect_package_manager(repo)
        self.assertEqual(info.name, "pnpm")
        self.assertFalse(info.declared)

    def test_detect_package_json_wins_over_lockfile(self):
        repo = self._repo(
            {"packageManager": "nub@0.8.3"},
            lockfiles=["pnpm-lock.yaml", "yarn.lock"],
        )
        info = detect_package_manager(repo)
        self.assertEqual(info.name, "nub")
        self.assertTrue(info.declared)

    def test_detect_none(self):
        repo = self._repo(package_json={})
        self.assertIsNone(detect_package_manager(repo))

    def test_detect_empty_repo(self):
        self.assertIsNone(detect_package_manager(self.tmp))


class ExecutableResolutionTests(unittest.TestCase):
    @patch("src.js_package_manager.shutil.which")
    def test_resolve_executable_found(self, which_mock):
        which_mock.return_value = "/usr/local/bin/nub"
        self.assertEqual(resolve_executable("nub"), "/usr/local/bin/nub")

    @patch("src.js_package_manager.shutil.which")
    def test_resolve_executable_not_found(self, which_mock):
        which_mock.return_value = None
        self.assertIsNone(resolve_executable("nub"))

    @patch("src.js_package_manager.shutil.which")
    def test_resolve_executable_windows_cmd(self, which_mock):
        # Windows tries name, name.cmd, name.bat, name.exe in order
        calls = []

        def fake_which(candidate):
            calls.append(candidate)
            if candidate == "nub.cmd":
                return "C:\\\\nub.cmd"
            return None

        which_mock.side_effect = fake_which
        with patch("src.js_package_manager.os.name", "nt"):
            result = resolve_executable("nub")
        self.assertEqual(result, "C:\\\\nub.cmd")
        self.assertIn("nub", calls)
        self.assertIn("nub.cmd", calls)


class VersionReadingTests(unittest.TestCase):
    @patch("src.js_package_manager.subprocess.run")
    def test_get_installed_version(self, run_mock):
        run_mock.return_value = MagicMock(
            returncode=0, stdout="0.8.3\n", stderr=""
        )
        self.assertEqual(get_installed_version("/bin/nub"), "0.8.3")

    @patch("src.js_package_manager.subprocess.run")
    def test_get_installed_version_failure(self, run_mock):
        run_mock.return_value = MagicMock(returncode=1, stdout="", stderr="err")
        self.assertIsNone(get_installed_version("/bin/nub"))

    @patch("src.js_package_manager.subprocess.run", side_effect=OSError("no such file"))
    def test_get_installed_version_os_error(self, run_mock):
        self.assertIsNone(get_installed_version("/bin/nub"))


class JSPackageManagerInstallTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        _write_json(
            os.path.join(self.tmp, "package.json"),
            {"packageManager": "nub@0.8.3", "name": "zod"},
        )

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _manager(self, executable="/bin/nub", version="0.8.3"):
        mgr = JSPackageManager(self.tmp, timeout=30)
        mgr.info = PackageManagerInfo(
            name="nub", version=version, executable=executable, declared=True
        )
        return mgr

    def _passing_manager(self, executable="/bin/nub", version="0.8.3"):
        """Manager whose check() passes (declared == installed version)."""
        mgr = self._manager(executable, version)
        self._version_patcher = patch(
            "src.js_package_manager.get_installed_version", return_value=version
        )
        self._version_patcher.start()
        return mgr

    def _teardown_version_patcher(self):
        if hasattr(self, "_version_patcher"):
            self._version_patcher.stop()

    def tearDown(self):
        import shutil
        self._teardown_version_patcher()
        shutil.rmtree(self.tmp, ignore_errors=True)

    @patch("src.js_package_manager.subprocess.run")
    @patch("src.js_package_manager.os.path.isdir")
    def test_install_success(self, isdir_mock, run_mock):
        isdir_mock.return_value = True  # node_modules exists after install
        run_mock.return_value = MagicMock(returncode=0, stdout="", stderr="")
        mgr = self._passing_manager()
        result = mgr.install()
        self.assertIn("nub", result)
        run_mock.assert_called_once()
        self.assertEqual(
            run_mock.call_args[0][0],
            ["nub", "install", "--frozen-lockfile"],
        )

    @patch("src.js_package_manager.subprocess.run")
    @patch("src.js_package_manager.os.path.isdir")
    def test_install_failure_is_reported_not_success(self, isdir_mock, run_mock):
        isdir_mock.return_value = True
        run_mock.return_value = MagicMock(
            returncode=1,
            stdout="",
            stderr="HTTP error: error decoding response body",
        )
        mgr = self._passing_manager()
        with self.assertRaises(JSBootstrapError) as ctx:
            mgr.install()
        exc = ctx.exception
        self.assertEqual(exc.package_manager, "nub")
        self.assertEqual(exc.exit_code, 1)
        self.assertIn("error decoding", exc.stderr)
        self.assertIsNotNone(exc.hint)
        # Must not be a success path.
        self.assertFalse(str(exc).startswith("Installed"))

    @patch("src.js_package_manager.subprocess.run", side_effect=subprocess.TimeoutExpired(["nub", "install"], 30))
    @patch("src.js_package_manager.os.path.isdir")
    def test_install_timeout_is_reported(self, isdir_mock, run_mock):
        isdir_mock.return_value = True
        mgr = self._passing_manager()
        with self.assertRaises(JSBootstrapError) as ctx:
            mgr.install()
        exc = ctx.exception
        self.assertEqual(exc.exit_code, -1)
        self.assertIn("timed out", str(exc))

    def test_install_missing_executable(self):
        mgr = self._manager(executable=None)
        with self.assertRaises(JSBootstrapError) as ctx:
            mgr.install()
        self.assertEqual(ctx.exception.package_manager, "nub")
        self.assertIsNotNone(ctx.exception.hint)

    def test_install_version_mismatch(self):
        # declared 1.0.0, installed 0.8.3 -> check() fails before install
        mgr = self._manager(version="1.0.0")
        with patch(
            "src.js_package_manager.get_installed_version", return_value="0.8.3"
        ):
            with self.assertRaises(JSBootstrapError) as ctx:
                mgr.install()
        self.assertIn("1.0.0", str(ctx.exception))
        self.assertIn("0.8.3", str(ctx.exception))


class BootstrapEnvironmentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        _write_json(
            os.path.join(self.tmp, "package.json"),
            {"packageManager": "nub@0.8.3", "name": "zod"},
        )

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    @patch("src.repo_loader.JSPackageManager")
    def test_bootstrap_detects_declared_nub_and_installs(self, installer_cls_mock):
        installer = installer_cls_mock.return_value
        installer.detect.return_value = PackageManagerInfo(
            name="nub", version="0.8.3", executable="/bin/nub", declared=True
        )
        installer.check.return_value = (True, None, "0.8.3")
        installer.install.return_value = "Installed JS/TS dependencies via nub (0.8.3)"
        result = bootstrap_js_environment(self.tmp, timeout=10)
        self.assertEqual(result, "Installed JS/TS dependencies via nub (0.8.3)")
        installer.install.assert_called_once()

    @patch("src.repo_loader.JSPackageManager")
    def test_bootstrap_missing_executable_raises(self, installer_cls_mock):
        installer = installer_cls_mock.return_value
        installer.detect.return_value = PackageManagerInfo(
            name="nub", version="0.8.3", executable=None, declared=True
        )
        with self.assertRaises(JSBootstrapError) as ctx:
            bootstrap_js_environment(self.tmp, timeout=10)
        self.assertEqual(ctx.exception.package_manager, "nub")
        self.assertIsNotNone(ctx.exception.hint)

    @patch("src.repo_loader.JSPackageManager")
    def test_bootstrap_version_mismatch_raises_with_actual(self, installer_cls_mock):
        installer = installer_cls_mock.return_value
        installer.detect.return_value = PackageManagerInfo(
            name="nub", version="1.0.0", executable="/bin/nub", declared=True
        )
        installer.check.return_value = (
            False,
            "Package manager 'nub' version mismatch: declared 1.0.0, installed 0.8.3.",
            "0.8.3",
        )
        with self.assertRaises(JSBootstrapError) as ctx:
            bootstrap_js_environment(self.tmp, timeout=10)
        self.assertEqual(ctx.exception.expected_version, "1.0.0")
        self.assertEqual(ctx.exception.actual_version, "0.8.3")

    @patch("src.repo_loader.JSPackageManager")
    def test_bootstrap_no_manager_returns_fallback(self, installer_cls_mock):
        installer_cls_mock.return_value.detect.return_value = None
        # No package.json manager at all.
        _write_json(os.path.join(self.tmp, "package.json"), {"name": "fixture"})
        result = bootstrap_js_environment(self.tmp, timeout=10)
        self.assertEqual(result, "Using existing node_modules or Node native runner")


class TypeScriptSandboxTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write_json(self, path, value):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(value, f)

    def test_prepare_sandbox_raises_when_vitest_configured_and_no_runner(self):
        from src.languages.typescript import TypeScriptAdapter
        from src.languages.base import SandboxPreparationError

        repo = os.path.join(self.tmp, "repo")
        work = os.path.join(self.tmp, "work")
        os.makedirs(work)
        self._write_json(
            os.path.join(repo, "package.json"),
            {
                "name": "zod",
                "packageManager": "nub@0.8.3",
                "devDependencies": {"vitest": "^4.1.5"},
            },
        )
        adapter = TypeScriptAdapter()
        with self.assertRaises(SandboxPreparationError) as ctx:
            adapter.prepare_sandbox(repo, work)
        self.assertIn("vitest", str(ctx.exception))
        self.assertIn("node_modules", str(ctx.exception))

    def test_prepare_sandbox_does_not_require_node_modules_without_runner(self):
        from src.languages.typescript import TypeScriptAdapter

        repo = os.path.join(self.tmp, "repo")
        work = os.path.join(self.tmp, "work")
        os.makedirs(work)
        self._write_json(
            os.path.join(repo, "package.json"),
            {"name": "fixture", "type": "module"},
        )
        adapter = TypeScriptAdapter()
        # No test runner configured, no node_modules -> should not raise.
        adapter.prepare_sandbox(repo, work)

    def test_generated_test_relpath_monorepo(self):
        from src.languages.typescript import TypeScriptAdapter

        repo = os.path.join(self.tmp, "repo")
        work = os.path.join(self.tmp, "work")
        os.makedirs(work)
        manifest = {
            "name": "zod",
            "packageManager": "nub@0.8.3",
            "workspaces": ["packages/*"],
            "devDependencies": {"vitest": "^4.1.5"},
        }
        self._write_json(os.path.join(repo, "package.json"), manifest)
        # The sandbox copy mirrors the repo manifest.
        self._write_json(os.path.join(work, "package.json"), manifest)
        self._write_json(
            os.path.join(repo, "packages", "zod", "package.json"),
            {"name": "zod"},
        )
        self._write_json(
            os.path.join(work, "packages", "zod", "package.json"),
            {"name": "zod"},
        )
        adapter = TypeScriptAdapter()
        rel = adapter.generated_test_relpath(work)
        self.assertEqual(
            rel,
            os.path.join("packages", "zod", "tests", "test_feature_patch.test.ts"),
        )

    def test_generated_test_relpath_default(self):
        from src.languages.typescript import TypeScriptAdapter

        adapter = TypeScriptAdapter()
        self.assertEqual(
            adapter.generated_test_relpath(None),
            os.path.join("tests", "test_feature_patch.test.ts"),
        )

    def test_is_new_test_recognizes_generated(self):
        from src.languages.typescript import TypeScriptAdapter

        adapter = TypeScriptAdapter()
        self.assertTrue(
            adapter.is_new_test(
                os.path.join("packages", "zod", "tests", "test_feature_patch.test.ts")
                + "::some test"
            )
        )
        self.assertTrue(adapter.is_new_test("tests/test_feature_patch.test.ts::some test"))
        self.assertFalse(
            adapter.is_new_test("packages/zod/src/v3/tests/basic.test.ts::existing")
        )


class TypeScriptRunnerResolutionTests(unittest.TestCase):
    """Local runner must satisfy a configured runner; a global runner is never a substitute."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.work = os.path.join(self.tmp, "work")
        os.makedirs(self.work)
        self.node_modules = os.path.join(self.work, "node_modules")
        os.makedirs(self.node_modules)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write_manifest(self, runner="vitest", package_manager="nub@0.8.3"):
        _write_json(
            os.path.join(self.work, "package.json"),
            {
                "name": "fixture",
                "packageManager": package_manager,
                "devDependencies": {runner: "^4.1.5"},
            },
        )

    def _local_bin(self, name="vitest"):
        bindir = os.path.join(self.node_modules, ".bin")
        os.makedirs(bindir, exist_ok=True)
        path = os.path.join(bindir, name + (".exe" if os.name == "nt" else ""))
        open(path, "w").close()
        return path

    def test_local_vitest_present(self):
        from src.languages.typescript import TypeScriptAdapter

        self._write_manifest()
        self._local_bin("vitest")
        adapter = TypeScriptAdapter()
        self.assertIsNotNone(adapter._find_local_bin(self.work, "vitest"))
        # Sandbox prep should not raise when the local runner exists.
        adapter.prepare_sandbox(self.work, self.work)

    def test_local_vitest_missing_global_present_is_not_used(self):
        from src.languages.typescript import TypeScriptAdapter
        from src.languages.base import SandboxPreparationError

        self._write_manifest()
        # Only a global vitest on PATH; nothing in sandbox node_modules/.bin.
        with patch("src.languages.typescript.shutil.which", return_value="/usr/bin/vitest"):
            adapter = TypeScriptAdapter()
            self.assertIsNone(adapter._find_local_bin(self.work, "vitest"))
            with self.assertRaises(SandboxPreparationError):
                adapter.prepare_sandbox(self.work, self.work)

    def test_node_modules_missing_raises_when_runner_configured(self):
        from src.languages.typescript import TypeScriptAdapter
        from src.languages.base import SandboxPreparationError

        self._write_manifest()
        # No node_modules at all.
        import shutil
        shutil.rmtree(self.node_modules)
        adapter = TypeScriptAdapter()
        self.assertIsNone(adapter._find_local_bin(self.work, "vitest"))
        with self.assertRaises(SandboxPreparationError):
            adapter.prepare_sandbox(self.work, self.work)

    @patch("src.languages.typescript.os.name", "nt")
    def test_windows_executable_resolution(self):
        from src.languages.typescript import TypeScriptAdapter

        self._write_manifest()
        self._local_bin("vitest")
        adapter = TypeScriptAdapter()
        self.assertTrue(
            adapter._find_local_bin(self.work, "vitest").endswith("vitest.exe")
        )


class TypeScriptWorkspacePlacementTests(unittest.TestCase):
    """Generated test must land in the relevant workspace package, not just workspaces[0]."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.work = os.path.join(self.tmp, "work")
        os.makedirs(self.work)
        manifest = {
            "name": "zod",
            "packageManager": "nub@0.8.3",
            "workspaces": ["packages/*"],
            "devDependencies": {"vitest": "^4.1.5"},
        }
        self._write_json(os.path.join(self.work, "package.json"), manifest)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write_json(self, path, value):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(value, f)

    def _add_workspace(self, pkg_dir):
        self._write_json(
            os.path.join(self.work, pkg_dir, "package.json"),
            {"name": os.path.basename(pkg_dir)},
        )

    def test_target_file_selects_containing_package(self):
        from src.languages.typescript import TypeScriptAdapter

        self._add_workspace(os.path.join("packages", "zod"))
        self._add_workspace(os.path.join("packages", "zod-v3"))
        adapter = TypeScriptAdapter()
        # A target file under packages/zod should select the zod package.
        rel = adapter.generated_test_relpath(
            self.work, target_file="packages/zod/src/v4/classic/schemas.ts"
        )
        self.assertEqual(
            rel,
            os.path.join("packages", "zod", "tests", "test_feature_patch.test.ts"),
        )
        # write_test_file honors target_file too.
        written = adapter.write_test_file(
            self.work, "export const x = 1;\n",
            target_file="packages/zod/src/v4/classic/schemas.ts",
        )
        self.assertTrue(
            written.endswith(os.path.join("packages", "zod", "tests", "test_feature_patch.test.ts"))
        )
        self.assertTrue(os.path.isfile(written))

    def test_fallback_uses_sorted_first_workspace_when_no_target(self):
        from src.languages.typescript import TypeScriptAdapter

        # Insertion order: zod-v3 first, zod second; sorted fallback must pick zod.
        self._add_workspace(os.path.join("packages", "zod-v3"))
        self._add_workspace(os.path.join("packages", "zod"))
        adapter = TypeScriptAdapter()
        rel = adapter.generated_test_relpath(self.work)
        self.assertEqual(
            rel,
            os.path.join("packages", "zod", "tests", "test_feature_patch.test.ts"),
        )

    def test_non_vitest_runner_uses_root_tests(self):
        from src.languages.typescript import TypeScriptAdapter

        self._write_json(
            os.path.join(self.work, "package.json"),
            {
                "name": "fixture",
                "packageManager": "npm@10.8.2",
                "workspaces": ["packages/*"],
                "devDependencies": {"jest": "^29.0.0"},
            },
        )
        self._add_workspace(os.path.join("packages", "zod"))
        adapter = TypeScriptAdapter()
        # test_runner is "node_test" (no vitest/jest configured) -> root tests/.
        self.assertEqual(
            adapter.generated_test_relpath(self.work),
            os.path.join("tests", "test_feature_patch.test.ts"),
        )


class EdgeCaseTests(unittest.TestCase):
    """Additional edge cases not covered by the main test classes."""

    def test_malformed_package_manager_specs(self):
        """Malformed packageManager declarations should not crash detection."""
        # Empty after @ - no match since .+ requires >=1 char
        self.assertEqual(parse_package_manager_spec("nub@"), ("nub@", None))
        # Scoped package names
        self.assertEqual(
            parse_package_manager_spec("@scope/manager@1.0.0"),
            ("@scope/manager", "1.0.0"),
        )
        # No version separator
        self.assertEqual(parse_package_manager_spec("nub"), ("nub", None))

    def test_missing_package_manager_with_no_lockfile_returns_none(self):
        """A repo with no packageManager and no lockfile should return None."""
        with tempfile.TemporaryDirectory() as tmp:
            _write_json(os.path.join(tmp, "package.json"), {"name": "fixture"})
            self.assertIsNone(detect_package_manager(tmp))

    def test_missing_package_manager_with_only_lockfile(self):
        """Only lockfile present -> fallback detection works."""
        with tempfile.TemporaryDirectory() as tmp:
            open(os.path.join(tmp, "yarn.lock"), "w").close()
            info = detect_package_manager(tmp)
            self.assertIsNotNone(info)
            self.assertEqual(info.name, "yarn")
            self.assertFalse(info.declared)

    def test_check_with_unreadable_version_returns_none_actual(self):
        """When --version is unreadable, actual_version should be None."""
        with tempfile.TemporaryDirectory() as tmp:
            _write_json(
                os.path.join(tmp, "package.json"),
                {"packageManager": "nub@0.8.3", "name": "zod"},
            )
            mgr = JSPackageManager(tmp, timeout=30)
            mgr.info = PackageManagerInfo(
                name="nub", version="0.8.3", executable="/bin/nub", declared=True
            )
            with patch(
                "src.js_package_manager.get_installed_version", return_value=None
            ):
                ok, reason, actual = mgr.check()
                self.assertFalse(ok)
                self.assertIsNone(actual)
                self.assertIn("unknown", reason)

    def test_resolve_executable_windows_variants(self):
        """Windows should try .cmd, .bat, and .exe variants in order."""
        calls = []

        def fake_which(candidate):
            calls.append(candidate)
            if candidate == "nub.bat":
                return "C:\\\\nub.bat"
            return None

        with patch("src.js_package_manager.shutil.which", side_effect=fake_which):
            with patch("src.js_package_manager.os.name", "nt"):
                result = resolve_executable("nub")
        # Returns on first match (nub.bat), so nub.exe is never tried.
        self.assertEqual(result, "C:\\\\nub.bat")
        self.assertIn("nub", calls)
        self.assertIn("nub.cmd", calls)
        self.assertIn("nub.bat", calls)

    def test_install_reports_exit_code_and_stderr_on_failure(self):
        """Failed install must carry exit_code and stderr in the exception."""
        with tempfile.TemporaryDirectory() as tmp:
            _write_json(
                os.path.join(tmp, "package.json"),
                {"packageManager": "nub@0.8.3", "name": "zod"},
            )
            mgr = JSPackageManager(tmp, timeout=30)
            mgr.info = PackageManagerInfo(
                name="nub", version="0.8.3", executable="/bin/nub", declared=True
            )
            with patch(
                "src.js_package_manager.get_installed_version", return_value="0.8.3"
            ):
                with patch(
                    "src.js_package_manager.subprocess.run",
                    return_value=MagicMock(
                        returncode=1,
                        stdout="",
                        stderr="HTTP error: error decoding response body",
                    ),
                ):
                    with patch("src.js_package_manager.os.path.isdir", return_value=True):
                        with self.assertRaises(JSBootstrapError) as ctx:
                            mgr.install()
                        exc = ctx.exception
                        self.assertEqual(exc.exit_code, 1)
                        self.assertIn("error decoding", exc.stderr)


if __name__ == "__main__":
    unittest.main()
