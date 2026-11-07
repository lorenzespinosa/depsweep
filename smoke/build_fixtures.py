#!/usr/bin/env python3
"""Build the smoke fixture tree: two fake git repos plus an offline npm stub.

Everything lands under smoke/ (gitignored):

- tree/app-alpha   git repo. One in-range caret upgrade available, one major
                   available, one exact pin, and a test script that passes.
- tree/app-beta    git repo. Only a pin and a major. Nothing to install, so
                   the sweep reports it clean.
- bin/npm          the offline npm stub (a copy of tests/npm_stub.py)
- npm-fixtures/    canned `npm outdated` payloads keyed by repo name

After running this script, sweep the tree offline with (from the repo root):

    python smoke/build_fixtures.py
    export PATH="$PWD/smoke/bin:$PATH"
    export NPM_STUB_FIXTURES="$PWD/smoke/npm-fixtures"
    export NPM_STUB_LOG="$PWD/smoke/npm-calls.log"

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
FIXTURES = HERE / "npm-fixtures"
STUB_SOURCE = HERE.parent / "tests" / "npm_stub.py"

ALPHA_PACKAGE = {
    "name": "app-alpha",
    "private": True,
    "scripts": {"test": "node --test"},
    "dependencies": {
        "left-pad": "^1.2.3",   # in range: upgrade to 1.4.0
        "big-lib": "*",         # wanted crosses a major: list only
        "pinned-kit": "3.1.0",  # exact pin: hold
    },
}
ALPHA_OUTDATED = {
    "left-pad": {"current": "1.2.3", "wanted": "1.4.0", "latest": "2.0.0"},
    "big-lib": {"current": "1.9.0", "wanted": "2.5.0", "latest": "2.5.0"},
    "pinned-kit": {"current": "3.1.0", "wanted": "3.2.0", "latest": "4.0.0"},
}

BETA_PACKAGE = {
    "name": "app-beta",
    "private": True,
    "scripts": {"build": "node build.js"},
    "dependencies": {"old-core": "2.0.0"},      # exact pin: hold
    "devDependencies": {"wide-open": "*"},       # 0.x -> 1.x: list only
}
BETA_OUTDATED = {
    "old-core": {"current": "2.0.0", "wanted": "2.4.0", "latest": "2.4.0"},
    "wide-open": {"current": "0.9.1", "wanted": "1.0.0", "latest": "1.0.0"},
}


def git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    )


def make_repo(name: str, package: dict, outdated: dict) -> Path:
    repo = TREE / name
    repo.mkdir(parents=True)
    (repo / "package.json").write_text(json.dumps(package, indent=2) + "\n")
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.email", "smoke@depsweep.invalid")
    git(repo, "config", "user.name", "depsweep smoke")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "init")
    (FIXTURES / f"{name}.outdated.json").write_text(
        json.dumps(outdated, indent=2) + "\n"
    )
    return repo


def main() -> int:
    shutil.rmtree(TREE, ignore_errors=True)
    shutil.rmtree(BIN, ignore_errors=True)
    shutil.rmtree(FIXTURES, ignore_errors=True)
    TREE.mkdir(parents=True)
    FIXTURES.mkdir(parents=True)
    BIN.mkdir(parents=True)

    make_repo("app-alpha", ALPHA_PACKAGE, ALPHA_OUTDATED)
    make_repo("app-beta", BETA_PACKAGE, BETA_OUTDATED)

    stub = BIN / "npm"
    shutil.copy(STUB_SOURCE, stub)
    stub.chmod(0o755)
    (HERE / "npm-calls.log").unlink(missing_ok=True)

    print(f"fixture tree : {TREE}")
    print(f"npm stub     : {stub}")
    print(f"npm fixtures : {FIXTURES}")
    print()
    print("Sweep it (run from the repo root):")
    print()
    print('    export PATH="$PWD/smoke/bin:$PATH"')
    print('    export NPM_STUB_FIXTURES="$PWD/smoke/npm-fixtures"')
    print('    export NPM_STUB_LOG="$PWD/smoke/npm-calls.log"')
    print()
    print("    python -m depsweep smoke/tree --dry-run   # preview")
    print("    python -m depsweep smoke/tree             # real sweep")
    return 0


if __name__ == "__main__":
    sys.exit(main())
