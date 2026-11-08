"""CLI tests: exit codes, flags, output on a fixture tree, entry point wiring.

The `python -m depsweep` subprocess path is exercised by the smoke script
(smoke/run_smoke.py prints the exact commands); here we drive main()
in process, which runs the identical code path minus the process spawn.
"""

import json
from pathlib import Path
from tomllib import load as tomllib_load

import pytest

from depsweep import gitops
from depsweep.cli import build_parser, default_branch_name, main

from conftest import read_pkg

ALPHA = {
    "name": "alpha",
    "private": True,
    "scripts": {"test": "node --test"},
    "dependencies": {"left-pad": "^1.2.3"},
}
ALPHA_OUTDATED = {
    "left-pad": {"current": "1.2.3", "wanted": "1.4.0", "latest": "2.0.0"}
}


def make_tree(repo_factory):
    repo_factory("alpha", package=ALPHA, outdated=ALPHA_OUTDATED)
    repo_factory("beta", package={"name": "beta"}, outdated={})
    return repo_factory.root


# --- usage errors -----------------------------------------------------------


def test_missing_root_argument_exits_2():
    with pytest.raises(SystemExit) as exc:
        main([])
    assert exc.value.code == 2


def test_bad_root_path_exits_2(tmp_path):
    with pytest.raises(SystemExit) as exc:
        main([str(tmp_path / "no-such-dir")])
    assert exc.value.code == 2


def test_root_that_is_a_file_exits_2(tmp_path):
    target = tmp_path / "a-file"
    target.write_text("hi")
    with pytest.raises(SystemExit) as exc:
        main([str(target)])
    assert exc.value.code == 2


def test_negative_max_depth_exits_2(tmp_path):
    with pytest.raises(SystemExit) as exc:
        main([str(tmp_path), "--max-depth", "-1"])
    assert exc.value.code == 2


def test_html_flag_with_missing_directory_exits_2(tmp_path):
    with pytest.raises(SystemExit) as exc:
        main([str(tmp_path), "--html", str(tmp_path / "no-such-dir" / "r.html")])
    assert exc.value.code == 2


def test_default_branch_name_has_date_shape():
    assert default_branch_name().startswith("dep-sweep-2")
    assert len(default_branch_name()) == len("dep-sweep-YYYYMMDD")


def test_worktrees_default_on_and_flag_turns_them_off():
    args = build_parser().parse_args(["/tmp/x"])
    assert args.use_worktrees is True
    args = build_parser().parse_args(["/tmp/x", "--no-worktrees"])
    assert args.use_worktrees is False


# --- happy paths ------------------------------------------------------------


def test_run_over_tree_exits_0_and_prints_table(repo_factory, capsys):
    tree = make_tree(repo_factory)
    code = main([str(tree)])
    assert code == 0

    captured = capsys.readouterr()
    header = captured.out.splitlines()[0]
    fields = [f for f in header.split() if f in {"repo", "upgraded", "majors", "held", "verify", "state"}]
    assert fields == ["repo", "upgraded", "majors", "held", "verify", "state"]
    assert "alpha" in captured.out
    assert "left-pad 1.2.3->1.4.0" in captured.out
    assert "beta" in captured.out
    assert "2 repo(s)" in captured.out
    assert "[1/2] alpha" in captured.err  # progress goes to stderr
    assert "[2/2] beta" in captured.err
    # Default sweep used a worktree: the repo itself never moved.
    alpha = tree / "alpha"
    assert gitops.current_branch(alpha) == "main"
    assert gitops.branch_exists(alpha, default_branch_name())
    assert gitops.worktree_list(alpha) == [str(alpha)]


def test_failed_repo_exits_1(repo_factory, capsys):
    repo_factory("doomed", package=ALPHA, outdated=ALPHA_OUTDATED, breaks={"left-pad": "1.4.0"})
    code = main([str(repo_factory.root)])
    assert code == 1
    out = capsys.readouterr().out
    assert "reverted" in out
    assert "reverted: left-pad broke tests" in out


def test_skips_do_not_fail_the_run(repo_factory, capsys):
    repo_factory("dirty", package=ALPHA, outdated=ALPHA_OUTDATED, dirty=True)
    code = main([str(repo_factory.root)])
    assert code == 0
    assert "skipped (dirty worktree)" in capsys.readouterr().out


def test_json_flag_prints_report_not_table(repo_factory, capsys):
    tree = make_tree(repo_factory)
    code = main([str(tree), "--json"])
    assert code == 0
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["root"] == str(tree)
    assert payload["branch"].startswith("dep-sweep-")
    assert payload["dry_run"] is False
    assert [r["repo"] for r in payload["repos"]] == ["alpha", "beta"]
    assert payload["repos"][0]["upgraded"] == [
        {"name": "left-pad", "from": "1.2.3", "to": "1.4.0"}
    ]
    assert payload["repos"][0]["reverted"] == []
    assert payload["repos"][0]["state"]["status"] == "branch-left"
    assert payload["repos"][1]["state"]["status"] == "clean"
    assert "repo " not in captured.out.splitlines()[0]  # no table header


def test_custom_branch_name_is_used(repo_factory, capsys):
    repo_factory("alpha", package=ALPHA, outdated=ALPHA_OUTDATED)
    code = main([str(repo_factory.root), "--branch", "sweep-custom"])
    assert code == 0
    assert "branch left: sweep-custom" in capsys.readouterr().out


def test_max_depth_limits_discovery(repo_factory, capsys):
    repo_factory("shallow", package={"name": "shallow"}, outdated={})
    repo_factory("nested/deep", package={"name": "deep"}, outdated={})
    code = main([str(repo_factory.root), "--max-depth", "1"])
    assert code == 0
    out = capsys.readouterr().out
    assert "shallow" in out
    assert "deep" not in out


def test_empty_root_runs_clean(capsys, tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    assert main([str(empty)]) == 0
    captured = capsys.readouterr()
    assert "0 repos found" in captured.out
    assert "no repos found" in captured.err


# --- the new flags --------------------------------------------------------------


def test_no_worktrees_sweeps_in_place(repo_factory, capsys):
    repo_factory("oldstyle", package=ALPHA, outdated=ALPHA_OUTDATED)
    code = main([str(repo_factory.root), "--no-worktrees"])
    assert code == 0
    repo = repo_factory.root / "oldstyle"
    # The pre-0.3.0 contract: the repo ends ON the sweep branch.
    assert gitops.current_branch(repo) == default_branch_name()
    assert read_pkg(repo)["dependencies"]["left-pad"] == "^1.4.0"
    assert gitops.worktree_list(repo) == [str(repo)]


def test_html_flag_writes_a_self_contained_report(repo_factory, capsys, tmp_path):
    make_tree(repo_factory)
    target = tmp_path / "sweep-report.html"
    code = main([str(repo_factory.root), "--html", str(target)])
    assert code == 0
    captured = capsys.readouterr()
    assert f"html report: {target}" in captured.err
    html = target.read_text()
    assert html.startswith("<!doctype html>")
    assert "left-pad 1.2.3->1.4.0" in html
    assert "<script" not in html.lower()
    # The table still went to stdout as usual.
    assert "2 repo(s)" in captured.out


def test_html_flag_composes_with_json_and_dry_run(repo_factory, capsys, tmp_path):
    make_tree(repo_factory)
    target = tmp_path / "dry.html"
    code = main([str(repo_factory.root), "--dry-run", "--json", "--html", str(target)])
    assert code == 0
    assert json.loads(capsys.readouterr().out)["dry_run"] is True
    assert "dry run, nothing was changed" in target.read_text()


def test_html_write_failure_fails_the_run(repo_factory, capsys, tmp_path):
    make_tree(repo_factory)
    target = tmp_path / "not-a-file"  # a directory: the write cannot happen
    target.mkdir()
    code = main([str(repo_factory.root), "--html", str(target)])
    assert code == 1
    captured = capsys.readouterr()
    assert "could not write html report" in captured.err
    # The sweep itself still reported its results to stdout.
    assert "2 repo(s)" in captured.out


def test_html_report_reflects_bisection_results(
    repo_factory, capsys, tmp_path, npm_env
):
    package = dict(ALPHA, dependencies={"one": "^1.0.0", "two": "^1.0.0"})
    outdated = {
        name: {"current": "1.0.0", "wanted": "1.2.0", "latest": "2.0.0"}
        for name in ("one", "two")
    }
    repo_factory("bisected", package=package, outdated=outdated, breaks={"two": "1.2.0"})
    target = tmp_path / "bisect.html"
    assert main([str(repo_factory.root), "--html", str(target)]) == 0
    html = target.read_text()
    assert "reverted: two broke tests" in html
    assert "two 1.0.0->1.2.0" in html  # the culprit, in the reverted list
    assert "one 1.0.0->1.2.0" in html


# --- end-to-end dry run -------------------------------------------------------


def test_end_to_end_dry_run_touches_nothing(repo_factory, capsys, npm_env):
    repo_factory("alpha", package=ALPHA, outdated=ALPHA_OUTDATED)
    repo_factory(
        "pinned",
        package={"name": "pinned", "dependencies": {"kit": "3.1.0"}},
        outdated={"kit": {"current": "3.1.0", "wanted": "3.2.0", "latest": "4.0.0"}},
    )
    tree = repo_factory.root

    code = main([str(tree), "--dry-run"])
    assert code == 0

    out = capsys.readouterr().out
    assert "dry-run" in out
    assert "left-pad 1.2.3->1.4.0" in out
    assert "kit (exact pin)" in out

    # Nothing changed anywhere: same branches, clean trees, untouched files.
    for name in ("alpha", "pinned"):
        repo = tree / name
        assert gitops.current_branch(repo) == "main"
        assert gitops.status_porcelain(repo) == ""
        assert not gitops.branch_exists(repo, default_branch_name())
        assert gitops.worktree_list(repo) == [str(repo)]
    assert read_pkg(tree / "alpha")["dependencies"]["left-pad"] == "^1.2.3"

    # Only the read-only outdated lookups ran.
    assert sorted(npm_env.calls()) == sorted(
        ["alpha: npm outdated --json", "pinned: npm outdated --json"]
    )


# --- entry point wiring -------------------------------------------------------


def test_console_script_entry_point_declared():
    """The console script is declared in pyproject; the smoke run exercises it."""
    pyproject = Path(__file__).parent.parent / "pyproject.toml"
    with pyproject.open("rb") as fh:
        data = tomllib_load(fh)
    assert data["project"]["scripts"]["depsweep"] == "depsweep.cli:main"


def test_main_module_wires_sys_exit():
    """__main__ calls sys.exit(main()), so `python -m depsweep` returns codes."""
    main_file = Path(__file__).parent.parent / "src" / "depsweep" / "__main__.py"
    source = main_file.read_text()
    assert "sys.exit(main())" in source


# --- filters and concurrency ----------------------------------------------------


def make_filter_tree(repo_factory):
    for name in ("apps/alpha", "apps/beta", "web", "legacy/gamma"):
        repo_factory(name, package={"name": name}, outdated={})
    return repo_factory.root


def test_include_limits_the_sweep(repo_factory, capsys):
    tree = make_filter_tree(repo_factory)
    assert main([str(tree), "--include", "apps/*", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "alpha" in out and "beta" in out
    assert "web" not in out and "gamma" not in out
    assert "2 repo(s)" in out


def test_exclude_removes_repos_and_wins_over_include(repo_factory, capsys):
    tree = make_filter_tree(repo_factory)
    # "*a*" matches alpha, beta and gamma but not web; legacy/* is excluded.
    code = main([str(tree), "--include", "*a*", "--exclude", "legacy/*", "--dry-run"])
    assert code == 0
    out = capsys.readouterr().out
    assert "alpha" in out and "beta" in out
    assert "gamma" not in out and "web" not in out


def test_jobs_must_be_positive(tmp_path):
    with pytest.raises(SystemExit) as exc:
        main([str(tmp_path), "--jobs", "0"])
    assert exc.value.code == 2


def test_parallel_output_matches_sequential(repo_factory, capsys):
    tree = make_filter_tree(repo_factory)
    assert main([str(tree), "--dry-run", "--jobs", "1"]) == 0
    sequential = capsys.readouterr().out
    assert main([str(tree), "--dry-run", "--jobs", "4"]) == 0
    parallel = capsys.readouterr().out
    assert parallel == sequential  # the table stays in discovery order


def test_progress_lines_are_numbered_by_discovery_position(repo_factory, capsys):
    tree = make_filter_tree(repo_factory)
    assert main([str(tree), "--dry-run", "--jobs", "4"]) == 0
    err = capsys.readouterr().err
    # Line content is deterministic even though completion order is not:
    # discovery order is apps/alpha, apps/beta, legacy/gamma, web.
    assert "[1/4] apps/alpha" in err
    assert "[2/4] apps/beta" in err
    assert "[3/4] legacy/gamma" in err
    assert "[4/4] web" in err


# --- mixed manager trees ---------------------------------------------------------


def make_mixed_tree(repo_factory, pip_repo_factory):
    repo_factory("app-npm", package=ALPHA, outdated=ALPHA_OUTDATED)
    repo_factory("app-pnpm", package=ALPHA, outdated=ALPHA_OUTDATED, pnpm=True)
    repo_factory("app-dirty", package=ALPHA, outdated=ALPHA_OUTDATED, dirty=True)
    pip_repo_factory(
        "app-pip",
        requirements="requests>=2.0\n",
        installed={"requests": "2.31.0"},
        resolve={"requests": "2.32.0"},
    )
    # pip_repo_factory writes into the same tree root by construction.
    return pip_repo_factory.root


def test_mixed_tree_sweeps_every_manager(repo_factory, pip_repo_factory, capsys):
    tree = make_mixed_tree(repo_factory, pip_repo_factory)
    code = main([str(tree), "--dry-run"])
    assert code == 0
    out = capsys.readouterr().out
    for name in ("app-dirty", "app-npm", "app-pip", "app-pnpm"):
        assert name in out
    assert "4 repo(s)" in out


def test_json_report_carries_manager_per_repo(
    repo_factory, pip_repo_factory, capsys
):
    tree = make_mixed_tree(repo_factory, pip_repo_factory)
    assert main([str(tree), "--json", "--dry-run"]) == 0
    payload = json.loads(capsys.readouterr().out)
    managers = {r["repo"]: r["manager"] for r in payload["repos"]}
    assert managers == {
        "app-dirty": "npm",
        "app-npm": "npm",
        "app-pip": "pip",
        "app-pnpm": "pnpm",
    }
