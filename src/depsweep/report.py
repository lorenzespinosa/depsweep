"""Rendering: the human table (fixed column order) and the --json report."""

from __future__ import annotations

import json
from pathlib import Path

from .models import (
    STATE_APPLIED,
    STATE_BRANCH_LEFT,
    STATE_CLEAN,
    STATE_DRY_RUN,
    STATE_ERROR,
    STATE_REVERTED,
    STATE_SKIPPED,
    RepoResult,
)

COLUMNS = ("repo", "upgraded", "majors", "held", "verify", "state")

_MAX_CELL = 60

_STATE_TEXT = {
    STATE_APPLIED: "applied",
    STATE_BRANCH_LEFT: "branch left",
    STATE_REVERTED: "reverted",
    STATE_SKIPPED: "skipped",
    STATE_ERROR: "error",
    STATE_CLEAN: "clean",
    STATE_DRY_RUN: "dry-run",
}


def _clip(text: str) -> str:
    if len(text) > _MAX_CELL:
        return text[: _MAX_CELL - 3] + "..."
    return text


def _list_cell(items: list[str]) -> str:
    return _clip("; ".join(items)) if items else "-"


def _upgrade_text(u) -> str:
    # A pip fresh install has no current version; "new" reads in the table.
    return f"{u.name} {u.current or 'new'}->{u.wanted}"


def _held_text(h) -> str:
    return f"{h.name} ({h.reason})"


def _verify_text(result: RepoResult) -> str:
    if result.verify is None:
        return "-"
    outcome = "ok" if result.verify.passed else "failed"
    return f"{result.verify.step} {outcome}"


def _state_text(result: RepoResult) -> str:
    text = _STATE_TEXT.get(result.status, result.status)
    if result.status == STATE_BRANCH_LEFT and result.branch:
        text = f"branch left: {result.branch}"
    if result.reason:
        text = f"{text} ({result.reason})"
    return _clip(text)


def render_table(results: list[RepoResult]) -> str:
    """The human summary table. Column order is fixed: repo | upgraded |
    majors | held | verify | state."""
    rows = [
        [
            _clip(r.repo),
            _list_cell([_upgrade_text(u) for u in r.upgrades]),
            _list_cell([_upgrade_text(m) for m in r.majors]),
            _list_cell([_held_text(h) for h in r.held]),
            _verify_text(r),
            _state_text(r),
        ]
        for r in results
    ]
    widths = [
        max(len(header), *(len(row[i]) for row in rows)) if rows else len(header)
        for i, header in enumerate(COLUMNS)
    ]
    lines = [
        "  ".join(header.ljust(widths[i]) for i, header in enumerate(COLUMNS)).rstrip(),
        "  ".join("-" * width for width in widths),
    ]
    for row in rows:
        lines.append(
            "  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)).rstrip()
        )
    return "\n".join(lines)


def summary_line(results: list[RepoResult]) -> str:
    if not results:
        return "0 repos found"
    counts = {
        "branch left": 0,
        "applied": 0,
        "clean": 0,
        "skipped": 0,
        "failed": 0,
        "dry-run": 0,
    }
    for r in results:
        if r.status == STATE_BRANCH_LEFT:
            counts["branch left"] += 1
        elif r.status == STATE_APPLIED:
            counts["applied"] += 1
        elif r.status == STATE_CLEAN:
            counts["clean"] += 1
        elif r.status == STATE_SKIPPED:
            counts["skipped"] += 1
        elif r.status == STATE_DRY_RUN:
            counts["dry-run"] += 1
        else:  # reverted or error
            counts["failed"] += 1
    parts = [f"{count} {label}" for label, count in counts.items() if count]
    return f"{len(results)} repo(s): " + ", ".join(parts)


def _repo_payload(r: RepoResult) -> dict:
    return {
        "repo": r.repo,
        "manager": r.manager,
        "upgraded": [
            {"name": u.name, "from": u.current, "to": u.wanted} for u in r.upgrades
        ],
        "majors": [
            {"name": m.name, "from": m.current, "to": m.wanted} for m in r.majors
        ],
        "held": [
            {
                "name": h.name,
                "current": h.current,
                "wanted": h.wanted,
                "reason": h.reason,
            }
            for h in r.held
        ],
        "verify": (
            {"step": r.verify.step, "passed": r.verify.passed}
            if r.verify is not None
            else None
        ),
        "state": {"status": r.status, "branch": r.branch, "reason": r.reason},
    }


def to_json_payload(
    root: Path, branch: str, dry_run: bool, results: list[RepoResult]
) -> dict:
    """The machine-readable report. Same fields as the table."""
    return {
        "root": str(root),
        "branch": branch,
        "dry_run": dry_run,
        "repos": [_repo_payload(r) for r in results],
        "totals": {
            "repos": len(results),
            "branch_left": sum(
                1 for r in results if r.status == STATE_BRANCH_LEFT
            ),
            "applied": sum(1 for r in results if r.status == STATE_APPLIED),
            "clean": sum(1 for r in results if r.status == STATE_CLEAN),
            "skipped": sum(1 for r in results if r.status == STATE_SKIPPED),
            "failed": sum(
                1 for r in results
                if r.status in (STATE_REVERTED, STATE_ERROR)
            ),
        },
    }


def render_json(payload: dict) -> str:
    return json.dumps(payload, indent=2)
