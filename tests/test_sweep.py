"""Per-repo sweep state machine tests on real git repos with the offline npm stub.

Worktrees are the default, so most tests here assert the worktree contract:
the repo never leaves its original branch, the sweep branch (with its
commit) survives in the repo, and no worktree is left behind. The in-place
contract (``--no-worktrees``) is covered in test_worktrees.py.
"""

from depsweep import gitops
from depsweep.models import (
    STATE_BRANCH_LEFT,
    STATE_CLEAN,
    STATE_DRY_RUN,
    STATE_ERROR,
    STATE_REVERTED,
    STATE_SKIPPED,
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


def test_happy_path_leaves_branch_with_commit(
    repo_factory, npm_env, worktree_sandbox
):
    repo = repo_factory("alpha", package=ALPHA, outdated=ALPHA_OUTDATED)
    result = sweep_repo(repo, "alpha", "dep-sweep-test")

    assert result.status == STATE_BRANCH_LEFT
    assert result.branch == "dep-sweep-test"
    assert result.reason is None
    assert result.reverted == ()
    assert result.upgrades == (Upgrade("left-pad", "1.2.3", "1.4.0"),)
    assert result.verify == VerifyResult("test", True)
    assert not result.failed

    # The repo itself never moved and stays clean...
    assert gitops.current_branch(repo) == "main"
    assert gitops.status_porcelain(repo) == ""
    # ...the sweep branch carries the upgrade and a depsweep commit...
    assert gitops.branch_exists(repo, "dep-sweep-test")
    assert '"^1.4.0"' in git(repo, "show", "dep-sweep-test:package.json")
    assert git(repo, "log", "-1", "--pretty=%s", "dep-sweep-test").strip().startswith(
        "depsweep:"
    )
    # ...and no worktree is left behind.
    assert gitops.worktree_list(repo) == [str(repo)]
    assert list(worktree_sandbox.iterdir()) == []
    assert "install left-pad@1.4.0" in " ".join(npm_env.calls("alpha"))

    # The original branch still carries the old range: it was never touched.
    assert '"^1.2.3"' in git(repo, "show", "main:package.json")


def test_failing_test_reverts_and_names_the_package_by_elimination(
    repo_factory, npm_env, worktree_sandbox
):
    # One upgrade in the batch: the failing gate convicts it without any
    # search, and the whole (one-package) batch is reverted.
    repo = repo_factory(
        "beta", package=ALPHA, outdated=ALPHA_OUTDATED, exits={"test": 1}
    )
    result = sweep_repo(repo, "beta", "dep-sweep-test")

    assert result.status == STATE_REVERTED
    assert result.reason == "reverted: left-pad broke tests"
    assert result.verify == VerifyResult("test", False)
    assert result.reverted == (Upgrade("left-pad", "1.2.3", "1.4.0"),)
    assert result.failed

    # Repo untouched, no branch, no worktree.
    assert gitops.current_branch(repo) == "main"
    assert not gitops.branch_exists(repo, "dep-sweep-test")
    assert gitops.status_porcelain(repo) == ""
    assert gitops.worktree_list(repo) == [str(repo)]
    assert list(worktree_sandbox.iterdir()) == []
    assert read_pkg(repo)["dependencies"]["left-pad"] == "^1.2.3"


def test_install_failure_auto_reverts_and_names_the_package(
    repo_factory, worktree_sandbox
):
    repo = repo_factory(
        "gamma", package=ALPHA, outdated=ALPHA_OUTDATED, exits={"install": 1}
    )
    result = sweep_repo(repo, "gamma", "dep-sweep-test")
    assert result.status == STATE_REVERTED
    assert "install failed: left-pad@1.4.0" in result.reason
    assert result.verify is None  # the gate never ran
    assert gitops.current_branch(repo) == "main"
    assert not gitops.branch_exists(repo, "dep-sweep-test")
    assert gitops.status_porcelain(repo) == ""
    assert gitops.worktree_list(repo) == [str(repo)]
    assert list(worktree_sandbox.iterdir()) == []


def test_dirty_repo_is_skipped_without_any_npm_call(repo_factory, npm_env):
    repo = repo_factory("dirty", package=ALPHA, outdated=ALPHA_OUTDATED, dirty=True)
    result = sweep_repo(repo, "dirty", "dep-sweep-test")
    assert result.status == STATE_SKIPPED
    assert result.reason == "dirty worktree"
    assert npm_env.calls("dirty") == []
    assert gitops.current_branch(repo) == "main"
    assert gitops.worktree_list(repo) == [str(repo)]


def test_detached_head_is_skipped(repo_factory):
    repo = repo_factory("detached", package=ALPHA, outdated=ALPHA_OUTDATED)
    git(repo, "checkout", "-q", "--detach")
    result = sweep_repo(repo, "detached", "dep-sweep-test")
    assert result.status == STATE_SKIPPED
    assert "detached HEAD" in result.reason


def test_existing_branch_name_is_skipped_before_any_worktree(
    repo_factory, worktree_sandbox
):
    repo = repo_factory("clash", package=ALPHA, outdated=ALPHA_OUTDATED)
    git(repo, "branch", "dep-sweep-test")
    result = sweep_repo(repo, "clash", "dep-sweep-test")
    assert result.status == STATE_SKIPPED
    assert "already exists" in result.reason
    assert gitops.current_branch(repo) == "main"
    assert gitops.worktree_list(repo) == [str(repo)]
    assert list(worktree_sandbox.iterdir()) == []


def test_nothing_outdated_is_clean_no_branch(repo_factory, npm_env, worktree_sandbox):
    repo = repo_factory("quiet", package=ALPHA, outdated={})
    result = sweep_repo(repo, "quiet", "dep-sweep-test")
    assert result.status == STATE_CLEAN
    assert result.branch is None
    assert gitops.current_branch(repo) == "main"
    assert not gitops.branch_exists(repo, "dep-sweep-test")
    assert list(worktree_sandbox.iterdir()) == []
    assert all("install" not in call for call in npm_env.calls("quiet"))


def test_majors_and_pins_only_are_reported_without_installs(repo_factory, npm_env):
    repo = repo_factory(
        "lists",
        package={
            "name": "lists",
            "dependencies": {"big": "*", "kit": "3.1.0"},
        },
        outdated={
            "big": {"current": "1.9.0", "wanted": "2.5.0", "latest": "2.5.0"},
            "kit": {"current": "3.1.0", "wanted": "3.2.0", "latest": "4.0.0"},
        },
    )
    result = sweep_repo(repo, "lists", "dep-sweep-test")
    assert result.status == STATE_CLEAN
    assert [m.name for m in result.majors] == ["big"]
    assert [(h.name, h.reason) for h in result.held] == [("kit", "exact pin")]
    assert read_pkg(repo)["dependencies"] == {"big": "*", "kit": "3.1.0"}
    assert all("install" not in call for call in npm_env.calls("lists"))


def test_malformed_outdated_json_is_an_error(repo_factory, worktree_sandbox):
    repo = repo_factory("broken", package=ALPHA, outdated="{{{ nope")
    result = sweep_repo(repo, "broken", "dep-sweep-test")
    assert result.status == STATE_ERROR
    assert "invalid JSON" in result.reason
    assert gitops.current_branch(repo) == "main"
    assert list(worktree_sandbox.iterdir()) == []


def test_invalid_package_json_is_an_error(repo_factory):
    repo = repo_factory("nope", package={"name": "nope"})
    (repo / "package.json").write_text("not json at all")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "break package.json")
    result = sweep_repo(repo, "nope", "dep-sweep-test")
    assert result.status == STATE_ERROR
    assert "package.json" in result.reason


def test_verify_falls_back_to_build_script(repo_factory, npm_env):
    repo = repo_factory(
        "builder",
        package={
            "name": "builder",
            "scripts": {"build": "node build.js"},
            "dependencies": {"left-pad": "^1.2.3"},
        },
        outdated=ALPHA_OUTDATED,
    )
    result = sweep_repo(repo, "builder", "dep-sweep-test")
    assert result.status == STATE_BRANCH_LEFT
    assert result.verify == VerifyResult("build", True)
    assert "npm run build --silent" in " ".join(npm_env.calls("builder"))


def test_verify_falls_back_to_clean_install(repo_factory, npm_env):
    repo = repo_factory(
        "bare",
        package={"name": "bare", "dependencies": {"left-pad": "^1.2.3"}},
        outdated=ALPHA_OUTDATED,
    )
    result = sweep_repo(repo, "bare", "dep-sweep-test")
    assert result.status == STATE_BRANCH_LEFT
    assert result.verify == VerifyResult("install", True)
    assert "bare: npm install" in npm_env.calls("bare")


def test_dry_run_touches_nothing(repo_factory, npm_env, worktree_sandbox):
    repo = repo_factory("dry", package=ALPHA, outdated=ALPHA_OUTDATED)
    result = sweep_repo(repo, "dry", "dep-sweep-test", dry_run=True)

    assert result.status == STATE_DRY_RUN
    assert result.branch == "dep-sweep-test"
    assert result.upgrades == (Upgrade("left-pad", "1.2.3", "1.4.0"),)
    assert result.verify is None

    # Read-only: still on main, clean, package.json untouched, no install ran.
    assert gitops.current_branch(repo) == "main"
    assert not gitops.branch_exists(repo, "dep-sweep-test")
    assert gitops.status_porcelain(repo) == ""
    assert read_pkg(repo)["dependencies"]["left-pad"] == "^1.2.3"
    assert list(worktree_sandbox.iterdir()) == []
    calls = npm_env.calls("dry")
    assert calls == ["dry: npm outdated --json"]


def test_dry_run_on_dirty_repo_still_skips(repo_factory):
    repo = repo_factory("dry-dirty", package=ALPHA, outdated=ALPHA_OUTDATED, dirty=True)
    result = sweep_repo(repo, "dry-dirty", "dep-sweep-test", dry_run=True)
    assert result.status == STATE_SKIPPED
    assert result.reason == "dirty worktree"


def test_dry_run_with_only_lists_reports_no_branch(repo_factory):
    repo = repo_factory(
        "dry-lists",
        package={"name": "dry-lists", "dependencies": {"kit": "3.1.0"}},
        outdated={"kit": {"current": "3.1.0", "wanted": "3.2.0", "latest": "4.0.0"}},
    )
    result = sweep_repo(repo, "dry-lists", "dep-sweep-test", dry_run=True)
    assert result.status == STATE_DRY_RUN
    assert result.branch is None
    assert [h.name for h in result.held] == ["kit"]


def test_unexpected_exception_becomes_error_not_crash(repo_factory, monkeypatch):
    repo = repo_factory("boom", package=ALPHA, outdated=ALPHA_OUTDATED)

    monkeypatch.setattr(
        "depsweep.sweep._sweep", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("kaboom"))
    )
    result = sweep_repo(repo, "boom", "dep-sweep-test")
    assert result.status == STATE_ERROR
    assert "kaboom" in result.reason
