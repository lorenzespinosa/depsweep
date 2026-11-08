"""Bisection tests: the pure narrow() logic, then full sweeps with a planted
culprit on real git repos and the offline npm/pnpm stubs."""

import math

from depsweep import bisect
from depsweep import gitops
from depsweep.models import (
    STATE_BRANCH_LEFT,
    STATE_REVERTED,
    Upgrade,
    VerifyResult,
)
from depsweep.sweep import sweep_repo

from conftest import git


# --- the pure narrowing logic ---------------------------------------------------


def probe_factory(guilty):
    """A probe that fails exactly when the guilty item is in the subset.
    Counts every call so budgets can be asserted."""
    calls = []

    def probe(subset):
        calls.append(list(subset))
        return any(item == guilty for item in subset)

    return probe, calls


def test_narrow_finds_the_culprit_by_halving():
    items = ["a", "b", "c", "d"]
    probe, calls = probe_factory("c")
    assert bisect.narrow(items, budget=10, probe=probe) == "c"
    # Fewer probes than packages: halving, not one-by-one.
    assert len(calls) <= math.ceil(math.log2(len(items))) + 1


def test_narrow_first_or_last_item():
    for guilty in ("a", "d"):
        probe, _ = probe_factory(guilty)
        assert bisect.narrow(["a", "b", "c", "d"], budget=10, probe=probe) == guilty


def test_narrow_two_items_is_one_by_one():
    probe, calls = probe_factory("b")
    assert bisect.narrow(["a", "b"], budget=2, probe=probe) == "b"
    assert calls == [["a"], ["b"]]


def test_narrow_returns_none_when_nothing_fails_alone():
    # Interaction: every subset passes, only the whole batch fails.
    def probe(subset):
        return False

    assert bisect.narrow(["a", "b"], budget=5, probe=probe) is None


def test_narrow_stops_at_the_budget():
    items = ["a", "b", "c", "d", "e", "f", "g", "h"]
    probe, calls = probe_factory("h")
    # Not enough budget to reach h: give up, return None, no exception.
    assert bisect.narrow(items, budget=1, probe=probe) is None
    assert len(calls) == 1


def test_narrow_budget_runs_out_exactly_at_the_singleton():
    # N=2, budget=1: the first probe passes, clearing "a", and the budget
    # is spent before "b" can be probed alone. Give up rather than overspend.
    calls = []

    def passing_probe(subset):
        calls.append(list(subset))
        return False

    assert bisect.narrow(["a", "b"], budget=1, probe=passing_probe) is None
    assert calls == [["a"]]


def test_narrow_worst_case_fits_the_documented_bound():
    for size in (2, 3, 4, 5, 8, 9, 16):
        items = [f"p{i}" for i in range(size)]
        # The last item is the most expensive to reach: every halving step
        # passes, so the final singleton needs its own probe.
        probe, calls = probe_factory(items[-1])
        assert bisect.narrow(items, budget=math.ceil(math.log2(size)) + 1, probe=probe)
        assert len(calls) <= math.ceil(math.log2(size)) + 1


# --- planted-culprit sweeps ------------------------------------------------------

BATCH_PACKAGE = {
    "name": "batchy",
    "private": True,
    "scripts": {"test": "node --test"},
    "dependencies": {
        "aa-lib": "^1.0.0",
        "bb-lib": "^1.0.0",
        "cc-lib": "^1.0.0",
        "dd-lib": "^1.0.0",
    },
}
BATCH_OUTDATED = {
    name: {"current": "1.0.0", "wanted": "1.1.0", "latest": "2.0.0"}
    for name in ("aa-lib", "bb-lib", "cc-lib", "dd-lib")
}


def gate_runs(calls):
    return [c for c in calls if "run test" in c]


def test_culprit_is_isolated_reverted_and_the_rest_lands(
    repo_factory, npm_env, worktree_sandbox
):
    repo = repo_factory(
        "bisected",
        package=BATCH_PACKAGE,
        outdated=BATCH_OUTDATED,
        breaks={"cc-lib": "1.1.0"},
    )
    result = sweep_repo(repo, "bisected", "dep-sweep-test")

    # The batch failed as a whole, bisection convicted cc-lib alone, and
    # the other three landed on the sweep branch.
    assert result.status == STATE_BRANCH_LEFT
    assert result.reason == "reverted: cc-lib broke tests"
    assert result.reverted == (Upgrade("cc-lib", "1.0.0", "1.1.0"),)
    assert result.upgrades == tuple(
        Upgrade(name, "1.0.0", "1.1.0")
        for name in ("aa-lib", "bb-lib", "dd-lib")
    )
    # The verify on the result is the confirming run for the survivors.
    assert result.verify == VerifyResult("test", True)

    landed = git(repo, "show", "dep-sweep-test:package.json")
    assert '"^1.1.0"' in landed
    assert '"cc-lib": "^1.0.0"' in landed  # held back at the old range
    subject = git(repo, "log", "-1", "--pretty=%s", "dep-sweep-test")
    assert "hold back cc-lib" in subject
    # The repo itself is untouched and nothing is left behind.
    assert gitops.current_branch(repo) == "main"
    assert gitops.status_porcelain(repo) == ""
    assert gitops.worktree_list(repo) == [str(repo)]
    assert list(worktree_sandbox.iterdir()) == []

    # Budget: the failing batch run plus at most ceil(log2(4)) + 2 = 4 more
    # gate runs for the whole bisection.
    runs = gate_runs(npm_env.calls("bisected"))
    assert len(runs) <= 1 + math.ceil(math.log2(4)) + 2


def test_culprit_found_with_few_packages_goes_one_by_one(
    repo_factory, npm_env
):
    package = {
        "name": "tiny",
        "scripts": {"test": "node --test"},
        "dependencies": {"one": "^1.0.0", "two": "^1.0.0"},
    }
    outdated = {
        name: {"current": "1.0.0", "wanted": "1.2.0", "latest": "2.0.0"}
        for name in ("one", "two")
    }
    repo = repo_factory("tiny", package=package, outdated=outdated, breaks={"two": "1.2.0"})
    result = sweep_repo(repo, "tiny", "dep-sweep-test")
    assert result.status == STATE_BRANCH_LEFT
    assert result.reason == "reverted: two broke tests"
    assert [u.name for u in result.upgrades] == ["one"]
    # batch {one,two} fail; probe {one} pass; probe {two} fail; confirm {one}.
    assert len(gate_runs(npm_env.calls("tiny"))) == 4


def test_bisection_for_pnpm_repos_too(repo_factory, npm_env):
    package = dict(BATCH_PACKAGE, name="pnpmbis")
    repo = repo_factory(
        "pnpmbis",
        package=package,
        outdated=BATCH_OUTDATED,
        breaks={"aa-lib": "1.1.0"},
        pnpm=True,
    )
    result = sweep_repo(repo, "pnpmbis", "dep-sweep-test")
    assert result.manager == "pnpm"
    assert result.status == STATE_BRANCH_LEFT
    assert result.reason == "reverted: aa-lib broke tests"
    assert [u.name for u in result.upgrades] == ["bb-lib", "cc-lib", "dd-lib"]
    assert all(": pnpm " in c for c in npm_env.calls("pnpmbis"))


def test_two_guilty_packages_mean_whole_batch_revert(repo_factory, npm_env):
    # cc-lib and dd-lib both break: the survivors' confirming run fails,
    # so nothing may land and the reason says the batch went back whole.
    repo = repo_factory(
        "twobad",
        package=BATCH_PACKAGE,
        outdated=BATCH_OUTDATED,
        breaks={"cc-lib": "1.1.0", "dd-lib": "1.1.0"},
    )
    result = sweep_repo(repo, "twobad", "dep-sweep-test")
    assert result.status == STATE_REVERTED
    assert result.reason == (
        "could not isolate the failing upgrade; whole batch reverted"
    )
    assert result.failed
    assert result.upgrades == ()
    assert [u.name for u in result.reverted] == [
        "aa-lib",
        "bb-lib",
        "cc-lib",
        "dd-lib",
    ]
    assert not gitops.branch_exists(repo, "dep-sweep-test")
    assert gitops.current_branch(repo) == "main"
    assert gitops.status_porcelain(repo) == ""


def test_interaction_only_failure_reverts_whole_batch(repo_factory, npm_env):
    # x and y are each fine alone and only break together: no single
    # culprit exists, so bisection gives up and the whole batch goes back.
    package = {
        "name": "interact",
        "scripts": {"test": "node --test"},
        "dependencies": {"x": "^1.0.0", "y": "^1.0.0"},
    }
    outdated = {
        name: {"current": "1.0.0", "wanted": "1.2.0", "latest": "2.0.0"}
        for name in ("x", "y")
    }
    repo = repo_factory(
        "interact",
        package=package,
        outdated=outdated,
        breaks_together={"x": "1.2.0", "y": "1.2.0"},
    )
    result = sweep_repo(repo, "interact", "dep-sweep-test")
    assert result.status == STATE_REVERTED
    assert result.reason == (
        "could not isolate the failing upgrade; whole batch reverted"
    )
    assert [u.name for u in result.reverted] == ["x", "y"]
    assert not gitops.branch_exists(repo, "dep-sweep-test")
    runs = gate_runs(npm_env.calls("interact"))
    # batch, then {x} passes, then {y} passes: 3 runs, inside the bound.
    assert len(runs) == 3
    assert len(runs) <= 1 + math.ceil(math.log2(2)) + 2


def test_gate_already_red_before_the_sweep_reverts_whole_batch(
    repo_factory, npm_env
):
    # The test script fails no matter what is applied. Bisection convicts
    # the first probe, but the survivors' confirming run fails too, which
    # is exactly what stops a false "x broke tests" from landing the rest.
    package = {
        "name": "alwaysred",
        "scripts": {"test": "node --test"},
        "dependencies": {"x": "^1.0.0", "y": "^1.0.0"},
    }
    outdated = {
        name: {"current": "1.0.0", "wanted": "1.2.0", "latest": "2.0.0"}
        for name in ("x", "y")
    }
    repo = repo_factory(
        "alwaysred", package=package, outdated=outdated, exits={"test": 1}
    )
    result = sweep_repo(repo, "alwaysred", "dep-sweep-test")
    assert result.status == STATE_REVERTED
    assert result.reason == (
        "could not isolate the failing upgrade; whole batch reverted"
    )
    assert result.verify == VerifyResult("test", False)
    assert not gitops.branch_exists(repo, "dep-sweep-test")
    # batch + {x} + confirm {y} = 3 runs.
    assert len(gate_runs(npm_env.calls("alwaysred"))) == 3


def test_single_upgrade_failure_needs_no_bisection_runs(repo_factory, npm_env):
    repo = repo_factory(
        "lone",
        package={
            "name": "lone",
            "scripts": {"test": "node --test"},
            "dependencies": {"solo": "^1.0.0"},
        },
        outdated={"solo": {"current": "1.0.0", "wanted": "1.1.0", "latest": "2.0.0"}},
        breaks={"solo": "1.1.0"},
    )
    result = sweep_repo(repo, "lone", "dep-sweep-test")
    assert result.status == STATE_REVERTED
    assert result.reason == "reverted: solo broke tests"
    # Exactly one gate run: the batch run. No probes needed.
    assert len(gate_runs(npm_env.calls("lone"))) == 1


def test_culprit_in_second_half_is_found(repo_factory, npm_env):
    repo = repo_factory(
        "latebad",
        package=BATCH_PACKAGE,
        outdated=BATCH_OUTDATED,
        breaks={"dd-lib": "1.1.0"},
    )
    result = sweep_repo(repo, "latebad", "dep-sweep-test")
    assert result.status == STATE_BRANCH_LEFT
    assert result.reason == "reverted: dd-lib broke tests"
    assert [u.name for u in result.upgrades] == ["aa-lib", "bb-lib", "cc-lib"]
