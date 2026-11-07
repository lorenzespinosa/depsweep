"""Shared fixtures: an offline npm stub on PATH and real git repos in tmp_path."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

STUB_SOURCE = Path(__file__).parent / "npm_stub.py"


def git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=True
    )
    return proc.stdout


@dataclass
class NpmEnv:
    """The stubbed npm world: fixtures dir, call log, PATH override."""

    fixtures: Path
    log: Path

    def calls(self, repo: str | None = None) -> list[str]:
        if not self.log.exists():
            return []
        lines = self.log.read_text().splitlines()
        if repo is None:
            return lines
        return [ln for ln in lines if ln.startswith(f"{repo}: ")]


@dataclass
class RepoFactory:
    npm: NpmEnv
    root: Path

    def __call__(
        self,
        name: str,
        package: dict,
        outdated: dict | str | None = None,
        exits: dict[str, int] | None = None,
        dirty: bool = False,
    ) -> Path:
        """Create a clean git repo (branch ``main``) under root/name."""
        repo = self.root / name
        repo.mkdir(parents=True)
        (repo / "package.json").write_text(json.dumps(package, indent=2) + "\n")
        git(repo, "init", "-q", "-b", "main")
        git(repo, "config", "user.email", "test@depsweep.invalid")
        git(repo, "config", "user.name", "Depsweep Test")
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "-m", "init")
        if outdated is not None:
            body = (
                outdated
                if isinstance(outdated, str)
                else json.dumps(outdated, indent=2) + "\n"
            )
            (self.npm.fixtures / f"{repo.name}.outdated.json").write_text(body)
        for kind, code in (exits or {}).items():
            (self.npm.fixtures / f"{repo.name}.{kind}.exit").write_text(f"{code}\n")
        if dirty:
            (repo / "uncommitted.txt").write_text("local dirt\n")
        return repo


@pytest.fixture()
def npm_env(tmp_path: Path, monkeypatch) -> NpmEnv:
    """Install the npm stub as ``npm`` on PATH, first in line."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    env = NpmEnv(fixtures=tmp_path / "npm-fixtures", log=tmp_path / "npm-calls.log")
    env.fixtures.mkdir()
    stub = bin_dir / "npm"
    shutil.copy(STUB_SOURCE, stub)
    stub.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("NPM_STUB_FIXTURES", str(env.fixtures))
    monkeypatch.setenv("NPM_STUB_LOG", str(env.log))
    return env


@pytest.fixture()
def repo_factory(tmp_path: Path, npm_env: NpmEnv) -> RepoFactory:
    """make_repo(name, package, outdated=?, exits=?, dirty=?) -> Path."""
    tree = tmp_path / "tree"
    tree.mkdir()
    return RepoFactory(npm=npm_env, root=tree)


@pytest.fixture()
def tree(tmp_path: Path) -> Path:
    """An empty fixture tree root."""
    tree = tmp_path / "tree"
    tree.mkdir()
    return tree


def read_pkg(repo: Path) -> dict:
    return json.loads((repo / "package.json").read_text())
