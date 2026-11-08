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


def head_sha(repo: Path, ref: str = "HEAD") -> str | None:
    """The commit id a ref points at, or None when git cannot resolve it."""
    proc = _git(repo, "rev-parse", "--verify", "--quiet", ref)
    return proc.stdout.strip() or None


def checkout_new_branch(repo: Path, name: str) -> subprocess.CompletedProcess[str]:
    return _git(repo, "checkout", "-q", "-b", name)


def checkout(repo: Path, name: str) -> subprocess.CompletedProcess[str]:
    return _git(repo, "checkout", "-q", name)


def discard_changes(repo: Path) -> subprocess.CompletedProcess[str]:
    """``git reset --hard``. Only ever called on depsweep's own sweep branch."""
    return _git(repo, "reset", "-q", "--hard")


def reset_to(repo: Path, sha: str) -> subprocess.CompletedProcess[str]:
    """Move the checked-out sweep branch back to ``sha`` and wipe the worktree.

    Only ever called on depsweep's own sweep branch (in place) or inside a
    depsweep worktree, so the reset cannot lose anyone's work."""
    return _git(repo, "reset", "-q", "--hard", sha)


def delete_branch(repo: Path, name: str) -> subprocess.CompletedProcess[str]:
    return _git(repo, "branch", "-q", "-D", name)


def commit_all(repo: Path, message: str) -> bool:
    _git(repo, "add", "-A")
    return _git(repo, "commit", "-q", "-m", message).returncode == 0


def ls_files(repo: Path, *paths: str) -> str:
    """Paths git tracks under the given subtrees (empty when none)."""
    return _git(repo, "ls-files", "--", *paths).stdout


# --- worktrees -----------------------------------------------------------------
# The sweep runs in a temporary worktree so the repo itself never switches
# branches. The worktree shares the repo's refs, so a branch created and
# committed there survives the worktree being removed.


def worktree_add(
    repo: Path, path: Path, branch: str, base: str
) -> subprocess.CompletedProcess[str]:
    """Check out a new ``branch`` at ``base`` in a worktree at ``path``."""
    return _git(
        repo, "worktree", "add", "--quiet", "-b", branch, str(path), base
    )


def worktree_remove(repo: Path, path: Path) -> subprocess.CompletedProcess[str]:
    """Remove a worktree depsweep created. Always forced: a plain remove
    refuses while untracked build output (node_modules and friends) sits in
    the tree, and everything valuable has either been committed or is being
    thrown away on purpose."""
    return _git(repo, "worktree", "remove", "--force", str(path))


def worktree_prune(repo: Path) -> subprocess.CompletedProcess[str]:
    """Drop stale worktree metadata after a removal failed."""
    return _git(repo, "worktree", "prune")


def worktree_list(repo: Path) -> list[str]:
    """The repo's registered worktree paths, main worktree first."""
    proc = _git(repo, "worktree", "list", "--porcelain")
    return [
        line.removeprefix("worktree ").strip()
        for line in proc.stdout.splitlines()
        if line.startswith("worktree ")
    ]
