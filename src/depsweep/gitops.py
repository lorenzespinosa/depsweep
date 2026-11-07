"""Every git call depsweep makes, in one place. Nothing here pushes.

All commands run against a local repo directory; depsweep never adds a
remote argument, so a push is not possible through this module.
"""

from __future__ import annotations

import subprocess
from pathlib import Path


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=False
    )


def status_porcelain(repo: Path) -> str:
    return _git(repo, "status", "--porcelain").stdout


def current_branch(repo: Path) -> str | None:
    """The checked-out branch name, or None for detached HEAD / git failure."""
    proc = _git(repo, "branch", "--show-current")
    return proc.stdout.strip() or None


def branch_exists(repo: Path, name: str) -> bool:
    proc = _git(repo, "rev-parse", "--verify", "--quiet", f"refs/heads/{name}")
    return proc.returncode == 0


def checkout_new_branch(repo: Path, name: str) -> subprocess.CompletedProcess[str]:
    return _git(repo, "checkout", "-q", "-b", name)


def checkout(repo: Path, name: str) -> subprocess.CompletedProcess[str]:
    return _git(repo, "checkout", "-q", name)


def discard_changes(repo: Path) -> subprocess.CompletedProcess[str]:
    """``git reset --hard``. Only ever called on depsweep's own sweep branch."""
    return _git(repo, "reset", "-q", "--hard")


def delete_branch(repo: Path, name: str) -> subprocess.CompletedProcess[str]:
    return _git(repo, "branch", "-q", "-D", name)


def commit_all(repo: Path, message: str) -> bool:
    _git(repo, "add", "-A")
    return _git(repo, "commit", "-q", "-m", message).returncode == 0


def ls_files(repo: Path, *paths: str) -> str:
    """Paths git tracks under the given subtrees (empty when none)."""
    return _git(repo, "ls-files", "--", *paths).stdout
