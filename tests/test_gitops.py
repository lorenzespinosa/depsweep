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


def test_head_sha_resolves_refs_and_rejects_the_unknown(tmp_path):
    repo = init_repo(tmp_path / "r")
    assert gitops.head_sha(repo, "refs/heads/main") == git(repo, "rev-parse", "HEAD").strip()
    assert gitops.head_sha(repo) == git(repo, "rev-parse", "HEAD").strip()
    assert gitops.head_sha(repo, "refs/heads/nope") is None


def test_reset_to_moves_branch_and_wipes_changes(tmp_path):
    repo = init_repo(tmp_path / "r")
    base = gitops.head_sha(repo, "refs/heads/main")
    gitops.checkout_new_branch(repo, "sweep")
    (repo / "package.json").write_text('{"name": "changed"}\n')
    git(repo, "commit", "-q", "-am", "a depsweep-style change")
    assert gitops.head_sha(repo, "refs/heads/sweep") != base
    gitops.reset_to(repo, base)
    assert gitops.head_sha(repo, "refs/heads/sweep") == base
    assert gitops.status_porcelain(repo) == ""
    assert '"x"' in (repo / "package.json").read_text()


# --- worktrees ------------------------------------------------------------------


def test_worktree_lifecycle(tmp_path):
    repo = init_repo(tmp_path / "r")
    worktree = tmp_path / "wt" / "r"
    assert gitops.worktree_add(repo, worktree, "sweep", "refs/heads/main").returncode == 0
    # The repo itself never moved; the branch lives in the worktree.
    assert gitops.current_branch(repo) == "main"
    assert gitops.current_branch(worktree) == "sweep"
    assert gitops.branch_exists(repo, "sweep")
    assert str(worktree) in gitops.worktree_list(repo)
    # A commit made in the worktree lands on the shared branch ref.
    (worktree / "package.json").write_text('{"name": "from-worktree"}\n')
    gitops.commit_all(worktree, "depsweep: from worktree")
    assert "from-worktree" in git(repo, "show", "sweep:package.json")
    # And survives the worktree being removed.
    assert gitops.worktree_remove(repo, worktree).returncode == 0
    assert gitops.worktree_list(repo) == [str(repo)]
    assert gitops.branch_exists(repo, "sweep")
    assert "from-worktree" in git(repo, "show", "sweep:package.json")


def test_worktree_remove_forces_out_untracked_files(tmp_path):
    repo = init_repo(tmp_path / "r")
    worktree = tmp_path / "wt" / "r"
    gitops.worktree_add(repo, worktree, "sweep", "refs/heads/main")
    # Untracked build output (node_modules and friends) must not block.
    (worktree / "node_modules").mkdir()
    (worktree / "node_modules" / "junk").write_text("x\n")
    (worktree / "package.json").write_text('{"name": "dirty"}\n')
    assert gitops.worktree_remove(repo, worktree).returncode == 0
    assert gitops.worktree_list(repo) == [str(repo)]


def test_worktree_add_refuses_existing_branch(tmp_path):
    repo = init_repo(tmp_path / "r")
    git(repo, "branch", "sweep")
    worktree = tmp_path / "wt" / "r"
    assert gitops.worktree_add(repo, worktree, "sweep", "refs/heads/main").returncode != 0
    assert gitops.worktree_list(repo) == [str(repo)]


def test_module_makes_no_push_calls():
    """Regression guard for the README promise: depsweep never pushes.

    If someone adds a push to gitops, this source check fails loudly.
    """
    import inspect

    source = inspect.getsource(gitops)
    assert '"push"' not in source
    assert "'push'" not in source
