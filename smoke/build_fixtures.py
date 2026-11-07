#!/usr/bin/env python3
"""Build the smoke fixture tree: four fake git repos plus offline stubs.

Everything lands under smoke/ (gitignored):

- tree/app-npm     git repo, npm. One in-range caret upgrade, one major, one
                   exact pin, and a test script that passes.
- tree/app-pnpm    git repo, pnpm (pnpm-lock.yaml). One in-range upgrade and
                   a failing check style gate that passes.
- tree/app-pip     git repo, pip. requirements.txt plus a fake .venv holding
                   the pip stub. One in-range upgrade, one == pin.
- tree/app-dirty   git repo, npm, with uncommitted work. Must be skipped.
- bin/npm, bin/pnpm      the offline node stub (tests/npm_stub.py)
- bin/pip, bin/python3   the offline pip stub (tests/pip_stub.py)
- npm-fixtures/, pip-fixtures/   canned payloads keyed by repo name

After running this script, sweep the tree offline with (from the repo root):

    python smoke/build_fixtures.py
    export PATH="$PWD/smoke/bin:$PATH"
    export NPM_STUB_FIXTURES="$PWD/smoke/npm-fixtures" NPM_STUB_LOG="$PWD/smoke/npm-calls.log"
    export PIP_STUB_FIXTURES="$PWD/smoke/pip-fixtures" PIP_STUB_LOG="$PWD/smoke/pip-calls.log"

    python -m depsweep smoke/tree --dry-run
    python -m depsweep smoke/tree
"""

import json
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
TREE = HERE / "tree"
BIN = HERE / "bin"
NODE_FIXTURES = HERE / "npm-fixtures"
PIP_FIXTURES = HERE / "pip-fixtures"
NPM_STUB_SOURCE = HERE.parent / "tests" / "npm_stub.py"
PIP_STUB_SOURCE = HERE.parent / "tests" / "pip_stub.py"

NPM_PACKAGE = {
    "name": "app-npm",
    "private": True,
    "scripts": {"test": "node --test"},
    "dependencies": {
        "left-pad": "^1.2.3",   # in range: upgrade to 1.4.0
        "big-lib": "*",         # wanted crosses a major: list only
        "pinned-kit": "3.1.0",  # exact pin: hold
    },
}
NPM_OUTDATED = {
    "left-pad": {"current": "1.2.3", "wanted": "1.4.0", "latest": "2.0.0"},
    "big-lib": {"current": "1.9.0", "wanted": "2.5.0", "latest": "2.5.0"},
    "pinned-kit": {"current": "3.1.0", "wanted": "3.2.0", "latest": "4.0.0"},
}

PNPM_PACKAGE = {
    "name": "app-pnpm",
    "private": True,
    "scripts": {"build": "pnpm build"},
    "dependencies": {"tiny-fmt": "~2.1.0"},   # in range: upgrade to 2.1.4
}
PNPM_OUTDATED = {
    "tiny-fmt": {"current": "2.1.0", "wanted": "2.1.4", "latest": "3.0.0"}
}

PIP_REQUIREMENTS = "requests>=2.0\nhttpx==0.27.0\n"


def install_stub(source: Path, target_dir: Path, name: str) -> None:
    """Write a stub executable whose shebang names the running interpreter."""
    body = source.read_text()
    if body.startswith("#!"):
        body = body.split("\n", 1)[1]
    stub = target_dir / name
    stub.write_text(f"#!{sys.executable}\n{body}")
    stub.chmod(0o755)


def git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    )


def init_repo(name: str) -> Path:
    repo = TREE / name
    repo.mkdir(parents=True)
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.email", "smoke@depsweep.invalid")
    git(repo, "config", "user.name", "depsweep smoke")
    return repo


def make_node_repo(name: str, package: dict, outdated: dict, pnpm: bool) -> Path:
    repo = init_repo(name)
    (repo / "package.json").write_text(json.dumps(package, indent=2) + "\n")
    if pnpm:
        (repo / "pnpm-lock.yaml").write_text("lockfileVersion: '9.0'\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "init")
    (NODE_FIXTURES / f"{name}.outdated.json").write_text(
        json.dumps(outdated, indent=2) + "\n"
    )
    return repo


def make_pip_repo(name: str) -> Path:
    repo = init_repo(name)
    (repo / "requirements.txt").write_text(PIP_REQUIREMENTS)
    (repo / ".gitignore").write_text(".venv/\nvenv/\n")
    venv_bin = repo / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    install_stub(PIP_STUB_SOURCE, venv_bin, "pip")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "init")
    (PIP_FIXTURES / f"{name}.list.json").write_text(
        json.dumps([
            {"name": "requests", "version": "2.31.0"},
            {"name": "httpx", "version": "0.27.0"},
        ]) + "\n"
    )
    (PIP_FIXTURES / f"{name}.resolve.json").write_text(
        json.dumps({"requests": "2.32.3", "httpx": "0.28.0"}, indent=2) + "\n"
    )
    return repo


def main() -> int:
    for directory in (TREE, BIN, NODE_FIXTURES, PIP_FIXTURES):
        shutil.rmtree(directory, ignore_errors=True)
    for directory in (TREE, BIN, NODE_FIXTURES, PIP_FIXTURES):
        directory.mkdir(parents=True)
    (HERE / "npm-calls.log").unlink(missing_ok=True)
    (HERE / "pip-calls.log").unlink(missing_ok=True)

    make_node_repo("app-npm", NPM_PACKAGE, NPM_OUTDATED, pnpm=False)
    make_node_repo("app-pnpm", PNPM_PACKAGE, PNPM_OUTDATED, pnpm=True)
    make_pip_repo("app-pip")
    dirty = make_node_repo(
        "app-dirty",
        {"name": "app-dirty", "dependencies": {"left-pad": "^1.2.3"}},
        {"left-pad": {"current": "1.2.3", "wanted": "1.4.0", "latest": "2.0.0"}},
        pnpm=False,
    )
    (dirty / "work-in-progress.txt").write_text("do not touch\n")

    install_stub(NPM_STUB_SOURCE, BIN, "npm")
    install_stub(NPM_STUB_SOURCE, BIN, "pnpm")
    install_stub(PIP_STUB_SOURCE, BIN, "pip")
    install_stub(PIP_STUB_SOURCE, BIN, "python3")

    print(f"fixture tree : {TREE}")
    print(f"node stubs   : {BIN / 'npm'}, {BIN / 'pnpm'}")
    print(f"pip stubs    : {BIN / 'pip'}, {BIN / 'python3'}")
    print()
    print("Sweep it (run from the repo root):")
    print()
    print('    export PATH="$PWD/smoke/bin:$PATH"')
    print('    export NPM_STUB_FIXTURES="$PWD/smoke/npm-fixtures" NPM_STUB_LOG="$PWD/smoke/npm-calls.log"')
    print('    export PIP_STUB_FIXTURES="$PWD/smoke/pip-fixtures" PIP_STUB_LOG="$PWD/smoke/pip-calls.log"')
    print()
    print("    python -m depsweep smoke/tree --dry-run   # preview")
    print("    python -m depsweep smoke/tree             # real sweep")
    return 0


if __name__ == "__main__":
    sys.exit(main())
