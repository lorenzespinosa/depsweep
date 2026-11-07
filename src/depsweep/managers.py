"""Which package manager owns a repo, and the adapters for the node ones."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import npm, pip, pnpm

NPM = "npm"
PNPM = "pnpm"
PIP = "pip"


def detect(repo: Path) -> str | None:
    """One manager per repo, decided by the strongest marker present.

    pnpm-lock.yaml next to a package.json means pnpm owns the repo. A bare
    package.json means npm. requirements.txt or a PEP 621 pyproject.toml
    means pip. A repo carrying markers for several managers is swept by the
    first one that wins here, the others are ignored for that pass.
    """
    has_package_json = (repo / "package.json").is_file()
    if (repo / "pnpm-lock.yaml").is_file() and has_package_json:
        return PNPM
    if has_package_json:
        return NPM
    if pip.has_pip_project(repo):
        return PIP
    return None


@dataclass(frozen=True)
class NodeTool:
    """The four operations the shared package.json sweep flow needs."""

    name: str
    outdated: Callable[[Path], tuple[dict, str | None]]
    install: Callable[[Path, str, str], subprocess.CompletedProcess]
    run: Callable[[Path, str], subprocess.CompletedProcess]
    bare_install: Callable[[Path], subprocess.CompletedProcess]


NPM_TOOL = NodeTool(
    name=NPM,
    outdated=npm.npm_outdated,
    install=npm.npm_install,
    run=npm.npm_run,
    bare_install=npm.npm_bare_install,
)

PNPM_TOOL = NodeTool(
    name=PNPM,
    outdated=pnpm.pnpm_outdated,
    install=pnpm.pnpm_add,
    run=pnpm.pnpm_run,
    bare_install=pnpm.pnpm_install,
)


def tool_for(manager: str) -> NodeTool:
    return {NPM: NPM_TOOL, PNPM: PNPM_TOOL}[manager]
