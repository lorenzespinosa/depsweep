"""Find sweepable repos under a root directory, and filter them by glob."""

from __future__ import annotations

import os
from fnmatch import fnmatch
from pathlib import Path

from .managers import detect

_SKIP_DIRS = {"node_modules"}


def is_repo(path: Path) -> bool:
    """A sweepable repo: has a .git entry (dir or worktree file) plus a
    marker depsweep understands (package.json, pnpm-lock.yaml alongside it,
    requirements.txt, or a PEP 621 pyproject.toml)."""
    return (path / ".git").exists() and detect(path) is not None


def rel_name(root: Path, repo: Path) -> str:
    """How a repo is named in output: its path relative to root."""
    return repo.name if repo == root else str(repo.relative_to(root))


def find_repos(root: Path, max_depth: int | None = None) -> list[Path]:
    """Walk ``root`` and return sorted repo paths.

    Never descends into a repo it found, into node_modules, into hidden
    directories, or through symlinks. If ``root`` itself is a repo, only
    ``root`` is returned. ``max_depth`` is the number of directory levels
    below root to scan (0 means root only).
    """
    repos: list[Path] = []
    if is_repo(root):
        repos.append(root)
        return repos

    def walk(directory: Path, depth: int) -> None:
        if max_depth is not None and depth >= max_depth:
            return
        try:
            entries = sorted(os.scandir(directory), key=lambda e: e.name)
        except OSError:
            return
        for entry in entries:
            if not entry.is_dir(follow_symlinks=False):
                continue
            if entry.name.startswith(".") or entry.name in _SKIP_DIRS:
                continue
            child = directory / entry.name
            if is_repo(child):
                repos.append(child)  # found a repo; do not look inside it
            else:
                walk(child, depth + 1)

    walk(root, 0)
    return repos


def filter_repos(
    repos: list[Path],
    root: Path,
    include: list[str] | None = None,
    exclude: list[str] | None = None,
) -> list[Path]:
    """Keep repos whose path relative to root passes the glob filters.

    A repo is kept when it matches at least one --include pattern (if any
    are given) and no --exclude pattern. Patterns are fnmatch globs against
    the relative path; ``*`` also matches across ``/``.
    """
    include = include or []
    exclude = exclude or []
    kept: list[Path] = []
    for repo in repos:
        rel = rel_name(root, repo)
        if include and not any(fnmatch(rel, pattern) for pattern in include):
            continue
        if any(fnmatch(rel, pattern) for pattern in exclude):
            continue
        kept.append(repo)
    return kept
