"""
Remote Repository Loader & Local Cache Manager.
Resolves local directories or remote Git repository URLs (e.g., GitHub)
with shallow cloning and commit metadata extraction.
"""

import os
import re
import subprocess
from typing import Dict, Optional, Tuple
from urllib.parse import urlparse

from .js_package_manager import JSBootstrapError, JSPackageManager


_URL_PREFIXES = ("https://", "http://", "git@", "ssh://", "git://", "file://")


def is_remote_repo_url(repo_input: str) -> bool:
    """Return True if repo_input looks like a remote Git URL rather than a local path."""
    if not repo_input:
        return False
    val = repo_input.strip()
    if val.startswith(_URL_PREFIXES):
        return True
    if val.endswith(".git") and not os.path.exists(val):
        return True
    return False


def extract_repo_slug(repo_url: str) -> str:
    """Derive a collision-free cache directory name (e.g. 'pallets__click') from a Git URL."""
    raw = repo_url.strip().rstrip("/")
    if raw.endswith(".git"):
        raw = raw[:-4]

    if raw.startswith("git@") and ":" in raw:
        path_part = raw.split(":", 1)[1]
    else:
        parsed = urlparse(raw)
        path_part = parsed.path.strip("/")

    segments = [re.sub(r"[^a-zA-Z0-9._-]", "_", seg) for seg in path_part.split("/") if seg]
    if len(segments) >= 2:
        return f"{segments[-2]}__{segments[-1]}"
    if segments:
        return segments[-1]
    return "cloned_repo"


def _run_git(args: list, cwd: Optional[str] = None) -> Optional[str]:
    """Run a git command safely and return stripped stdout, or None on failure."""
    try:
        res = subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if res.returncode == 0:
            return res.stdout.strip()
    except Exception:
        pass
    return None


def get_git_metadata(repo_path: str, source_url: Optional[str] = None) -> Dict[str, str]:
    """Extract repo name, origin URL, and HEAD commit hash from a local repo directory."""
    abs_path = os.path.abspath(repo_path)
    repo_name = os.path.basename(abs_path)
    if "__" in repo_name and source_url:
        repo_name = repo_name.split("__", 1)[1]

    remote_url = source_url or _run_git(["config", "--get", "remote.origin.url"], cwd=abs_path) or ""
    commit_hash = _run_git(["rev-parse", "HEAD"], cwd=abs_path) or ""

    return {
        "repo_name": repo_name,
        "repo_url": remote_url,
        "commit_hash": commit_hash,
    }


def resolve_repository(
    repo_input: str,
    cache_dir: str = "repos",
    depth: int = 1,
    update: bool = False,
) -> Tuple[str, Dict[str, str]]:
    """
    Resolve a local path or Git URL into a local directory path + metadata dict.
    Remote repositories are shallow-cloned into `<cache_dir>/<owner>__<repo>`.
    """
    if not repo_input or not str(repo_input).strip():
        raise ValueError("Repository path or URL cannot be empty.")

    raw_input = str(repo_input).strip()

    if not is_remote_repo_url(raw_input):
        local_path = os.path.abspath(raw_input)
        if not os.path.exists(local_path):
            raise FileNotFoundError(f"Target repository not found at: {local_path}")
        if not os.path.isdir(local_path):
            raise NotADirectoryError(f"Target repository path is not a directory: {local_path}")
        return local_path, get_git_metadata(local_path)

    slug = extract_repo_slug(raw_input)
    abs_cache = os.path.abspath(cache_dir)
    os.makedirs(abs_cache, exist_ok=True)
    target_path = os.path.join(abs_cache, slug)

    if os.path.exists(target_path) and os.path.isdir(os.path.join(target_path, ".git")):
        if update:
            subprocess.run(
                ["git", "pull", "--ff-only"],
                cwd=target_path,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
        return target_path, get_git_metadata(target_path, source_url=raw_input)

    cmd = ["git", "clone"]
    if depth and depth > 0:
        cmd.extend(["--depth", str(depth)])
    cmd.extend([raw_input, target_path])

    res = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if res.returncode != 0:
        err = (res.stderr or res.stdout or "git clone failed").strip()
        raise RuntimeError(f"Failed to clone '{raw_input}': {err}")

    return target_path, get_git_metadata(target_path, source_url=raw_input)


def bootstrap_environment(repo_path: str, language_id: str) -> str:
    """Best-effort dependency bootstrap for a cloned repository (only called when --setup-env is enabled)."""
    import shutil
    import sys

    abs_repo = os.path.abspath(repo_path)
    if language_id == "python":
        venv_dir = os.path.join(abs_repo, ".venv")
        venv_py = os.path.join(venv_dir, "bin", "python")
        if not os.path.isfile(venv_py):
            subprocess.run([sys.executable, "-m", "venv", venv_dir], check=True)
        subprocess.run([venv_py, "-m", "pip", "install", "-q", "pytest"], check=False)
        if os.path.isfile(os.path.join(abs_repo, "pyproject.toml")) or os.path.isfile(os.path.join(abs_repo, "setup.py")):
            subprocess.run([venv_py, "-m", "pip", "install", "-q", "-e", abs_repo], check=False)
        return f"Python virtualenv ready at {venv_dir}"

    if language_id == "typescript":
        return bootstrap_js_environment(abs_repo, timeout=180)

    if language_id == "cpp":
        cmake_bin = shutil.which("cmake")
        return f"CMake available at {cmake_bin}" if cmake_bin else "CMake missing in PATH"

    return f"No bootstrap steps configured for {language_id}"


def bootstrap_js_environment(repo_path: str, timeout: int = 180) -> str:
    """Bootstrap JavaScript/TypeScript dependencies for a repository.

    Uses the package manager declared in ``package.json`` (``packageManager`` field)
    as the source of truth, falling back to lockfile detection. Installs dependencies
    using the manager's frozen/immutable install mode where supported.

    Raises :class:`src.js_package_manager.JSBootstrapError` on any setup failure
    (missing executable, version mismatch, install failure) so callers can distinguish
    setup failures from code-generation or verification failures. A missing or
    incompatible package manager is never reported as successful dependency setup.
    """
    installer = JSPackageManager(repo_path, timeout=timeout)
    info = installer.detect()
    if not info:
        return "Using existing node_modules or Node native runner"

    if not info.executable:
        raise JSBootstrapError(
            f"Package manager '{info.name}' is declared in package.json but its "
            f"executable was not found on PATH."
            + (f" Declared version: {info.version}." if info.version else ""),
            package_manager=info.name,
            expected_version=info.version,
            hint=(
                f"Install {info.name} (e.g. via Corepack or npm) and ensure it is on "
                f"PATH before running dependency bootstrap."
            ),
        )

    ok, reason, actual = installer.check()
    if not ok:
        raise JSBootstrapError(
            reason or "Package-manager check failed.",
            package_manager=info.name,
            expected_version=info.version,
            actual_version=actual,
        )

    return installer.install()
