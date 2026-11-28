"""
JavaScript/TypeScript package-manager detection, resolution, and dependency bootstrap.

Supports npm, pnpm, Yarn, and Nub. The declared package manager in ``package.json``
(``packageManager`` field, e.g. ``nub@0.8.3``) is the source of truth; lockfiles are
only used as a fallback when no declaration is present.

The module never rewrites ``package.json`` or regenerates lockfiles, and never
performs global machine modifications. All installation errors are surfaced as
actionable :class:`JSBootstrapError` exceptions carrying the package manager,
expected/actual version, executed command, exit code, and relevant output.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

_LOCKFILE_MANAGERS: dict[str, str] = {
    "package-lock.json": "npm",
    "pnpm-lock.yaml": "pnpm",
    "yarn.lock": "yarn",
}

# Manager-specific frozen/immutable install commands. The command list is used as the
# base; the caller appends any timeout handling. ``check`` is left to the caller so
# that stdout/stderr can be captured and reported.
_INSTALL_COMMANDS: dict[str, List[str]] = {
    "npm": ["npm", "ci"],
    "pnpm": ["pnpm", "install", "--frozen-lockfile"],
    "yarn": ["yarn", "install", "--frozen-lockfile"],
    "nub": ["nub", "install", "--frozen-lockfile"],
}

# Matches a full ``major.minor.patch`` version, optionally followed by a prerelease
# tag (e.g. ``0.8.3``, ``10.8.2``, ``0.8.3-rc.1``). Anything else is malformed.
_VERSION_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:-([A-Za-z0-9.\-]+))?$")
_PACKAGE_MANAGER_RE = re.compile(r"^([A-Za-z0-9@\-/]+)@(.+)$")


class JSBootstrapError(RuntimeError):
    """Raised when a JavaScript dependency bootstrap step fails."""

    def __init__(
        self,
        message: str,
        *,
        package_manager: Optional[str] = None,
        expected_version: Optional[str] = None,
        actual_version: Optional[str] = None,
        command: Optional[List[str]] = None,
        exit_code: Optional[int] = None,
        stdout: str = "",
        stderr: str = "",
        hint: Optional[str] = None,
    ):
        super().__init__(message)
        self.package_manager = package_manager
        self.expected_version = expected_version
        self.actual_version = actual_version
        self.command = command or []
        self.exit_code = exit_code
        self.stdout = stdout
        self.stderr = stderr
        self.hint = hint

    def __str__(self) -> str:
        parts: List[str] = [super().__str__()]
        if self.package_manager:
            parts.append(f"package_manager={self.package_manager}")
        if self.expected_version:
            parts.append(f"expected_version={self.expected_version}")
        if self.actual_version:
            parts.append(f"actual_version={self.actual_version}")
        if self.command:
            parts.append(f"command={' '.join(self.command)}")
        if self.exit_code is not None:
            parts.append(f"exit_code={self.exit_code}")
        if self.hint:
            parts.append(f"hint={self.hint}")
        return " | ".join(parts)


@dataclass
class PackageManagerInfo:
    """Resolved information about a project's package manager."""

    name: str
    version: Optional[str] = None
    executable: Optional[str] = None
    lockfile: Optional[str] = None
    declared: bool = False
    manifests: List[str] = field(default_factory=list)


def parse_version(version: Optional[str]) -> Optional[Tuple[int, int, int, Optional[str]]]:
    """Parse a ``major.minor.patch[-prerelease]`` version string.

    Returns ``(major, minor, patch, prerelease)`` or ``None`` when the string is
    malformed (not a fully-specified semver version). Prerelease is kept as the
    raw tag string (without the leading dash) so that ``0.8.3-rc.1`` differs from
    ``0.8.3``.
    """
    if not version:
        return None
    m = _VERSION_RE.match(version.strip())
    if not m:
        return None
    return int(m.group(1)), int(m.group(2)), int(m.group(3)), m.group(4)


def parse_package_manager_spec(spec: str) -> Tuple[str, Optional[str]]:
    """Parse a ``packageManager`` spec like ``nub@0.8.3`` into ``(name, version)``."""
    m = _PACKAGE_MANAGER_RE.match(spec.strip())
    if not m:
        return spec.strip(), None
    return m.group(1), m.group(2)


def version_matches(required: Optional[str], actual: Optional[str]) -> bool:
    """Return True only when ``actual`` exactly equals ``required``.

    The ``packageManager`` field declares an exact version (e.g. ``nub@0.8.3``),
    not a range. A missing, unreadable, or malformed version on either side fails
    validation rather than being silently accepted.
    """
    if not required or not actual:
        return False
    req = parse_version(required)
    act = parse_version(actual)
    if req is None or act is None:
        return False
    return req == act


def version_satisfies(required: Optional[str], actual: Optional[str]) -> bool:
    """Backward-compatible alias for :func:`version_matches` (exact-match semantics)."""
    return version_matches(required, actual)
def detect_package_manager(repo_path: str) -> Optional[PackageManagerInfo]:
    """Detect the package manager for a repository.

    Precedence:
      1. ``package.json`` ``packageManager`` field (source of truth).
      2. Lockfile presence (``package-lock.json`` -> npm, ``pnpm-lock.yaml`` -> pnpm,
         ``yarn.lock`` -> yarn).
    """
    pkg_path = os.path.join(repo_path, "package.json")
    declared_name: Optional[str] = None
    declared_version: Optional[str] = None
    manifests: List[str] = []

    if os.path.isfile(pkg_path):
        manifests.append("package.json")
        try:
            with open(pkg_path, "r", encoding="utf-8") as f:
                pkg = json.load(f)
            pm = pkg.get("packageManager")
            if isinstance(pm, str) and pm.strip():
                declared_name, declared_version = parse_package_manager_spec(pm)
        except (OSError, json.JSONDecodeError):
            pass

    # Fall back to lockfile detection when nothing is declared.
    lockfile_name: Optional[str] = None
    lockfile_path: Optional[str] = None
    if not declared_name:
        for lockfile, manager in _LOCKFILE_MANAGERS.items():
            candidate = os.path.join(repo_path, lockfile)
            if os.path.isfile(candidate):
                lockfile_name = manager
                lockfile_path = candidate
                break

    name = declared_name or lockfile_name
    if not name:
        return None

    return PackageManagerInfo(
        name=name.lower(),
        version=declared_version,
        declared=bool(declared_name),
        lockfile=lockfile_path,
        manifests=manifests,
    )


def resolve_executable(manager: str) -> Optional[str]:
    """Locate an installed package-manager executable on PATH.

    Handles Windows ``.cmd``/``.bat``/``.exe`` launchers and JavaScript entry points.
    """
    candidates = [manager]
    if os.name == "nt":
        candidates.extend([f"{manager}.cmd", f"{manager}.bat", f"{manager}.exe"])
    for candidate in candidates:
        path = shutil.which(candidate)
        if path:
            return path
    return None


def get_installed_version(executable: str) -> Optional[str]:
    """Return the installed version of a package manager, or None if unreadable."""
    try:
        res = subprocess.run(
            [executable, "--version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
        if res.returncode != 0:
            return None
        out = (res.stdout or res.stderr or "").strip()
        # Strip a leading "v" prefix (e.g. "v0.8.3" -> "0.8.3") before parsing.
        out = out[1:] if out.startswith("v") and len(out) > 1 and out[1].isdigit() else out
        # Take the first token that looks like a version.
        for token in out.split():
            if _VERSION_RE.match(token):
                return token
        return out.splitlines()[0].strip() if out.splitlines() else None
    except (OSError, subprocess.SubprocessError):
        return None


def install_command(manager: str, lockfile: Optional[str] = None) -> List[str]:
    """Return the dependency-install command for a manager.

    Uses frozen/immutable install mode when a lockfile is present, falling back to a
    non-lockfile-mutating install otherwise.
    """
    base = list(_INSTALL_COMMANDS.get(manager, [manager, "install"]))
    # npm's `ci` already requires package-lock.json; if absent, fall back to `npm install`.
    if manager == "npm" and base == ["npm", "ci"] and not lockfile:
        return ["npm", "install", "--no-audit", "--no-fund"]
    return base


class JSPackageManager:
    """High-level helper for detecting and installing JavaScript dependencies."""

    def __init__(self, repo_path: str, timeout: int = 120):
        self.repo_path = os.path.abspath(repo_path)
        self.timeout = timeout
        self.info: Optional[PackageManagerInfo] = None

    def detect(self) -> Optional[PackageManagerInfo]:
        self.info = detect_package_manager(self.repo_path)
        if self.info and not self.info.executable:
            self.info.executable = resolve_executable(self.info.name)
        return self.info

    def check(self) -> Tuple[bool, Optional[str], Optional[str]]:
        """Return ``(ok, reason, actual_version)`` whether the declared manager is installed and compatible."""
        if not self.info:
            self.detect()
        if not self.info:
            return False, "No package manager detected for repository.", None
        if not self.info.executable:
            return False, (
                f"Package manager '{self.info.name}' is declared but its executable "
                "was not found on PATH."
            ), None
        actual = get_installed_version(self.info.executable)
        if not version_matches(self.info.version, actual):
            return False, (
                f"Package manager '{self.info.name}' version mismatch: "
                f"declared {self.info.version}, installed "
                f"{actual or 'unknown'}."
            ), actual
        return True, None, actual

    def install(self) -> str:
        """Install dependencies and return a human-readable summary.

        Raises :class:`JSBootstrapError` on any failure.
        """
        if not self.info:
            self.detect()
        if not self.info:
            raise JSBootstrapError(
                "No package manager detected; cannot install JavaScript dependencies.",
                package_manager="<unknown>",
            )
        if not self.info.executable:
            raise JSBootstrapError(
                f"Package manager '{self.info.name}' executable not found on PATH.",
                package_manager=self.info.name,
                expected_version=self.info.version,
                hint=f"Install {self.info.name} (e.g. via Corepack or npm) and ensure it is on PATH.",
            )
        ok, reason, actual = self.check()
        if not ok:
            raise JSBootstrapError(
                reason or "Package-manager check failed.",
                package_manager=self.info.name,
                expected_version=self.info.version,
                actual_version=actual,
            )

        cmd = install_command(self.info.name, self.info.lockfile)
        try:
            res = subprocess.run(
                cmd,
                cwd=self.repo_path,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.timeout,
            )
        except subprocess.TimeoutExpired as exc:
            raise JSBootstrapError(
                f"Dependency installation timed out after {self.timeout} seconds.",
                package_manager=self.info.name,
                expected_version=self.info.version,
                command=cmd,
                exit_code=-1,
                stderr=str(exc),
                hint="Increase --timeout or check registry connectivity.",
            ) from exc
        except OSError as exc:
            raise JSBootstrapError(
                f"Failed to launch package manager '{self.info.name}'.",
                package_manager=self.info.name,
                expected_version=self.info.version,
                command=cmd,
                stderr=str(exc),
                hint=f"Ensure {self.info.name} is installed and executable.",
            ) from exc

        if res.returncode != 0:
            raise JSBootstrapError(
                f"Dependency installation failed with {self.info.name}.",
                package_manager=self.info.name,
                expected_version=self.info.version,
                actual_version=self.info.version,
                command=cmd,
                exit_code=res.returncode,
                stdout=res.stdout or "",
                stderr=res.stderr or "",
                hint=(
                    "Check registry connectivity, the declared package-manager version, "
                    "and repository lockfile compatibility."
                ),
            )

        node_modules = os.path.join(self.repo_path, "node_modules")
        if not os.path.isdir(node_modules):
            raise JSBootstrapError(
                f"{self.info.name} install reported success but node_modules was not created.",
                package_manager=self.info.name,
                command=cmd,
                exit_code=res.returncode,
                stdout=res.stdout or "",
                stderr=res.stderr or "",
            )

        return (
            f"Installed JS/TS dependencies via {self.info.name}"
            + (f" ({self.info.version})" if self.info.version else "")
        )
