"""CLI tests: exit codes, flags, output on a fixture tree, entry point wiring.

The `python -m depsweep` subprocess path is exercised by the smoke script
(smoke/build_fixtures.py prints the exact commands); here we drive main()
in process, which runs the identical code path minus the process spawn.
"""

import json
from pathlib import Path
from tomllib import load as tomllib_load

import pytest

from depsweep import gitops
from depsweep.cli import default_branch_name, main

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


def test_default_branch_name_has_date_shape():
    assert default_branch_name().startswith("dep-sweep-2")
    assert len(default_branch_name()) == len("dep-sweep-YYYYMMDD")


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


def test_failed_repo_exits_1(repo_factory, capsys):
    repo_factory("doomed", package=ALPHA, outdated=ALPHA_OUTDATED, exits={"test": 1})
    code = main([str(repo_factory.root)])
    assert code == 1
    out = capsys.readouterr().out
    assert "reverted" in out


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
