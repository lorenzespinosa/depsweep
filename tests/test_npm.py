"""npm subprocess behaviour through the offline stub: exit 1 is data, bad JSON is error."""

import json

from depsweep.npm import (
    npm_bare_install,
    npm_install,
    npm_outdated,
    npm_run,
    npm_scripts,
    read_package_json,
)

from conftest import read_pkg


def test_outdated_exit_one_with_json_is_valid_data(repo_factory):
    repo = repo_factory(
        "alpha",
        package={"name": "alpha", "dependencies": {"left-pad": "^1.2.3"}},
        outdated={
            "left-pad": {"current": "1.2.3", "wanted": "1.4.0", "latest": "2.0.0"}
        },
    )
    data, error = npm_outdated(repo)
    assert error is None
    assert data == {
        "left-pad": {"current": "1.2.3", "wanted": "1.4.0", "latest": "2.0.0"}
    }


def test_outdated_empty_object_is_no_news(repo_factory):
    repo = repo_factory("beta", package={"name": "beta"}, outdated={})
    data, error = npm_outdated(repo)
    assert error is None
    assert data == {}


def test_outdated_missing_fixture_means_nothing_outdated(repo_factory):
    repo = repo_factory("gamma", package={"name": "gamma"})
    data, error = npm_outdated(repo)
    assert error is None
    assert data == {}


def test_outdated_exit_code_one_with_empty_body_is_still_valid(repo_factory):
    # npm exits 1 whenever anything is outdated; with an empty body it is
    # still just data, never an error.
    repo = repo_factory("delta", package={"name": "delta"}, outdated={},
                        exits={"outdated": 1})
    data, error = npm_outdated(repo)
    assert error is None
    assert data == {}


def test_outdated_weird_exit_with_valid_json_is_still_data(repo_factory):
    repo = repo_factory(
        "epsilon",
        package={"name": "epsilon", "dependencies": {"a": "^1.0.0"}},
        outdated={"a": {"current": "1.0.0", "wanted": "1.1.0", "latest": "1.1.0"}},
        exits={"outdated": 3},
    )
    data, error = npm_outdated(repo)
    assert error is None
    assert "a" in data


def test_outdated_malformed_json_is_an_error(repo_factory):
    repo = repo_factory("zeta", package={"name": "zeta"}, outdated="not json {{{")
    data, error = npm_outdated(repo)
    assert data == {}
    assert "invalid JSON" in error


def test_outdated_no_output_with_hard_error_code_is_an_error(repo_factory):
    repo = repo_factory("eta", package={"name": "eta"}, outdated="",
                        exits={"outdated": 2})
    data, error = npm_outdated(repo)
    assert data == {}
    assert "exited 2" in error


def test_install_rewrites_package_json_and_succeeds(repo_factory):
    repo = repo_factory(
        "theta",
        package={
            "name": "theta",
            "dependencies": {"left-pad": "^1.2.3"},
            "devDependencies": {"tool": "^0.4.0"},
        },
    )
    assert npm_install(repo, "left-pad", "1.4.0").returncode == 0
    assert read_pkg(repo)["dependencies"]["left-pad"] == "^1.4.0"
    assert read_pkg(repo)["devDependencies"]["tool"] == "^0.4.0"
    npm_install(repo, "tool", "0.9.1")
    assert read_pkg(repo)["devDependencies"]["tool"] == "^0.9.1"


def test_install_honours_exit_override(repo_factory):
    repo = repo_factory("iota", package={"name": "iota"}, exits={"install": 1})
    assert npm_install(repo, "any", "1.0.0").returncode == 1


def test_install_scoped_package_name_survives(repo_factory):
    repo = repo_factory(
        "kappa",
        package={"name": "kappa", "dependencies": {"@scope/pkg": "^1.0.0"}},
    )
    assert npm_install(repo, "@scope/pkg", "1.2.0").returncode == 0
    assert read_pkg(repo)["dependencies"]["@scope/pkg"] == "^1.2.0"


def test_run_respects_script_exit_override(repo_factory):
    repo = repo_factory("lambda", package={"name": "lambda"}, exits={"test": 7})
    assert npm_run(repo, "test").returncode == 7
    assert npm_run(repo, "build").returncode == 0


def test_bare_install_respects_exit_override(repo_factory):
    repo = repo_factory("mu", package={"name": "mu"}, exits={"install": 5})
    assert npm_bare_install(repo).returncode == 5


def test_read_package_json_variants(tmp_path):
    good = tmp_path / "good"
    good.mkdir()
    (good / "package.json").write_text('{"name": "good"}')
    assert read_package_json(good) == {"name": "good"}
    bad = tmp_path / "bad"
    bad.mkdir()
    (bad / "package.json").write_text("not json")
    assert read_package_json(bad) == {}
    empty = tmp_path / "empty"
    empty.mkdir()
    assert read_package_json(empty) == {}


def test_npm_scripts_tolerates_garbage():
    assert npm_scripts({}) == {}
    assert npm_scripts({"scripts": None}) == {}
    assert npm_scripts({"scripts": "nope"}) == {}
    assert npm_scripts({"scripts": {"test": "echo hi"}}) == {"test": "echo hi"}
