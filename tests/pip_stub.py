#!/usr/bin/env python3
"""Offline pip and python3 stub for tests and the smoke run. No network.

One script plays two roles, picked from argv[0]:

- ``python3``: answers ``python3 -m venv DIR`` by creating ``DIR/bin`` and
  installing copies of itself there as ``pip`` (so the scratch venv is
  sweepable offline). Any other invocation runs the named script, if one is
  given, so shebang chains keep working; otherwise it exits 0.
- ``pip``: answers the calls depsweep makes, driven by the repo directory
  name (the basename of cwd) plus two environment variables:

  - ``PIP_STUB_FIXTURES``: directory holding canned responses.
  - ``PIP_STUB_LOG``: file every invocation appends one line to, as
    ``<repo>: pip <args...>``.

Fixture files, keyed by repo name:

- ``<repo>.list.json``      body printed for ``pip list --format json``;
                            missing file means an empty environment (``[]``).
- ``<repo>.resolve.json``   object mapping requirement name to the version
                            the resolver should report. Names absent from
                            the map resolve to nothing.
- ``<repo>.resolve.exit``   overrides the resolver exit code (default 0).
- ``<repo>.install.exit``   exit code for any real install (default 0).
- ``<repo>.check.exit``     exit code for ``pip check`` (default 0).

The resolver answer is printed as a pip installation report: depsweep reads
back only the entry whose name matches the requirement, so fixtures may list
extra names to prove transitive noise is ignored.
"""

import json
import os
import re
import runpy
import shutil
import sys
from pathlib import Path

FIXTURES = Path(os.environ["PIP_STUB_FIXTURES"])
LOG = os.environ.get("PIP_STUB_LOG")
REPO = Path.cwd().name
NAME = Path(sys.argv[0]).name

_REQ_NAME = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)")


def log_invocation(args):
    if LOG:
        with open(LOG, "a") as fh:
            fh.write(f"{REPO}: pip {' '.join(args)}\n")


def override_exit(kind, default):
    path = FIXTURES / f"{REPO}.{kind}.exit"
    if path.exists():
        return int(path.read_text().strip())
    return default


def requirement_in(args):
    """The requirement text of a ``-r FILE`` argument, or None."""
    for index, arg in enumerate(args):
        if arg == "-r" and index + 1 < len(args):
            try:
                return Path(args[index + 1]).read_text().strip()
            except OSError:
                return None
    return None


def cmd_list():
    fixture = FIXTURES / f"{REPO}.list.json"
    if fixture.exists():
        print(fixture.read_text(), end="")
    else:
        print("[]")
    return 0


def cmd_resolve(args):
    text = requirement_in(args) or ""
    match = _REQ_NAME.match(text)
    name = match.group(1).lower() if match else ""
    versions = {}
    resolve_fixture = FIXTURES / f"{REPO}.resolve.json"
    if resolve_fixture.exists():
        try:
            loaded = json.loads(resolve_fixture.read_text())
            if isinstance(loaded, dict):
                versions = {str(k).lower(): str(v) for k, v in loaded.items()}
        except json.JSONDecodeError:
            versions = {}
    install = []
    version = versions.get(name)
    if version is not None:
        install.append({"metadata": {"name": name, "version": version}})
    # One decoy dependency: depsweep must pick its own package, not this.
    if "requests" in versions and name != "requests":
        install.append({"metadata": {"name": "some-transitive-dep", "version": "9.9.9"}})
    print(json.dumps({"install": install}))
    return override_exit("resolve", 0)


def cmd_install(args):
    code = override_exit("install", 0)
    text = requirement_in(args) or ""
    if text:
        # Log the flags plus the requirement text, never the temp file path.
        flags = [a for a in args if a != "-r" and not a.startswith("/")]
        if LOG:
            with open(LOG, "a") as fh:
                fh.write(f"{REPO}: pip {' '.join(flags)} :: {text}\n")
    return code


def cmd_check():
    return override_exit("check", 0)


def pip_main(args):
    log_invocation(args)
    if not args:
        return 0
    if args[0] == "list":
        return cmd_list()
    if args[0] == "install":
        if "--dry-run" in args:
            return cmd_resolve(args)
        return cmd_install(args)
    if args[0] == "check":
        return cmd_check()
    return 0


def python3_main(args):
    if len(args) >= 3 and args[0] == "-m" and args[1] == "venv":
        venv_bin = Path(args[2]) / "bin"
        venv_bin.mkdir(parents=True, exist_ok=True)
        for role in ("pip", "python3"):
            stub = venv_bin / role
            shutil.copy(Path(__file__), stub)
            stub.chmod(0o755)
        return 0
    if args and Path(args[0]).is_file():
        try:
            runpy.run_path(args[0], run_name="__main__")
        except SystemExit as exc:  # run the target script faithfully
            return exc.code or 0
        return 0
    return 0


def main():
    args = sys.argv[1:]
    if NAME in ("python", "python3"):
        return python3_main(args)
    return pip_main(args)


if __name__ == "__main__":
    raise SystemExit(main())
