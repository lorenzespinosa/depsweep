"""Command line entry point.

Exit codes: 0 ran (skips allowed), 1 at least one failed repo, 2 usage error.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

from . import report, sweep
from .discover import find_repos

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_USAGE = 2


def default_branch_name() -> str:
    return f"dep-sweep-{date.today().strftime('%Y%m%d')}"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="depsweep",
        description=(
            "Safely upgrade in-range npm dependencies across many local git "
            "repos, one repo at a time, with verification and auto-revert."
        ),
    )
    parser.add_argument(
        "root",
        help="directory to scan for git repos (a directory with .git and package.json)",
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
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    root = Path(args.root).expanduser().resolve()
    if not root.is_dir():
        parser.error(f"root path is not a directory: {args.root}")
    if args.max_depth is not None and args.max_depth < 0:
        parser.error("--max-depth must be >= 0")
    branch = args.branch or default_branch_name()

    repos = find_repos(root, args.max_depth)
    if not repos:
        print(f"no repos found under {root}", file=sys.stderr)

    results = []
    for index, repo in enumerate(repos, start=1):
        rel = repo.name if repo == root else str(repo.relative_to(root))
        print(f"[{index}/{len(repos)}] {rel}", file=sys.stderr)
        results.append(sweep.sweep_repo(repo, rel, branch, args.dry_run))

    if args.json:
        print(report.render_json(report.to_json_payload(root, branch, args.dry_run, results)))
    else:
        print(report.render_table(results))
        print(report.summary_line(results))

    if any(r.failed for r in results):
        return EXIT_FAILED
    return EXIT_OK
