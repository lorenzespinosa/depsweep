"""The shared npm/pnpm sweep flow: both live in package.json repos."""

from __future__ import annotations

from pathlib import Path

from . import gitops, npm
from .classify import build_plan
from .managers import NodeTool
from .models import (
    STATE_BRANCH_LEFT,
    STATE_CLEAN,
    STATE_DRY_RUN,
    STATE_ERROR,
    STATE_REVERTED,
    STATE_SKIPPED,
    RepoResult,
    VerifyResult,
)


def run_node_sweep(
    repo: Path,
    rel_name: str,
    original_branch: str,
    branch_name: str,
    dry_run: bool,
    tool: NodeTool,
) -> RepoResult:
    """Sweep one package.json repo with npm or pnpm. The caller has checked
    the worktree is clean and named the branch it started on."""
    package_json = npm.read_package_json(repo)
    if not package_json:
        return RepoResult(
            rel_name, STATE_ERROR, manager=tool.name, reason="package.json missing or invalid"
        )

    outdated, error = tool.outdated(repo)
    plan = build_plan(package_json, outdated or {})
    carried = dict(upgrades=plan.upgrades, majors=plan.majors, held=plan.held)
    if error is not None:
        return RepoResult(rel_name, STATE_ERROR, manager=tool.name, reason=error, **carried)

    if dry_run:
        return RepoResult(
            rel_name,
            STATE_DRY_RUN,
            manager=tool.name,
            branch=branch_name if plan.upgrades else None,
            **carried,
        )

    if not plan.upgrades:
        # Nothing to apply: report the lists, leave the repo untouched.
        return RepoResult(rel_name, STATE_CLEAN, manager=tool.name, **carried)

    # Branch off the current branch; the original is never modified.
    if gitops.branch_exists(repo, branch_name):
        return RepoResult(
            rel_name,
            STATE_SKIPPED,
            manager=tool.name,
            reason=f"branch {branch_name} already exists",
            **carried,
        )
    if gitops.checkout_new_branch(repo, branch_name).returncode != 0:
        return RepoResult(
            rel_name,
            STATE_ERROR,
            manager=tool.name,
            reason=f"could not create branch {branch_name}",
            **carried,
        )

    # Apply in-range upgrades one package at a time.
    failure: str | None = None
    for upgrade in plan.upgrades:
        proc = tool.install(repo, upgrade.name, upgrade.wanted)
        if proc.returncode != 0:
            failure = f"install failed: {upgrade.name}@{upgrade.wanted}"
            break

    # Verify with the repo's own bar: test script, else build, else clean install.
    verify: VerifyResult | None = None
    if failure is None:
        scripts = npm.npm_scripts(package_json)
        if "test" in scripts:
            step, proc = "test", tool.run(repo, "test")
        elif "build" in scripts:
            step, proc = "build", tool.run(repo, "build")
        else:
            step, proc = "install", tool.bare_install(repo)
        verify = VerifyResult(step, proc.returncode == 0)
        if not verify.passed:
            failure = f"verify failed: {step}"

    # Any failure: back on the original branch, sweep branch deleted.
    if failure is not None:
        _revert(repo, original_branch, branch_name)
        return RepoResult(
            rel_name, STATE_REVERTED, manager=tool.name, reason=failure, verify=verify, **carried
        )

    # Success: leave the sweep branch, changes committed when git allows it.
    committed = gitops.commit_all(
        repo, f"depsweep: upgrade {len(plan.upgrades)} in-range package(s)"
    )
    return RepoResult(
        rel_name,
        STATE_BRANCH_LEFT,
        manager=tool.name,
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
