"""Every npm call depsweep makes, plus package.json reading."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path


def _npm(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["npm", *args], cwd=repo, capture_output=True, text=True, check=False
    )


def read_package_json(repo: Path) -> dict:
    try:
        data = json.loads((repo / "package.json").read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def npm_scripts(package_json: dict) -> dict[str, str]:
    scripts = package_json.get("scripts") or {}
    return scripts if isinstance(scripts, dict) else {}


def npm_outdated(repo: Path) -> tuple[dict, str | None]:
    """Run ``npm outdated --json``.

    npm exits 1 whenever anything is outdated, so any exit code is fine as
    long as valid JSON comes back. Only missing or invalid JSON is an error.
    Returns ``(data, error)``; exactly one of them is meaningful.
    """
    proc = _npm(repo, "outdated", "--json")
    stdout = proc.stdout.strip()
    if not stdout:
        if proc.returncode in (0, 1):
            return {}, None
        return {}, f"npm outdated exited {proc.returncode} with no output"
    try:
        data = json.loads(stdout)
    except json.JSONDecodeError:
        return {}, f"npm outdated printed invalid JSON (exit {proc.returncode})"
    if not isinstance(data, dict):
        return {}, "npm outdated JSON was not an object"
    return data, None


def npm_install(repo: Path, name: str, version: str) -> subprocess.CompletedProcess[str]:
    return _npm(repo, "install", f"{name}@{version}")


def npm_run(repo: Path, script: str) -> subprocess.CompletedProcess[str]:
    return _npm(repo, "run", script, "--silent")


def npm_bare_install(repo: Path) -> subprocess.CompletedProcess[str]:
    return _npm(repo, "install")
