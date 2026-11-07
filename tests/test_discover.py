"""Repo discovery tests: what counts as a repo, where we walk, where we stop."""

import os
from pathlib import Path

from depsweep.discover import filter_repos, find_repos, is_repo


def make_fake_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    (path / ".git").mkdir(exist_ok=True)
    (path / "package.json").write_text('{"name": "x"}\n')
    return path


def test_is_repo_needs_git_and_package_json(tmp_path):
    make_fake_repo(tmp_path / "yes")
    plain = tmp_path / "plain"
    plain.mkdir()
    (plain / "package.json").write_text("{}")
    git_only = tmp_path / "git-only"
    git_only.mkdir()
    (git_only / ".git").mkdir()
    assert is_repo(tmp_path / "yes")
    assert not is_repo(plain)
    assert not is_repo(git_only)
    assert not is_repo(tmp_path / "missing")


def test_git_worktree_file_counts(tmp_path):
    # A git worktree has .git as a FILE, not a directory.
    repo = tmp_path / "worktree"
    repo.mkdir()
    (repo / ".git").write_text("gitdir: ../elsewhere\n")
    (repo / "package.json").write_text("{}")
    assert is_repo(repo)


def test_finds_repos_at_multiple_depths(tmp_path):
    make_fake_repo(tmp_path / "shallow")
    make_fake_repo(tmp_path / "deep" / "deeper" / "repo")
    assert find_repos(tmp_path) == [tmp_path / "deep" / "deeper" / "repo", tmp_path / "shallow"]


def test_ignores_dirs_without_both_markers(tmp_path):
    (tmp_path / "not-a-repo").mkdir()
    (tmp_path / "not-a-repo" / ".git").mkdir()
    assert find_repos(tmp_path) == []


def test_does_not_descend_into_found_repo(tmp_path):
    make_fake_repo(tmp_path / "outer" / "inner")
    # "outer" has no markers, so inner is found through it.
    assert find_repos(tmp_path) == [tmp_path / "outer" / "inner"]
    # But once a directory IS a repo, nothing inside it is scanned.
    make_fake_repo(tmp_path / "real")
    make_fake_repo(tmp_path / "real" / "vendored")
    assert tmp_path / "real" / "vendored" not in find_repos(tmp_path)


def test_skips_node_modules_and_hidden_dirs(tmp_path):
    make_fake_repo(tmp_path / "node_modules" / "hidden-repo")
    make_fake_repo(tmp_path / ".config" / "hidden-repo")
    assert find_repos(tmp_path) == []


def test_max_depth_limits_the_scan(tmp_path):
    make_fake_repo(tmp_path / "level1")                       # depth 1
    make_fake_repo(tmp_path / "plain" / "deep-repo")          # depth 2
    assert find_repos(tmp_path, max_depth=0) == []
    assert find_repos(tmp_path, max_depth=1) == [tmp_path / "level1"]
    assert find_repos(tmp_path, max_depth=2) == [
        tmp_path / "level1",
        tmp_path / "plain" / "deep-repo",
    ]


def test_max_depth_cannot_see_past_a_repo(tmp_path):
    # deep-repo hides inside another repo: discovery never enters a repo.
    make_fake_repo(tmp_path / "level1")
    make_fake_repo(tmp_path / "level1" / "level2" / "deep-repo")  # depth 3
    assert find_repos(tmp_path) == [tmp_path / "level1"]
    assert find_repos(tmp_path, max_depth=3) == [tmp_path / "level1"]


def test_root_itself_being_a_repo_wins(tmp_path):
    make_fake_repo(tmp_path)
    make_fake_repo(tmp_path / "child")
    assert find_repos(tmp_path) == [tmp_path]


def test_symlinked_dirs_are_not_followed(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside"  # outside the scanned root
    make_fake_repo(outside / "linked-repo")
    os.symlink(outside, root / "link")
    assert find_repos(root) == []


# --- pip repos and manager precedence ----------------------------------------


def make_fake_pip_repo(path: Path, marker: str = "requirements") -> Path:
    path.mkdir(parents=True, exist_ok=True)
    (path / ".git").mkdir(exist_ok=True)
    if marker == "requirements":
        (path / "requirements.txt").write_text("requests>=2.0\n")
    elif marker == "pep621":
        (path / "pyproject.toml").write_text(
            '[project]\nname = "x"\ndependencies = ["requests>=2.0"]\n'
        )
    elif marker == "tool-only":
        (path / "pyproject.toml").write_text("[tool.black]\nline-length = 88\n")
    return path


def test_requirements_txt_repo_is_found(tmp_path):
    make_fake_pip_repo(tmp_path / "pyrepo", "requirements")
    assert find_repos(tmp_path) == [tmp_path / "pyrepo"]


def test_pep621_pyproject_repo_is_found(tmp_path):
    make_fake_pip_repo(tmp_path / "modern", "pep621")
    assert find_repos(tmp_path) == [tmp_path / "modern"]


def test_pyproject_without_project_table_is_not_a_repo(tmp_path):
    make_fake_pip_repo(tmp_path / "toolonly", "tool-only")
    assert find_repos(tmp_path) == []


def test_manager_precedence(tmp_path):
    from depsweep.managers import detect

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    (repo / "package.json").write_text("{}")
    (repo / "pnpm-lock.yaml").write_text("lockfileVersion: '9.0'\n")
    (repo / "requirements.txt").write_text("requests>=2.0\n")
    assert detect(repo) == "pnpm"  # lockfile is the strongest node marker

    (repo / "pnpm-lock.yaml").unlink()
    assert detect(repo) == "npm"  # package.json beats pip markers

    (repo / "package.json").unlink()
    assert detect(repo) == "pip"

    (repo / "requirements.txt").unlink()
    (repo / "pyproject.toml").write_text("[build-system]\nrequires = []\n")
    assert detect(repo) is None


# --- glob filters --------------------------------------------------------------


def make_repo_at(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    (path / ".git").mkdir(exist_ok=True)
    (path / "package.json").write_text("{}")
    return path


def test_filter_repos_include_and_exclude(tmp_path):
    for name in ("apps/alpha", "apps/beta", "web", "legacy/gamma"):
        make_repo_at(tmp_path / name)
    repos = find_repos(tmp_path)

    kept = filter_repos(repos, tmp_path, include=["apps/*"])
    assert kept == [tmp_path / "apps" / "alpha", tmp_path / "apps" / "beta"]

    kept = filter_repos(repos, tmp_path, include=["*a*"])
    assert tmp_path / "apps" / "alpha" in kept  # * crosses /
    assert tmp_path / "legacy" / "gamma" in kept

    kept = filter_repos(repos, tmp_path, exclude=["legacy/*", "web"])
    assert kept == [tmp_path / "apps" / "alpha", tmp_path / "apps" / "beta"]

    # Exclude wins over include.
    kept = filter_repos(repos, tmp_path, include=["apps/*"], exclude=["*beta*"])
    assert kept == [tmp_path / "apps" / "alpha"]

    # No filters means everything.
    assert filter_repos(repos, tmp_path) == repos


def test_filter_repos_exact_name_match(tmp_path):
    make_repo_at(tmp_path / "alpha")
    make_repo_at(tmp_path / "alphaweb")
    repos = find_repos(tmp_path)
    assert filter_repos(repos, tmp_path, include=["alpha"]) == [tmp_path / "alpha"]
