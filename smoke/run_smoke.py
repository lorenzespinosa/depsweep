#!/usr/bin/env python3
"""Offline smoke run for depsweep: build a mixed fixture tree in a temp
directory, sweep it with the CLI entry point, and check the outcome.

The tree, per repo:

- app-npm    git repo, npm. One in-range caret upgrade, one major, one exact
             pin, test script passes. Lands on a sweep branch.
- app-pnpm   git repo, pnpm (pnpm-lock.yaml). Build gate, passes.
- app-pip    git repo, pip. requirements.txt plus a fake .venv holding the
             pip stub. One in-range upgrade, one == pin.
- app-dirty  git repo, npm, with uncommitted work. Must be skipped.
- app-bisect git repo, npm. Four in-range upgrades; the repo's tests are
             planted to fail exactly once bad-lib reaches 1.3.0. Bisection
             must convict bad-lib, hold it back, and land the other three.

Everything is offline: npm, pnpm and pip are the test stubs, git fixtures
are real throwaway repos, and the whole tree lives in a temp directory that
is removed afterwards (kept when a check fails, for inspection). The CLI is
driven in process through depsweep.cli.main, the exact function the console
script wraps, so the smoke needs no installed copy. Run it from the repo
root (the -B keeps Python from writing bytecode caches into the repo):

    python -B smoke/run_smoke.py

Exit code 0 means every check passed.
"""

import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NPM_STUB_SOURCE = ROOT / "tests" / "npm_stub.py"
PIP_STUB_SOURCE = ROOT / "tests" / "pip_stub.py"

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

BISECT_PACKAGE = {
    "name": "app-bisect",
    "private": True,
    "scripts": {"test": "node --test"},
    "dependencies": {
        "aa-lib": "^1.0.0",
        "bb-lib": "^1.0.0",
        "bad-lib": "^1.2.0",
        "dd-lib": "^1.0.0",
    },
}
BISECT_OUTDATED = {
    "aa-lib": {"current": "1.0.0", "wanted": "1.1.0", "latest": "2.0.0"},
    "bb-lib": {"current": "1.0.0", "wanted": "1.1.0", "latest": "2.0.0"},
    "bad-lib": {"current": "1.2.0", "wanted": "1.3.0", "latest": "2.0.0"},
    "dd-lib": {"current": "1.0.0", "wanted": "1.1.0", "latest": "2.0.0"},
}
# The plant: this repo's tests fail exactly once bad-lib sits at 1.3.0.
BISECT_BREAKS = {"bad-lib": "1.3.0"}

BRANCH = "dep-sweep-smoke"

ALL_REPOS = ("app-npm", "app-pnpm", "app-pip", "app-dirty", "app-bisect")


def install_stub(source: Path, target_dir: Path, name: str) -> None:
    """Write a stub executable whose shebang names the running interpreter."""
    body = source.read_text()
    if body.startswith("#!"):
        body = body.split("\n", 1)[1]
    stub = target_dir / name
    stub.write_text(f"#!{sys.executable}\n{body}")
    stub.chmod(0o755)


def git(repo: Path, *args: str) -> str:
    """Run git in a repo. Argument list form, shell never involved."""
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout


def init_repo(tree: Path, name: str) -> Path:
    repo = tree / name
    repo.mkdir(parents=True)
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.email", "smoke@depsweep.invalid")
    git(repo, "config", "user.name", "depsweep smoke")
    return repo


def make_node_repo(
    tree: Path,
    fixtures: Path,
    name: str,
    package: dict,
    outdated: dict,
    pnpm: bool = False,
    breaks: dict | None = None,
) -> Path:
    repo = init_repo(tree, name)
    (repo / "package.json").write_text(json.dumps(package, indent=2) + "\n")
    if pnpm:
        (repo / "pnpm-lock.yaml").write_text("lockfileVersion: '9.0'\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "init")
    (fixtures / f"{name}.outdated.json").write_text(
        json.dumps(outdated, indent=2) + "\n"
    )
    if breaks:
        (fixtures / f"{name}.breaks.json").write_text(json.dumps(breaks) + "\n")
    return repo


def make_pip_repo(tree: Path, fixtures: Path) -> Path:
    repo = init_repo(tree, "app-pip")
    (repo / "requirements.txt").write_text(PIP_REQUIREMENTS)
    (repo / ".gitignore").write_text(".venv/\nvenv/\n")
    venv_bin = repo / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    install_stub(PIP_STUB_SOURCE, venv_bin, "pip")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "init")
    (fixtures / "app-pip.list.json").write_text(
        json.dumps([
            {"name": "requests", "version": "2.31.0"},
            {"name": "httpx", "version": "0.27.0"},
        ]) + "\n"
    )
    (fixtures / "app-pip.resolve.json").write_text(
        json.dumps({"requests": "2.32.3", "httpx": "0.28.0"}) + "\n"
    )
    return repo


def build_tree(base: Path) -> tuple[Path, dict]:
    tree = base / "tree"
    bin_dir = base / "bin"
    npm_fixtures = base / "npm-fixtures"
    pip_fixtures = base / "pip-fixtures"
    for directory in (tree, bin_dir, npm_fixtures, pip_fixtures):
        directory.mkdir(parents=True)

    make_node_repo(tree, npm_fixtures, "app-npm", NPM_PACKAGE, NPM_OUTDATED)
    make_node_repo(tree, npm_fixtures, "app-pnpm", PNPM_PACKAGE, PNPM_OUTDATED, pnpm=True)
    make_pip_repo(tree, pip_fixtures)
    dirty = make_node_repo(
        tree,
        npm_fixtures,
        "app-dirty",
        {"name": "app-dirty", "dependencies": {"left-pad": "^1.2.3"}},
        {"left-pad": {"current": "1.2.3", "wanted": "1.4.0", "latest": "2.0.0"}},
    )
    (dirty / "work-in-progress.txt").write_text("do not touch\n")
    make_node_repo(
        tree,
        npm_fixtures,
        "app-bisect",
        BISECT_PACKAGE,
        BISECT_OUTDATED,
        breaks=BISECT_BREAKS,
    )

    install_stub(NPM_STUB_SOURCE, bin_dir, "npm")
    install_stub(NPM_STUB_SOURCE, bin_dir, "pnpm")
    install_stub(PIP_STUB_SOURCE, bin_dir, "pip")
    install_stub(PIP_STUB_SOURCE, bin_dir, "python3")

    env = dict(os.environ)
    env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"
    env["NPM_STUB_FIXTURES"] = str(npm_fixtures)
    env["NPM_STUB_LOG"] = str(base / "npm-calls.log")
    env["PIP_STUB_FIXTURES"] = str(pip_fixtures)
    env["PIP_STUB_LOG"] = str(base / "pip-calls.log")
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return tree, env


def run_depsweep(tree: Path, argv: list[str]) -> tuple[int, str, str]:
    """Drive the real CLI entry point in process; return (code, out, err)."""
    cli = import_cli()
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli.main([str(tree), *argv])
    return code, out.getvalue(), err.getvalue()


def import_cli():
    sys.path.insert(0, str(ROOT / "src"))
    from depsweep import cli

    return cli


def main() -> int:
    base = Path(tempfile.mkdtemp(prefix="depsweep-smoke-"))
    keep_on_exit = False
    print(f"fixture tree : {base / 'tree'}")
    print(f"node stubs   : {base / 'bin' / 'npm'}, {base / 'bin' / 'pnpm'}")
    print(f"pip stubs    : {base / 'bin' / 'pip'}, {base / 'bin' / 'python3'}")
    print()
    try:
        tree, stub_env = build_tree(base)
        html_path = base / "report.html"
        # Mutate os.environ in place: rebinding the name (os.environ = x)
        # leaves the real process environment untouched, so subprocesses
        # spawned without an explicit env would see the un-stubbed PATH.
        real_env = dict(os.environ)
        os.environ.clear()
        os.environ.update(stub_env)
        try:
            print("--- preview (dry run) ---")
            print("$ depsweep", tree, "--dry-run")
            _, preview_out, _ = run_depsweep(tree, ["--dry-run"])
            print(preview_out.rstrip())
            print()
            print("--- real sweep, planted culprit in app-bisect ---")
            print("$ depsweep", tree, "--html", html_path, "--branch", BRANCH)
            code, out, err = run_depsweep(
                tree, ["--html", str(html_path), "--branch", BRANCH]
            )
            print(out.rstrip())
            if err.strip():
                print(err.rstrip())
            print()
        finally:
            os.environ.clear()
            os.environ.update(real_env)

        checks: list[tuple[str, bool, str]] = []

        def check(name: str, ok: bool, detail: str = "") -> None:
            checks.append((name, ok, detail))

        npm_repo = tree / "app-npm"
        bisect_repo = tree / "app-bisect"
        bisect_pkg = json.loads(
            git(bisect_repo, "show", f"{BRANCH}:package.json")
        )["dependencies"]

        check(
            "exit code 0 (skips allowed, bisection landed the rest)",
            code == 0,
            f"got {code}",
        )
        check(
            "app-bisect names the culprit",
            "reverted: bad-lib broke tests" in out,
        )
        check(
            "app-bisect held bad-lib back at ^1.2.0",
            bisect_pkg.get("bad-lib") == "^1.2.0",
            str(bisect_pkg),
        )
        check(
            "app-bisect landed the other three",
            all(bisect_pkg.get(name) == "^1.1.0" for name in ("aa-lib", "bb-lib", "dd-lib")),
        )
        check(
            "commit message says hold back",
            "hold back bad-lib" in git(bisect_repo, "log", "-1", "--pretty=%s", BRANCH),
        )
        check(
            "app-npm landed its upgrade",
            '"^1.4.0"' in git(npm_repo, "show", f"{BRANCH}:package.json"),
        )
        check("app-dirty was skipped", "skipped (dirty worktree)" in out)
        check(
            "pip repo applied its upgrade",
            "applied" in out and "requests 2.31.0->2.32.3" in out,
        )
        check(
            "repos never left main",
            all(
                git(tree / name, "branch", "--show-current").strip() == "main"
                for name in ("app-npm", "app-pnpm", "app-bisect")
            ),
        )
        check(
            "no worktrees left behind",
            all(len(git(tree / name, "worktree", "list").splitlines()) == 1 for name in ALL_REPOS),
        )
        check(
            "preview ran read-only",
            "dry-run" in preview_out and "app-bisect" in preview_out,
        )
        html = html_path.read_text()
        check(
            "html report written, self-contained",
            html.startswith("<!doctype html>") and "<script" not in html.lower(),
        )
        check(
            "html report under 15KB",
            len(html.encode()) < 15 * 1024,
            f"{len(html.encode())} bytes",
        )
        check("html report shows the culprit", "reverted: bad-lib broke tests" in html)

        failed = [name for name, ok, _ in checks if not ok]
        for name, ok, detail in checks:
            mark = "PASS" if ok else "FAIL"
            suffix = f" ({detail})" if detail and not ok else ""
            print(f"[{mark}] {name}{suffix}")
        print()
        if failed:
            print(f"{len(failed)} check(s) failed; fixture tree kept at {base}")
            keep_on_exit = True
            return 1
        print("all checks passed; bisection caught the planted culprit")
        return 0
    finally:
        if not keep_on_exit:
            shutil.rmtree(base, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
