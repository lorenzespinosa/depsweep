"""The per-repo sweep: check safety, plan, apply on a branch, verify, revert."""

from __future__ import annotations

from pathlib import Path

from . import gitops, npm
from .classify import build_plan
from .models import (
    STATE_BRANCH_LEFT,
    STATE_CLEAN,
    STATE_DRY_RUN,
    STATE_ERROR,
    STATE_REVERTED,
    STATE_SKIPPED,
    Plan,
    RepoResult,
    VerifyResult,
)


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
    # 1. Safety first: never enter a dirty repo, never guess at detached HEAD.
    if gitops.status_porcelain(repo).strip():
        return RepoResult(rel_name, STATE_SKIPPED, reason="dirty worktree")
    original = gitops.current_branch(repo)
    if original is None:
        return RepoResult(
            rel_name, STATE_SKIPPED, reason="detached HEAD or unreadable branch"
        )

    package_json = npm.read_package_json(repo)
    if not package_json:
        return RepoResult(rel_name, STATE_ERROR, reason="package.json missing or invalid")

    outdated, error = npm.npm_outdated(repo)
    plan = build_plan(package_json, outdated or {})
    carried = dict(upgrades=plan.upgrades, majors=plan.majors, held=plan.held)
    if error is not None:
        return RepoResult(rel_name, STATE_ERROR, reason=error, **carried)

    if dry_run:
        return RepoResult(
            rel_name,
            STATE_DRY_RUN,
            branch=branch_name if plan.upgrades else None,
            **carried,
        )

    if not plan.upgrades:
        # Nothing to apply: report the lists, leave the repo untouched.
        return RepoResult(rel_name, STATE_CLEAN, **carried)

    # 2. Branch off the current branch; the original is never modified.
    if gitops.branch_exists(repo, branch_name):
        return RepoResult(
            rel_name,
            STATE_SKIPPED,
            reason=f"branch {branch_name} already exists",
            **carried,
        )
    if gitops.checkout_new_branch(repo, branch_name).returncode != 0:
        return RepoResult(
            rel_name,
            STATE_ERROR,
            reason=f"could not create branch {branch_name}",
            **carried,
        )

    # 3. Apply in-range upgrades one package at a time.
    failure: str | None = None
    for upgrade in plan.upgrades:
        proc = npm.npm_install(repo, upgrade.name, upgrade.wanted)
        if proc.returncode != 0:
            failure = f"install failed: {upgrade.name}@{upgrade.wanted}"
            break

    # 4. Verify with the repo's own bar: test script, else build, else clean install.
    verify: VerifyResult | None = None
    if failure is None:
        scripts = npm.npm_scripts(package_json)
        if "test" in scripts:
            step, proc = "test", npm.npm_run(repo, "test")
        elif "build" in scripts:
            step, proc = "build", npm.npm_run(repo, "build")
        else:
            step, proc = "install", npm.npm_bare_install(repo)
        verify = VerifyResult(step, proc.returncode == 0)
        if not verify.passed:
            failure = f"verify failed: {step}"

    # 5. Any failure: back on the original branch, sweep branch deleted.
    if failure is not None:
        _revert(repo, original, branch_name)
        return RepoResult(
            rel_name, STATE_REVERTED, reason=failure, verify=verify, **carried
        )

    # 6. Success: leave the sweep branch, changes committed when git allows it.
    committed = gitops.commit_all(
        repo, f"depsweep: upgrade {len(plan.upgrades)} in-range package(s)"
    )
    return RepoResult(
        rel_name,
        STATE_BRANCH_LEFT,
        branch=branch_name,
        reason=None if committed else "changes left uncommitted",
        verify=verify,
        **carried,
    )


def _revert(repo: Path, original: str, branch: str) -> None:
    """Undo everything depsweep did in this repo, best effort."""
    gitops.discard_changes(repo)  # our own changes on our own branch
    if gitops.current_branch(repo) != original:
        gitops.checkout(repo, original)
    if gitops.branch_exists(repo, branch):
        gitops.delete_branch(repo, branch)
