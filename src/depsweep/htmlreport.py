"""The --html report: one self-contained file, inline CSS, no scripts, no
external assets. Everything it needs to display travels inside it, so it
can be mailed, archived, or opened from disk with no network.

Small by construction: a 50-repo sweep, with several findings per repo,
must stay under 15KB. To fit, the summary table carries only the state word
(no reasons, no verify detail) and each per-repo detail block is two short
lines: the full state, then one compact line of upgrade lists. The branch
name and the run totals are printed once, not per repo. Values that come
from a repo (names, reasons, versions) are escaped individually; joining
punctuation, including the -> arrow, is depsweep's own and stays literal.
"""

from __future__ import annotations

from html import escape
from pathlib import Path

from .models import RepoResult
from .report import state_word, summary_line, verify_text

_CSS = (
    "body{font-family:system-ui,sans-serif;max-width:60rem;margin:1.5rem auto;"
    "color:#1b1b1b}\n"
    "h1{font-size:1.4rem;margin:0 0 .2rem}h2{font-size:1.1rem;margin:1.4rem 0 .3rem}\n"
    "table{border-collapse:collapse;width:100%}\n"
    "th,td{border:1px solid #d6d6d6;padding:.25rem .5rem;text-align:left;"
    "font-size:.9rem}\n"
    "th{background:#f0f0f0}\n"
    "tr.b td{background:#fdecea}tr.w td{background:#fff6e0}\n"
    ".m{color:#555}\n"
    ".r{border-top:1px solid #e2e2e2;padding:.4rem 0}\n"
    ".r p{margin:.2rem 0 0}\n"
    ".r p+p{font-size:.9rem;color:#333}\n"
)


def _esc(text: str) -> str:
    return escape(str(text), quote=True)


def _move(name: str, current: str, wanted: str) -> str:
    # "name 1.2.3->1.4.0" (or "name new->1.4.0" for a fresh install).
    return f"{_esc(name)} {_esc(current or 'new')}->{_esc(wanted)}"


def _row_class(result: RepoResult) -> str:
    if result.failed:
        return ' class="b"'
    if result.status in ("skipped", "dry-run"):
        return ' class="w"'
    return ""


def _state_with_reason(result: RepoResult) -> str:
    word = state_word(result)
    if result.reason:
        return f"{word} ({_esc(result.reason)})"
    return word


def _detail(result: RepoResult) -> str:
    """One block per repo: full state line, then one line of upgrade lists."""
    state = _state_with_reason(result)
    if result.verify is not None:
        state += f" · {_esc(verify_text(result))}"
    lists = []
    if result.upgrades:
        lists.append(
            "upgraded " + "; ".join(_move(u.name, u.current, u.wanted) for u in result.upgrades)
        )
    if result.reverted:
        lists.append(
            "reverted " + "; ".join(_move(u.name, u.current, u.wanted) for u in result.reverted)
        )
    if result.majors:
        lists.append(
            "majors " + "; ".join(_move(m.name, m.current, m.wanted) for m in result.majors)
        )
    if result.held:
        lists.append(
            "held "
            + "; ".join(f"{_esc(h.name)} ({_esc(h.reason)})" for h in result.held)
        )
    # The list line joins already-escaped pieces with depsweep's own
    # punctuation, so it is written as-is.
    lists_line = f'<p>{", ".join(lists)}</p>' if lists else "<p>nothing to do</p>"
    return f'<div class="r"><p><b>{_esc(result.repo)}</b> {state}</p>{lists_line}</div>'


def render_html(
    results: list[RepoResult], root: Path, branch: str, dry_run: bool
) -> str:
    """The whole report as one HTML string."""
    meta = [f"{len(results)} repo(s) under {root}", f"branch {branch}"]
    if dry_run:
        meta.append("dry run, nothing was changed")
    meta.append(summary_line(results))

    head = "".join(f"<th>{name}</th>" for name in ("repo", "manager", "state"))
    rows = []
    for r in results:
        cells = "".join(
            f"<td>{cell}</td>"
            for cell in (_esc(r.repo), _esc(r.manager), _esc(state_word(r)))
        )
        rows.append(f"<tr{_row_class(r)}>{cells}</tr>")
    summary = (
        "<h2>Summary</h2><table><thead><tr>"
        f"{head}</tr></thead><tbody>{''.join(rows)}</tbody></table>"
    )

    details = "".join(_detail(r) for r in results)
    detail_html = (
        f"<h2>Per-repo detail</h2>{details}"
        if details
        else '<h2>Per-repo detail</h2><p class="m">no repos found</p>'
    )

    return (
        '<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        "<title>depsweep report</title>\n<style>\n"
        f"{_CSS}</style>\n</head>\n<body>\n<h1>depsweep report</h1>\n"
        f'<p class="m">{_esc(" · ".join(meta))}</p>\n'
        f"{summary}\n{detail_html}\n</body>\n</html>\n"
    )


def write_html(
    path: Path, results: list[RepoResult], root: Path, branch: str, dry_run: bool
) -> None:
    """Render and write the report. OSError is the caller's to report."""
    path.write_text(
        render_html(results, root, branch, dry_run), encoding="utf-8"
    )
