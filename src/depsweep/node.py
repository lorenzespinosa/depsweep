"""The shared npm/pnpm sweep flow: both live in package.json repos.

Two ways to run, decided by the caller:

- Worktrees (the default): the sweep happens in a temporary git worktree on
  a new sweep branch. The repo itself never switches branches and stays
  exactly as it was; the branch (with its commit, when the gate passed)
  survives in the repo after the worktree is removed.
- In place (``--no-worktrees``): the old behaviour. depsweep switches the
  repo itself to a new sweep branch and switches back on failure.

When the gate fails for a batch of several upgrades, the batch is bisected
(see ``bisect``) to find the single package that broke it. The culprit is
reported as reverted, the rest of the batch lands after a confirming gate
run. When no single package is guilty, the whole batch is reverted and the
reason says so.
"""

from __future__ import annotations

import math
import shutil
import tempfile
from pathlib import Path

from . import bisect, gitops, npm
from .classify import build_plan
from .managers import NodeTool
from .models import (
    STATE_BRANCH_LEFT,
    STATE_CLEAN,
    STATE_DRY_RUN,
    STATE_ERROR,
    STATE_REVERTED,
    STATE_SKIPPED,
    Plan,
    RepoResult,
    Upgrade,
    VerifyResult,
)

_COULD_NOT_ISOLATE = "could not isolate the failing upgrade; whole batch reverted"


def run_node_sweep(
    repo: Path,
    rel_name: str,
    original_branch: str,
    branch_name: str,
    dry_run: bool,
    tool: NodeTool,
    use_worktrees: bool = True,
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
    lists = dict(majors=plan.majors, held=plan.held)
    if error is not None:
        return RepoResult(rel_name, STATE_ERROR, manager=tool.name, reason=error, **lists)

    if dry_run:
        return RepoResult(
            rel_name,
            STATE_DRY_RUN,
            manager=tool.name,
            branch=branch_name if plan.upgrades else None,
            upgrades=plan.upgrades,
            **lists,
        )

    if not plan.upgrades:
        # Nothing to apply: report the lists, leave the repo untouched.
        return RepoResult(rel_name, STATE_CLEAN, manager=tool.name, **lists)

    # Branch off the current branch; the original is never modified.
    if gitops.branch_exists(repo, branch_name):
        return RepoResult(
            rel_name,
            STATE_SKIPPED,
            manager=tool.name,
            reason=f"branch {branch_name} already exists",
            upgrades=plan.upgrades,
            **lists,
        )
    base_sha = gitops.head_sha(repo, f"refs/heads/{original_branch}")
    if base_sha is None:
        return RepoResult(
            rel_name,
            STATE_ERROR,
            manager=tool.name,
            reason=f"could not resolve branch {original_branch}",
            upgrades=plan.upgrades,
            **lists,
        )

    if use_worktrees:
        return _sweep_in_worktree(repo, rel_name, branch_name, base_sha, tool, plan)
    return _sweep_in_place(
        repo, rel_name, original_branch, branch_name, base_sha, tool, plan
    )


def _new_worktree_parent() -> Path:
    """A fresh private directory for one repo's worktree. Its own function
    so tests can point worktrees at a directory they watch."""
    return Path(tempfile.mkdtemp(prefix="depsweep-worktrees-"))


def _sweep_in_worktree(
    repo: Path,
    rel_name: str,
    branch_name: str,
    base_sha: str,
    tool: NodeTool,
    plan: Plan,
) -> RepoResult:
    lists = dict(majors=plan.majors, held=plan.held)
    parent = _new_worktree_parent()
    # The worktree directory keeps the repo's name so package-manager
    # tooling (and the offline stubs) see the same project name inside.
    work_dir = parent / repo.name
    added = keep_branch = False
    try:
        added = gitops.worktree_add(repo, work_dir, branch_name, base_sha).returncode == 0
        if not added:
            return RepoResult(
                rel_name,
                STATE_ERROR,
                manager=tool.name,
                reason="could not create a worktree for this repo",
                upgrades=plan.upgrades,
                **lists,
            )
        landed, verify, failure, reverted = _apply_with_bisect(
            work_dir, tool, plan, base_sha
        )
        committed = False
        if failure is None:
            committed = gitops.commit_all(work_dir, _commit_message(landed, reverted))
            keep_branch = committed
    finally:
        # Every exit path ends here: gate passed, gate failed, or a crash.
        # The worktree goes first so the branch is checked out nowhere and
        # can be deleted when it holds nothing worth keeping. The branch was
        # absent before the sweep (checked above), so any branch by that
        # name now is ours.
        if added and gitops.worktree_remove(repo, work_dir).returncode != 0:
            gitops.worktree_prune(repo)
        shutil.rmtree(parent, ignore_errors=True)
        if added and not keep_branch:
            # prune again now the directory is gone, so a failed
            # worktree_remove cannot leave the branch "checked out"
            gitops.worktree_prune(repo)
            if gitops.branch_exists(repo, branch_name):
                gitops.delete_branch(repo, branch_name)
        # when worktree_add failed, a branch by this name is not ours
        # (add fails precisely because someone else created it)

    if failure is not None:
        return RepoResult(
            rel_name,
            STATE_REVERTED,
            manager=tool.name,
            reason=failure,
            verify=verify,
            reverted=reverted,
            **lists,
        )
    if not committed:
        # Uncommitted changes cannot outlive the worktree, so nothing landed.
        return RepoResult(
            rel_name,
            STATE_ERROR,
            manager=tool.name,
            reason=(
                "git commit failed in the worktree, nothing kept "
                "(check git user.name and user.email, or use --no-worktrees)"
            ),
            verify=verify,
            reverted=tuple(plan.upgrades),
            **lists,
        )
    return RepoResult(
        rel_name,
        STATE_BRANCH_LEFT,
        manager=tool.name,
        branch=branch_name,
        reason=_landed_reason(True, reverted),
        verify=verify,
        upgrades=tuple(landed),
        reverted=reverted,
        **lists,
    )


def _sweep_in_place(
    repo: Path,
    rel_name: str,
    original_branch: str,
    branch_name: str,
    base_sha: str,
    tool: NodeTool,
    plan: Plan,
) -> RepoResult:
    """The pre-worktree behaviour: switch the repo itself to the sweep
    branch and switch back on failure."""
    lists = dict(majors=plan.majors, held=plan.held)
    if gitops.checkout_new_branch(repo, branch_name).returncode != 0:
        return RepoResult(
            rel_name,
            STATE_ERROR,
            manager=tool.name,
            reason=f"could not create branch {branch_name}",
            upgrades=plan.upgrades,
            **lists,
        )
    try:
        landed, verify, failure, reverted = _apply_with_bisect(
            repo, tool, plan, base_sha
        )
    except Exception:
        _revert(repo, original_branch, branch_name)
        raise
    if failure is not None:
        _revert(repo, original_branch, branch_name)
        return RepoResult(
            rel_name,
            STATE_REVERTED,
            manager=tool.name,
            reason=failure,
            verify=verify,
            reverted=reverted,
            **lists,
        )
    committed = gitops.commit_all(repo, _commit_message(landed, reverted))
    return RepoResult(
        rel_name,
        STATE_BRANCH_LEFT,
        manager=tool.name,
        branch=branch_name,
        reason=_landed_reason(committed, reverted),
        verify=verify,
        upgrades=tuple(landed),
        reverted=reverted,
        **lists,
    )


def _apply_with_bisect(
    work_dir: Path, tool: NodeTool, plan: Plan, base_sha: str
) -> tuple[list[Upgrade], VerifyResult | None, str | None, tuple[Upgrade, ...]]:
    """Apply the batch, verify, and on a verify failure bisect for the
    culprit. Returns ``(landed, verify, failure, reverted)``; ``failure`` is
    None exactly when ``landed`` is final and its gate run passed.

    Install failures are not bisected: the batch never fully applied, so
    there is nothing meaningful to search for.
    """
    upgrades = list(plan.upgrades)
    # Verify with the repo's own bar: test script, else build, else clean
    # install. Decided once, before anything changes package.json.
    scripts = npm.npm_scripts(npm.read_package_json(work_dir))
    if "test" in scripts:
        step = "test"
    elif "build" in scripts:
        step = "build"
    else:
        step = "install"

    def run_gate() -> VerifyResult:
        if step == "install":
            proc = tool.bare_install(work_dir)
        else:
            proc = tool.run(work_dir, step)
        return VerifyResult(step, proc.returncode == 0)

    # Baseline: the gate must be green before anything is upgraded, or a
    # later failure cannot honestly be attributed to the batch. test/build
    # gates need the base dependencies installed first.
    if step != "install":
        tool.bare_install(work_dir)  # base deps; its own failures surface as a red baseline
    baseline = run_gate()
    if not baseline.passed:
        return [], baseline, _COULD_NOT_ISOLATE, tuple(upgrades)

    def apply_batch(batch: list[Upgrade]) -> str | None:
        # One package at a time, so a failure names its package.
        for upgrade in batch:
            if tool.install(work_dir, upgrade.name, upgrade.wanted).returncode != 0:
                return f"install failed: {upgrade.name}@{upgrade.wanted}"
        return None

    failure = apply_batch(upgrades)
    if failure is not None:
        return [], None, failure, ()

    verify = run_gate()
    if verify.passed:
        return upgrades, verify, None, ()

    # The gate failed on the whole batch. The baseline run above proved the
    # gate green at the base, so with one upgrade no search is needed: it is
    # the culprit by elimination.
    if len(upgrades) == 1:
        return [], verify, f"reverted: {upgrades[0].name} broke tests", tuple(upgrades)

    reset_failed = False

    def probe(subset: list[Upgrade]) -> bool:
        # True means the gate fails with exactly this subset applied to the
        # untouched base. A package that cannot even install counts as
        # breaking. A reset that cannot restore the base poisons every
        # later probe, so the whole search is abandoned instead.
        nonlocal reset_failed
        if gitops.reset_to(work_dir, base_sha).returncode != 0:
            reset_failed = True
            return True
        if apply_batch(subset) is not None:
            return True
        return not run_gate().passed

    # narrow() spends at most ceil(log2(N)) + 1 gate runs; the confirming
    # run for the survivors below makes ceil(log2(N)) + 2 for the whole
    # bisection, on top of the batch run that failed.
    culprit = bisect.narrow(
        upgrades, budget=math.ceil(math.log2(len(upgrades))) + 1, probe=probe
    )
    if culprit is None or reset_failed:
        return [], verify, _COULD_NOT_ISOLATE, tuple(upgrades)

    survivors = [u for u in upgrades if u is not culprit]
    if gitops.reset_to(work_dir, base_sha).returncode != 0:
        # an unrestorable base means no survivor set can be trusted to land
        return [], verify, _COULD_NOT_ISOLATE, tuple(upgrades)
    if apply_batch(survivors) is not None:
        return [], verify, _COULD_NOT_ISOLATE, tuple(upgrades)
    confirmed = run_gate()
    if not confirmed.passed:
        # More than one package is guilty, or the gate was already red
        # before the sweep. Either way no part of the batch may land.
        return [], confirmed, _COULD_NOT_ISOLATE, tuple(upgrades)
    return survivors, confirmed, None, (culprit,)


def _commit_message(landed: list[Upgrade], reverted: tuple[Upgrade, ...]) -> str:
    message = f"depsweep: upgrade {len(landed)} in-range package(s)"
    if reverted:
        message += f", hold back {', '.join(u.name for u in reverted)}"
    return message


def _landed_reason(committed: bool, reverted: tuple[Upgrade, ...]) -> str | None:
    parts = []
    if reverted:
        parts.append(f"reverted: {', '.join(u.name for u in reverted)} broke tests")
    if not committed:
        parts.append("changes left uncommitted")
    return "; ".join(parts) or None


def _revert(repo: Path, original: str, branch: str) -> None:
    """Undo everything depsweep did in this repo, best effort."""
    gitops.discard_changes(repo)  # our own changes on our own branch
    if gitops.current_branch(repo) != original:
        gitops.checkout(repo, original)
    if gitops.branch_exists(repo, branch):
        gitops.delete_branch(repo, branch)
