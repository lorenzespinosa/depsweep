"""Worktree isolation: the repo never moves, worktrees never outlive the
sweep, and --no-worktrees keeps the pre-0.3.0 in-place behaviour."""

from depsweep import gitops, node
from depsweep.models import (
    STATE_APPLIED,
    STATE_BRANCH_LEFT,
    STATE_CLEAN,
    STATE_ERROR,
    STATE_REVERTED,
    Upgrade,
    VerifyResult,
)
from depsweep.sweep import sweep_repo

from conftest import git, read_pkg

ALPHA = {
    "name": "alpha",
    "private": True,
    "scripts": {"test": "node --test"},
    "dependencies": {"left-pad": "^1.2.3"},
}
ALPHA_OUTDATED = {
    "left-pad": {"current": "1.2.3", "wanted": "1.4.0", "latest": "2.0.0"}
}


def test_no_worktrees_keeps_the_old_in_place_behaviour(repo_factory, npm_env):
    repo = repo_factory("inplace", package=ALPHA, outdated=ALPHA_OUTDATED)
    result = sweep_repo(repo, "inplace", "dep-sweep-test", use_worktrees=False)

    assert result.status == STATE_BRANCH_LEFT
    assert result.branch == "dep-sweep-test"
    assert result.verify == VerifyResult("test", True)

    # The pre-0.3.0 contract: the repo itself ends ON the sweep branch,
    # with the upgrade committed there, original branch untouched.
    assert gitops.current_branch(repo) == "dep-sweep-test"
    assert gitops.status_porcelain(repo) == ""
    assert read_pkg(repo)["dependencies"]["left-pad"] == "^1.4.0"
    assert '"^1.2.3"' in git(repo, "show", "main:package.json")
    assert gitops.worktree_list(repo) == [str(repo)]


def test_no_worktrees_failure_switches_back_and_deletes_branch(repo_factory):
    repo = repo_factory(
        "inplacebad", package=ALPHA, outdated=ALPHA_OUTDATED, exits={"test": 1}
    )
    result = sweep_repo(repo, "inplacebad", "dep-sweep-test", use_worktrees=False)
    assert result.status == STATE_REVERTED
    assert gitops.current_branch(repo) == "main"
    assert not gitops.branch_exists(repo, "dep-sweep-test")
    assert gitops.status_porcelain(repo) == ""
    assert read_pkg(repo)["dependencies"]["left-pad"] == "^1.2.3"


def test_default_leaves_repo_untouched_and_branch_kept(
    repo_factory, worktree_sandbox
):
    repo = repo_factory("kept", package=ALPHA, outdated=ALPHA_OUTDATED)
    result = sweep_repo(repo, "kept", "dep-sweep-test")
    assert result.status == STATE_BRANCH_LEFT
    # The repo never switched branches; the branch survives; no worktree.
    assert gitops.current_branch(repo) == "main"
    assert gitops.branch_exists(repo, "dep-sweep-test")
    assert gitops.worktree_list(repo) == [str(repo)]
    assert list(worktree_sandbox.iterdir()) == []


def test_worktree_removed_when_gate_fails(repo_factory, worktree_sandbox):
    repo = repo_factory(
        "gone", package=ALPHA, outdated=ALPHA_OUTDATED, exits={"test": 1}
    )
    result = sweep_repo(repo, "gone", "dep-sweep-test")
    assert result.status == STATE_REVERTED
    assert not gitops.branch_exists(repo, "dep-sweep-test")
    assert gitops.worktree_list(repo) == [str(repo)]
    assert list(worktree_sandbox.iterdir()) == []


def test_worktree_removed_when_bisection_lands_the_rest(
    repo_factory, worktree_sandbox
):
    package = dict(ALPHA, dependencies={"one": "^1.0.0", "two": "^1.0.0"})
    outdated = {
        name: {"current": "1.0.0", "wanted": "1.2.0", "latest": "2.0.0"}
        for name in ("one", "two")
    }
    repo = repo_factory("partly", package=package, outdated=outdated, breaks={"two": "1.2.0"})
    result = sweep_repo(repo, "partly", "dep-sweep-test")
    assert result.status == STATE_BRANCH_LEFT
    assert result.reverted == (Upgrade("two", "1.0.0", "1.2.0"),)
    assert gitops.worktree_list(repo) == [str(repo)]
    assert list(worktree_sandbox.iterdir()) == []
    assert '"two": "^1.0.0"' in git(repo, "show", "dep-sweep-test:package.json")


def test_worktree_removed_when_worktree_creation_would_leave_one_behind(
    repo_factory, worktree_sandbox
):
    # A clean repo with nothing outdated never creates a worktree at all.
    repo = repo_factory("nochange", package=ALPHA, outdated={})
    result = sweep_repo(repo, "nochange", "dep-sweep-test")
    assert result.status == STATE_CLEAN
    assert gitops.worktree_list(repo) == [str(repo)]
    assert list(worktree_sandbox.iterdir()) == []


def test_worktree_removed_on_unexpected_error(repo_factory, monkeypatch):
    repo = repo_factory("crashy", package=ALPHA, outdated=ALPHA_OUTDATED)

    def exploding_apply(*args, **kwargs):
        raise RuntimeError("mid-sweep crash")

    monkeypatch.setattr("depsweep.node._apply_with_bisect", exploding_apply)
    result = sweep_repo(repo, "crashy", "dep-sweep-test")

    # The crash became an error result, and every trace was cleaned up:
    # worktree gone, half-swept branch gone, repo still clean on main.
    assert result.status == STATE_ERROR
    assert "mid-sweep crash" in result.reason
    assert gitops.worktree_list(repo) == [str(repo)]
    assert not gitops.branch_exists(repo, "dep-sweep-test")
    assert gitops.current_branch(repo) == "main"
    assert gitops.status_porcelain(repo) == ""


def test_in_place_crash_switches_back_and_deletes_branch(repo_factory, monkeypatch):
    repo = repo_factory("crashy2", package=ALPHA, outdated=ALPHA_OUTDATED)

    def exploding_apply(*args, **kwargs):
        raise RuntimeError("in-place crash")

    monkeypatch.setattr("depsweep.node._apply_with_bisect", exploding_apply)
    result = sweep_repo(repo, "crashy2", "dep-sweep-test", use_worktrees=False)

    assert result.status == STATE_ERROR
    assert "in-place crash" in result.reason
    assert gitops.current_branch(repo) == "main"
    assert not gitops.branch_exists(repo, "dep-sweep-test")
    assert gitops.status_porcelain(repo) == ""


def test_commit_failure_in_worktree_keeps_nothing(repo_factory, worktree_sandbox):
    # No git identity configured: git refuses to commit. Uncommitted work
    # cannot outlive the worktree, so the sweep must say error and keep
    # neither branch nor worktree.
    repo = repo_factory("noident", package=ALPHA, outdated=ALPHA_OUTDATED)
    git(repo, "config", "user.email", "")
    git(repo, "config", "user.name", "")
    result = sweep_repo(repo, "noident", "dep-sweep-test")

    assert result.status == STATE_ERROR
    assert "git commit failed" in result.reason
    assert result.upgrades == ()  # nothing landed
    assert gitops.worktree_list(repo) == [str(repo)]
    assert not gitops.branch_exists(repo, "dep-sweep-test")
    assert gitops.current_branch(repo) == "main"
    assert gitops.status_porcelain(repo) == ""
    assert list(worktree_sandbox.iterdir()) == []


def test_worktree_named_after_the_repo(repo_factory, npm_env, monkeypatch):
    # The worktree keeps the repo's directory name (seen by the stubs and
    # by tools that key off the project name), under a private temp parent.
    seen_parents = []
    real = node._new_worktree_parent
    monkeypatch.setattr(
        node, "_new_worktree_parent", lambda: seen_parents.append(real()) or seen_parents[-1]
    )
    repo = repo_factory("named", package=ALPHA, outdated=ALPHA_OUTDATED)
    result = sweep_repo(repo, "named", "dep-sweep-test")
    assert result.status == STATE_BRANCH_LEFT
    # Every stub call was keyed by the repo's name, not a temp path.
    assert npm_env.calls("named")
    assert all(c.startswith("named: ") for c in npm_env.calls("named"))
    for parent in seen_parents:
        assert (parent / "named").exists() is False  # cleaned up


def test_pip_repos_are_swept_in_place_even_with_worktrees(
    pip_repo_factory, pip_env
):
    repo = pip_repo_factory(
        "inplacepy",
        requirements="requests>=2.0\n",
        installed={"requests": "2.31.0"},
        resolve={"requests": "2.32.0"},
    )
    result = sweep_repo(repo, "inplacepy", "dep-sweep-test")
    assert result.status == STATE_APPLIED
    assert gitops.worktree_list(repo) == [str(repo)]
    assert gitops.current_branch(repo) == "main"
