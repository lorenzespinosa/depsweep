"""Every pnpm call depsweep makes.

pnpm's ``outdated`` exits 1 whenever anything is outdated, exactly like npm,
so the same "exit 1 is data, only bad JSON is an error" contract applies.
The JSON shape has varied across pnpm releases: newer releases print an
object keyed by package name (npm's shape), older ones an array of objects
carrying a ``name`` field. Both are normalized to the object shape.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path


def _pnpm(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    # Argument list form, never a shell string (shell is False by default).
    return subprocess.run(
        ["pnpm", *args], cwd=repo, capture_output=True, text=True, check=False
    )


def pnpm_outdated(repo: Path) -> tuple[dict, str | None]:
    """Run ``pnpm outdated --format json``. Returns ``(data, error)``."""
    proc = _pnpm(repo, "outdated", "--format", "json")
    stdout = proc.stdout.strip()
    if not stdout:
        if proc.returncode in (0, 1):
            return {}, None
        return {}, f"pnpm outdated exited {proc.returncode} with no output"
    try:
        data = json.loads(stdout)
    except json.JSONDecodeError:
        return {}, f"pnpm outdated printed invalid JSON (exit {proc.returncode})"
    if isinstance(data, list):
        data = {
            item["name"]: item
            for item in data
            if isinstance(item, dict) and isinstance(item.get("name"), str)
        }
    if not isinstance(data, dict):
        return {}, "pnpm outdated JSON was not an object"
    return data, None


def pnpm_add(repo: Path, name: str, version: str) -> subprocess.CompletedProcess[str]:
    """Install one package at an exact version and save it to package.json.

    ``pnpm add`` is pnpm's way to do what ``npm install pkg@version`` does;
    bare ``pnpm install`` takes no package arguments.
    """
    return _pnpm(repo, "add", f"{name}@{version}")


def pnpm_run(repo: Path, script: str) -> subprocess.CompletedProcess[str]:
    return _pnpm(repo, "run", script, "--silent")


def pnpm_install(repo: Path) -> subprocess.CompletedProcess[str]:
    return _pnpm(repo, "install")
