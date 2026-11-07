"""Command line entry point.

Exit codes: 0 ran (skips allowed), 1 at least one failed repo, 2 usage error.
"""

from __future__ import annotations

import argparse
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from pathlib import Path

from . import report, sweep
from .discover import filter_repos, find_repos, rel_name
from .models import RepoResult

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_USAGE = 2

DEFAULT_JOBS = 4


def default_branch_name() -> str:
    return f"dep-sweep-{date.today().strftime('%Y%m%d')}"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="depsweep",
        description=(
            "Safely upgrade in-range dependencies across many local git "
            "repos, one repo at a time, with verification and auto-revert. "
            "npm and pnpm repos get a sweep branch; pip repos (requirements.txt "
            "or PEP 621 pyproject.toml) get their venv upgraded."
        ),
    )
    parser.add_argument(
        "root",
        help=(
            "directory to scan for git repos (a directory with .git plus "
            "package.json, pnpm-lock.yaml, requirements.txt, or a PEP 621 "
            "pyproject.toml)"
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print what would happen, change nothing",
    )
    parser.add_argument(
        "--branch",
        metavar="NAME",
        default=None,
        help="sweep branch name (default: dep-sweep-YYYYMMDD)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="print a machine-readable JSON report instead of the table",
    )
    parser.add_argument(
        "--max-depth",
        type=int,
        metavar="N",
        default=None,
        help="how many directory levels below root to scan (default: no limit)",
    )
    parser.add_argument(
        "--include",
        action="append",
        default=[],
        metavar="PATTERN",
        help=(
            "only sweep repos whose path relative to root matches this glob "
            "(repeatable; * also matches across /)"
        ),
    )
    parser.add_argument(
        "--exclude",
        action="append",
        default=[],
        metavar="PATTERN",
        help=(
            "skip repos whose path relative to root matches this glob "
            "(repeatable; wins over --include)"
        ),
    )
    parser.add_argument(
        "--jobs",
        type=int,
        metavar="N",
        default=DEFAULT_JOBS,
        help=f"how many repos to sweep in parallel (default: {DEFAULT_JOBS})",
    )
    return parser


def _sweep_all(
    repos: list[Path], root: Path, branch: str, dry_run: bool, jobs: int
) -> list[RepoResult]:
    """Sweep every repo, ``jobs`` at a time. Results come back in discovery
    order whatever the completion order was, so the table is deterministic.
    Repos are independent directories, so parallel sweeps cannot step on
    each other."""
    total = len(repos)
    results: list[RepoResult | None] = [None] * total
    if jobs <= 1 or total <= 1:
        for index, repo in enumerate(repos):
            rel = rel_name(root, repo)
            print(f"[{index + 1}/{total}] {rel}", file=sys.stderr)
            results[index] = sweep.sweep_repo(repo, rel, branch, dry_run)
    else:
        with ThreadPoolExecutor(max_workers=jobs) as pool:
            futures = {
                pool.submit(sweep.sweep_repo, repo, rel_name(root, repo), branch, dry_run): index
                for index, repo in enumerate(repos)
            }
            for future in as_completed(futures):
                index = futures[future]
                results[index] = future.result()
                # Numbered by discovery position; lines appear as repos finish.
                print(
                    f"[{index + 1}/{total}] {rel_name(root, repos[index])}",
                    file=sys.stderr,
                )
    return [r for r in results if r is not None]


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    root = Path(args.root).expanduser().resolve()
    if not root.is_dir():
        parser.error(f"root path is not a directory: {args.root}")
    if args.max_depth is not None and args.max_depth < 0:
        parser.error("--max-depth must be >= 0")
    if args.jobs < 1:
        parser.error("--jobs must be >= 1")
    branch = args.branch or default_branch_name()

    repos = filter_repos(find_repos(root, args.max_depth), root, args.include, args.exclude)
    if not repos:
        print(f"no repos found under {root}", file=sys.stderr)

    results = _sweep_all(repos, root, branch, args.dry_run, args.jobs)

    if args.json:
        print(report.render_json(report.to_json_payload(root, branch, args.dry_run, results)))
    else:
        print(report.render_table(results))
        print(report.summary_line(results))

    if any(r.failed for r in results):
        return EXIT_FAILED
    return EXIT_OK
