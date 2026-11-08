"""pnpm support: detection, both outdated JSON shapes, add-based upgrades."""

from depsweep import gitops, managers
from depsweep.models import (
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


def test_pnpm_lockfile_wins_over_npm(repo_factory):
    repo = repo_factory("peta", package=ALPHA, outdated=ALPHA_OUTDATED, pnpm=True)
    assert managers.detect(repo) == "pnpm"


def test_package_json_without_lockfile_is_npm(repo_factory):
    repo = repo_factory("plain", package=ALPHA, outdated=ALPHA_OUTDATED)
    assert managers.detect(repo) == "npm"


def test_pnpm_sweep_upgrades_via_add_and_leaves_branch(repo_factory, npm_env):
    repo = repo_factory("peta", package=ALPHA, outdated=ALPHA_OUTDATED, pnpm=True)
    result = sweep_repo(repo, "peta", "dep-sweep-test")

    assert result.status == STATE_BRANCH_LEFT
    assert result.manager == "pnpm"
    assert result.branch == "dep-sweep-test"
    assert result.upgrades == (Upgrade("left-pad", "1.2.3", "1.4.0"),)
    assert result.verify == VerifyResult("test", True)

    calls = " ".join(npm_env.calls("peta"))
    assert "peta: pnpm outdated --format json" in calls
    assert "pnpm add left-pad@1.4.0" in calls
    assert "pnpm run test --silent" in calls
    # The whole repo went through pnpm, never plain npm.
    assert not [c for c in npm_env.calls("peta") if c.startswith("peta: npm ")]

    # The sweep happened in a worktree: the repo stayed put, the branch
    # carries the upgrade, and the repo never left main.
    assert gitops.current_branch(repo) == "main"
    assert '"^1.4.0"' in git(repo, "show", "dep-sweep-test:package.json")
    assert gitops.worktree_list(repo) == [str(repo)]
    # The original branch keeps the old range.
    assert '"^1.2.3"' in git(repo, "show", "main:package.json")


def test_pnpm_outdated_array_shape_is_normalized(repo_factory, npm_env):
    # Older pnpm releases printed an array of objects; it must classify too.
    array_shape = [
        {"name": "left-pad", "current": "1.2.3", "wanted": "1.4.0", "latest": "2.0.0"}
    ]
    repo = repo_factory(
        "oldpnpm",
        package={"name": "oldpnpm", "dependencies": {"left-pad": "^1.2.3"}},
        outdated=array_shape,
        pnpm=True,
    )
    result = sweep_repo(repo, "oldpnpm", "dep-sweep-test")
    assert result.upgrades == (Upgrade("left-pad", "1.2.3", "1.4.0"),)
    assert result.status == STATE_BRANCH_LEFT


def test_pnpm_invalid_json_is_an_error(repo_factory):
    repo = repo_factory(
        "badpnpm", package=ALPHA, outdated="not json", pnpm=True
    )
    result = sweep_repo(repo, "badpnpm", "dep-sweep-test")
    assert result.status == STATE_ERROR
    assert "invalid JSON" in result.reason
    assert result.manager == "pnpm"
    assert gitops.current_branch(repo) == "main"


def test_pnpm_add_failure_reverts(repo_factory, npm_env):
    repo = repo_factory(
        "broken",
        package=ALPHA,
        outdated=ALPHA_OUTDATED,
        exits={"add": 1},
        pnpm=True,
    )
    result = sweep_repo(repo, "broken", "dep-sweep-test")
    assert result.status == STATE_REVERTED
    assert "install failed: left-pad@1.4.0" in result.reason
    assert gitops.current_branch(repo) == "main"
    assert not gitops.branch_exists(repo, "dep-sweep-test")
    assert gitops.worktree_list(repo) == [str(repo)]
    assert read_pkg(repo)["dependencies"]["left-pad"] == "^1.2.3"


def test_pnpm_verify_falls_back_to_bare_install(repo_factory, npm_env):
    repo = repo_factory(
        "bare",
        package={"name": "bare", "dependencies": {"left-pad": "^1.2.3"}},
        outdated=ALPHA_OUTDATED,
        pnpm=True,
    )
    result = sweep_repo(repo, "bare", "dep-sweep-test")
    assert result.status == STATE_BRANCH_LEFT
    assert result.verify == VerifyResult("install", True)
    assert "bare: pnpm install" in npm_env.calls("bare")


def test_pnpm_nothing_outdated_is_clean(repo_factory, npm_env):
    repo = repo_factory(
        "quiet", package={"name": "quiet"}, outdated={}, pnpm=True
    )
    result = sweep_repo(repo, "quiet", "dep-sweep-test")
    assert result.status == STATE_CLEAN
    assert result.manager == "pnpm"
    assert gitops.current_branch(repo) == "main"
