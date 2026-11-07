"""Raw git operation tests on real throwaway repos."""

import subprocess

from depsweep import gitops

from conftest import git


def init_repo(path, commit=True):
    path.mkdir(parents=True, exist_ok=True)
    (path / "package.json").write_text('{"name": "x"}\n')
    git(path, "init", "-q", "-b", "main")
    git(path, "config", "user.email", "test@depsweep.invalid")
    git(path, "config", "user.name", "Depsweep Test")
    if commit:
        git(path, "add", "-A")
        git(path, "commit", "-q", "-m", "init")
    return path


def test_status_porcelain_clean_then_dirty(tmp_path):
    repo = init_repo(tmp_path / "r")
    assert gitops.status_porcelain(repo) == ""
    (repo / "dirt.txt").write_text("dirt\n")
    assert gitops.status_porcelain(repo) != ""


def test_current_branch_and_detached_head(tmp_path):
    repo = init_repo(tmp_path / "r")
    assert gitops.current_branch(repo) == "main"
    git(repo, "checkout", "-q", "--detach")
    assert gitops.current_branch(repo) is None


def test_branch_lifecycle(tmp_path):
    repo = init_repo(tmp_path / "r")
    assert not gitops.branch_exists(repo, "sweep")
    assert gitops.checkout_new_branch(repo, "sweep").returncode == 0
    assert gitops.branch_exists(repo, "sweep")
    assert gitops.current_branch(repo) == "sweep"
    assert gitops.checkout(repo, "main").returncode == 0
    assert gitops.delete_branch(repo, "sweep").returncode == 0
    assert not gitops.branch_exists(repo, "sweep")


def test_invalid_branch_name_is_refused_by_git(tmp_path):
    repo = init_repo(tmp_path / "r")
    assert gitops.checkout_new_branch(repo, "bad name").returncode != 0


def test_discard_changes_resets_worktree(tmp_path):
    repo = init_repo(tmp_path / "r")
    gitops.checkout_new_branch(repo, "sweep")
    (repo / "package.json").write_text('{"name": "changed"}\n')
    assert gitops.status_porcelain(repo) != ""
    gitops.discard_changes(repo)
    assert gitops.status_porcelain(repo) == ""
    assert '"x"' in (repo / "package.json").read_text()


def test_commit_all_commits_everything(tmp_path):
    repo = init_repo(tmp_path / "r")
    gitops.checkout_new_branch(repo, "sweep")
    (repo / "package.json").write_text('{"name": "changed"}\n')
    (repo / "extra.txt").write_text("new file\n")
    assert gitops.commit_all(repo, "depsweep: test commit") is True
    assert gitops.status_porcelain(repo) == ""
    subject = git(repo, "log", "-1", "--pretty=%s")
    assert subject.strip() == "depsweep: test commit"
    # Nothing left to commit: git refuses, we report False.
    assert gitops.commit_all(repo, "depsweep: again") is False


def test_module_makes_no_push_calls():
    """Regression guard for the README promise: depsweep never pushes.

    If someone adds a push to gitops, this source check fails loudly.
    """
    import inspect

    source = inspect.getsource(gitops)
    assert '"push"' not in source
    assert "'push'" not in source
