"""Shared fixtures: offline npm/pnpm and pip stubs on PATH, real git repos."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

NPM_STUB_SOURCE = Path(__file__).parent / "npm_stub.py"
PIP_STUB_SOURCE = Path(__file__).parent / "pip_stub.py"


def install_stub(source: Path, bin_dir: Path, name: str) -> None:
    """Write a stub executable whose shebang names the real interpreter.

    A fixed shebang (not ``env python3``) keeps stubs working when a fake
    ``python3`` sits further up PATH for pip tests."""
    body = source.read_text()
    if body.startswith("#!"):
        body = body.split("\n", 1)[1]
    stub = bin_dir / name
    stub.write_text(f"#!{sys.executable}\n{body}")
    stub.chmod(0o755)


def git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=True
    )
    return proc.stdout


@dataclass
class NpmEnv:
    """The stubbed npm/pnpm world: fixtures dir, call log, PATH override."""

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
class PipEnv:
    """The stubbed pip world: fixtures dir, call log, PATH override."""

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
        pnpm: bool = False,
    ) -> Path:
        """Create a clean git repo (branch ``main``) under root/name.

        ``pnpm=True`` adds a pnpm-lock.yaml so the repo is swept with pnpm;
        the canned outdated payload and exit overrides are shared."""
        repo = self.root / name
        repo.mkdir(parents=True)
        (repo / "package.json").write_text(json.dumps(package, indent=2) + "\n")
        if pnpm:
            (repo / "pnpm-lock.yaml").write_text("lockfileVersion: '9.0'\n")
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


def _toml(pyproject: dict) -> str:
    """Render a minimal pyproject.toml from a dict of sections."""
    lines = []
    for section, body in pyproject.items():
        lines.append(f"[{section}]")
        for key, value in body.items():
            if isinstance(value, list):
                rendered = ", ".join(json.dumps(v) for v in value)
                lines.append(f"{key} = [{rendered}]")
            elif isinstance(value, str):
                lines.append(f'{key} = "{value}"')
            else:
                lines.append(f"{key} = {value}")
        lines.append("")
    return "\n".join(lines)


@dataclass
class PipRepoFactory:
    pip: PipEnv
    root: Path

    def __call__(
        self,
        name: str,
        requirements: str | None = None,
        pyproject: dict | None = None,
        installed: dict[str, str] | None = None,
        resolve: dict[str, str] | None = None,
        exits: dict[str, int] | None = None,
        venv: bool = True,
        dirty: bool = False,
    ) -> Path:
        """Create a clean git pip repo under root/name.

        ``requirements`` is written verbatim as requirements.txt; when None,
        ``pyproject`` (a dict of TOML sections, see _toml) is written as a
        PEP 621 pyproject.toml instead. ``installed`` becomes the pip list
        fixture and ``resolve`` the resolver fixture. With ``venv=True`` the
        repo gets a fake ``.venv/bin/pip`` (the stub) plus a .gitignore for
        it, mirroring real life."""
        repo = self.root / name
        repo.mkdir(parents=True)
        if requirements is not None:
            (repo / "requirements.txt").write_text(requirements)
        elif pyproject is not None:
            (repo / "pyproject.toml").write_text(_toml(pyproject))
        git(repo, "init", "-q", "-b", "main")
        git(repo, "config", "user.email", "test@depsweep.invalid")
        git(repo, "config", "user.name", "Depsweep Test")
        if venv:
            (repo / ".gitignore").write_text(".venv/\nvenv/\n")
            bin_dir = repo / ".venv" / "bin"
            bin_dir.mkdir(parents=True)
            install_stub(PIP_STUB_SOURCE, bin_dir, "pip")
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "-m", "init")
        if installed is not None:
            body = json.dumps(
                [{"name": k, "version": v} for k, v in sorted(installed.items())]
            )
            (self.pip.fixtures / f"{repo.name}.list.json").write_text(body + "\n")
        if resolve is not None:
            (self.pip.fixtures / f"{repo.name}.resolve.json").write_text(
                json.dumps(resolve, indent=2) + "\n"
            )
        for kind, code in (exits or {}).items():
            (self.pip.fixtures / f"{repo.name}.{kind}.exit").write_text(f"{code}\n")
        if dirty:
            (repo / "uncommitted.txt").write_text("local dirt\n")
        return repo


@pytest.fixture()
def npm_env(tmp_path: Path, monkeypatch) -> NpmEnv:
    """Install the node stub as ``npm`` and ``pnpm`` on PATH, first in line."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    env = NpmEnv(fixtures=tmp_path / "npm-fixtures", log=tmp_path / "npm-calls.log")
    env.fixtures.mkdir()
    install_stub(NPM_STUB_SOURCE, bin_dir, "npm")
    install_stub(NPM_STUB_SOURCE, bin_dir, "pnpm")
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("NPM_STUB_FIXTURES", str(env.fixtures))
    monkeypatch.setenv("NPM_STUB_LOG", str(env.log))
    return env


@pytest.fixture()
def pip_env(tmp_path: Path, monkeypatch) -> PipEnv:
    """Install the pip stub as ``pip`` and a venv-creating ``python3``."""
    bin_dir = tmp_path / "pip-bin"
    bin_dir.mkdir()
    env = PipEnv(fixtures=tmp_path / "pip-fixtures", log=tmp_path / "pip-calls.log")
    env.fixtures.mkdir()
    install_stub(PIP_STUB_SOURCE, bin_dir, "pip")
    install_stub(PIP_STUB_SOURCE, bin_dir, "python3")
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("PIP_STUB_FIXTURES", str(env.fixtures))
    monkeypatch.setenv("PIP_STUB_LOG", str(env.log))
    return env


@pytest.fixture()
def repo_factory(tmp_path: Path, npm_env: NpmEnv) -> RepoFactory:
    """make_repo(name, package, outdated=?, exits=?, dirty=?, pnpm=?) -> Path."""
    tree = tmp_path / "tree"
    tree.mkdir(exist_ok=True)  # shared with pip_repo_factory in mixed tests
    return RepoFactory(npm=npm_env, root=tree)


@pytest.fixture()
def pip_repo_factory(tmp_path: Path, pip_env: PipEnv) -> PipRepoFactory:
    """make_pip_repo(name, requirements=?, pyproject=?, ...) -> Path."""
    tree = tmp_path / "tree"
    tree.mkdir(exist_ok=True)  # shared with repo_factory in mixed tests
    return PipRepoFactory(pip=pip_env, root=tree)


@pytest.fixture()
def tree(tmp_path: Path) -> Path:
    """An empty fixture tree root."""
    tree = tmp_path / "tree"
    tree.mkdir()
    return tree


def read_pkg(repo: Path) -> dict:
    return json.loads((repo / "package.json").read_text())
