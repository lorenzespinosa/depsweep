#!/usr/bin/env python3
"""Offline npm/pnpm stub for tests and the smoke run. Never touches network.

Installed as ``npm`` and ``pnpm`` on PATH, ahead of any real ones. Which
tool it pretends to be comes from argv[0]; behaviour is otherwise identical
because depsweep drives both with the same shapes. State is driven by the
repo directory name (the basename of cwd) plus two environment variables:

- ``NPM_STUB_FIXTURES``: directory holding canned responses.
- ``NPM_STUB_LOG``: file every invocation is appended to, one line per call,
  as ``<repo>: <npm|pnpm> <args...>``.

Fixture files, keyed by repo name (shared by both tools; a repo is swept by
exactly one of them):

- ``<repo>.outdated.json``  body printed for ``npm|pnpm outdated --json``.
  Missing file means nothing is outdated (``{}`` and exit 0). Exit code
  defaults to 1 when the body names packages and 0 when it is empty,
  matching real npm and pnpm.
- ``<repo>.outdated.exit``  overrides the outdated exit code.
- ``<repo>.install.exit``   exit code for any install/add (default 0).
- ``<repo>.add.exit``       same, but only heard for ``pnpm add``.
- ``<repo>.test.exit``      exit code for ``run test`` (default 0).
- ``<repo>.build.exit``     exit code for ``run build`` (default 0).

``install|add name@version`` rewrites that dependency in package.json to
``^version`` (with a leading caret, npm's usual save prefix). That is what
makes repos genuinely dirty, so revert behaviour is tested against real
working-tree changes.
"""

import json
import os
import sys
from pathlib import Path

FIXTURES = Path(os.environ["NPM_STUB_FIXTURES"])
LOG = os.environ.get("NPM_STUB_LOG")
REPO = Path.cwd().name
TOOL = Path(sys.argv[0]).name  # "npm" or "pnpm": same shapes either way


def log_invocation(args):
    if LOG:
        with open(LOG, "a") as fh:
            fh.write(f"{REPO}: {TOOL} {' '.join(args)}\n")


def override_exit(kind, default):
    path = FIXTURES / f"{REPO}.{kind}.exit"
    if path.exists():
        return int(path.read_text().strip())
    return default


def rewrite_package_json(install_args):
    """Apply each ``name@version`` argument to package.json."""
    pkg = Path("package.json")
    if not pkg.exists():
        return
    try:
        data = json.loads(pkg.read_text())
    except json.JSONDecodeError:
        return
    changed = False
    for arg in install_args:
        if not arg or arg.startswith("-"):
            continue
        name, sep, version = arg.rpartition("@")
        if arg.startswith("@"):  # scoped package: @scope/pkg@1.2.3
            name = "@" + name.lstrip("@")
        if not sep or not name or not version or not version[0].isdigit():
            continue
        for section in ("dependencies", "devDependencies"):
            block = data.get(section)
            if isinstance(block, dict) and name in block:
                block[name] = f"^{version}"
                changed = True
    if changed:
        pkg.write_text(json.dumps(data, indent=2) + "\n")


def cmd_outdated():
    fixture = FIXTURES / f"{REPO}.outdated.json"
    if fixture.exists():
        body = fixture.read_text()
        print(body)
        try:
            data = json.loads(body) if body.strip() else {}
        except json.JSONDecodeError:
            data = None
        default = 1 if isinstance(data, dict) and data else 0
        return override_exit("outdated", default)
    print("{}")
    return override_exit("outdated", 0)


def cmd_install(args):
    # Works for npm "install pkg@ver" and pnpm "add pkg@ver" alike. The
    # exit override may name the command heard ("add") or plain "install".
    code = override_exit(args[0], override_exit("install", 0))
    if code == 0 and len(args) > 1:
        rewrite_package_json(args[1:])
    return code


def cmd_run(args):
    script = args[1] if len(args) > 1 else ""
    return override_exit(script, 0) if script else 0


def main():
    args = sys.argv[1:]
    log_invocation(args)
    if not args:
        return 0
    if args[0] == "outdated":
        return cmd_outdated()
    if args[0] in ("install", "add"):
        return cmd_install(args)
    if args[0] == "run":
        return cmd_run(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
