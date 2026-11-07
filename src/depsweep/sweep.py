"""The per-repo sweep: check safety, then dispatch to the right manager."""

from __future__ import annotations

from pathlib import Path

from . import gitops, managers, node, pip
from .models import STATE_ERROR, STATE_SKIPPED, RepoResult


def sweep_repo(
    repo: Path, rel_name: str, branch_name: str, dry_run: bool = False
) -> RepoResult:
    """Sweep one repo. Never raises: unexpected errors become STATE_ERROR."""
    try:
        return _sweep(repo, rel_name, branch_name, dry_run)
    except Exception as exc:  # noqa: BLE001 - one broken repo must not stop the run
        return RepoResult(rel_name, STATE_ERROR, reason=f"unexpected error: {exc}")


def _sweep(
    repo: Path, rel_name: str, branch_name: str, dry_run: bool
) -> RepoResult:
    # Which manager owns this repo, decided up front so every result, even
    # an early skip, can say which one it would have been.
    manager = managers.detect(repo) or "unknown"

    # 1. Safety first, for every manager: never enter a dirty repo, never
    #    guess at detached HEAD.
    if gitops.status_porcelain(repo).strip():
        return RepoResult(rel_name, STATE_SKIPPED, manager=manager, reason="dirty worktree")
    original = gitops.current_branch(repo)
    if original is None:
        return RepoResult(
            rel_name,
            STATE_SKIPPED,
            manager=manager,
            reason="detached HEAD or unreadable branch",
        )

    # 2. Dispatch to the manager that owns this repo.
    if manager == managers.PIP:
        return pip.sweep_pip_repo(repo, rel_name, dry_run)
    if manager != "unknown":
        return node.run_node_sweep(
            repo, rel_name, original, branch_name, dry_run, managers.tool_for(manager)
        )
    return RepoResult(
        rel_name,
        STATE_ERROR,
        manager=manager,
        reason="no supported package manager marker",
    )
