"""The --html report: self-contained, inline CSS only, no scripts, small."""

from pathlib import Path

from depsweep.htmlreport import render_html, write_html
from depsweep.models import (
    STATE_APPLIED,
    STATE_BRANCH_LEFT,
    STATE_CLEAN,
    STATE_ERROR,
    STATE_REVERTED,
    STATE_SKIPPED,
    Held,
    Major,
    RepoResult,
    Upgrade,
    VerifyResult,
)

GOLDEN = Path(__file__).parent / "fixtures" / "expected-report.html"
ROOT = Path("/tmp/demo")
BRANCH = "dep-sweep-20261107"


def golden_results() -> list[RepoResult]:
    return [
        RepoResult(
            repo="app-npm",
            status=STATE_BRANCH_LEFT,
            branch="dep-sweep-20261107",
            upgrades=(
                Upgrade("left-pad", "1.2.3", "1.4.0"),
                Upgrade("tool-kit", "0.4.0", "0.9.1"),
            ),
            majors=(Major("big-lib", "1.9.0", "2.5.0"),),
            held=(Held("pinned-kit", "3.1.0", "3.2.0", "exact pin"),),
            verify=VerifyResult("test", True),
        ),
        RepoResult(
            repo="app-bisect",
            status=STATE_BRANCH_LEFT,
            branch="dep-sweep-20261107",
            reason="reverted: bad-lib broke tests",
            upgrades=(Upgrade("ok-lib", "1.0.0", "1.1.0"),),
            reverted=(Upgrade("bad-lib", "2.0.0", "2.1.0"),),
            verify=VerifyResult("test", True),
        ),
        RepoResult(
            repo="app-pip",
            status=STATE_APPLIED,
            manager="pip",
            upgrades=(Upgrade("requests", "2.31.0", "2.32.3"),),
            verify=VerifyResult("pip check", True),
        ),
        RepoResult(
            repo="app-dirty",
            status=STATE_SKIPPED,
            reason="dirty worktree",
        ),
        RepoResult(repo="app-quiet", status=STATE_CLEAN),
    ]


def test_matches_the_golden_file():
    """Byte-for-byte stable output: change the renderer, change this file."""
    assert render_html(golden_results(), ROOT, BRANCH, dry_run=False) == (
        GOLDEN.read_text()
    )


def test_self_contained_no_scripts_no_external_assets():
    html = render_html(golden_results(), ROOT, BRANCH, dry_run=False)
    lowered = html.lower()
    assert "<script" not in lowered
    assert "onerror" not in lowered
    assert "http://" not in lowered
    assert "https://" not in lowered
    assert 'src="' not in lowered  # no images, no linked files
    assert "<link" not in lowered
    assert html.count("<style>") == 1  # inline CSS, exactly once
    assert html.startswith("<!doctype html>")


def test_carries_summary_detail_and_lists():
    html = render_html(golden_results(), ROOT, BRANCH, dry_run=True)
    assert "depsweep report" in html
    assert "Summary" in html and "Per-repo detail" in html
    # Summary table columns and rows (terse: state word only).
    for heading in ("<th>repo</th>", "<th>manager</th>", "<th>state</th>"):
        assert heading in html
    assert "<td>app-npm</td>" in html
    assert "branch dep-sweep-20261107" in html  # said once, in the meta line
    assert "test ok" in html  # the gate outcome, in the detail
    # Upgrade lists spell out the moves.
    assert "left-pad 1.2.3->1.4.0" in html
    assert "big-lib 1.9.0->2.5.0" in html
    assert "pinned-kit (exact pin)" in html
    assert "bad-lib 2.0.0->2.1.0" in html
    assert "requests 2.31.0->2.32.3" in html
    # Skipped repo shows its reason, clean repo shows nothing to do.
    assert "dirty worktree" in html
    assert "nothing to do" in html
    # Dry run is said plainly.
    assert "dry run, nothing was changed" in html


def test_escapes_repo_names_and_reasons():
    hostile = RepoResult(
        repo="<script>alert(1)</script>",
        status=STATE_ERROR,
        reason="broke <b>on</b> purpose & badly",
    )
    html = render_html([hostile], ROOT, BRANCH, dry_run=False)
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html
    assert "<b>on</b>" not in html  # the reason's markup is neutralised
    assert "broke &lt;b&gt;on&lt;/b&gt; purpose &amp; badly" in html


def test_fifty_repo_report_stays_under_15kb():
    results = []
    for i in range(50):
        results.append(
            RepoResult(
                repo=f"repo-number-{i:02d}",
                status=STATE_BRANCH_LEFT if i % 4 else STATE_REVERTED,
                branch="dep-sweep-20261107",
                reason=None if i % 4 else "reverted: pkg-3 broke tests",
                upgrades=tuple(
                    Upgrade(f"pkg-{j}", f"1.{j}.0", f"1.{j}.1") for j in range(3)
                ),
                reverted=(Upgrade("pkg-3", "1.3.0", "1.3.1"),) if i % 4 == 0 else (),
                majors=(Major("pkg-big", "1.0.0", "2.0.0"),),
                held=(Held("pkg-pin", "2.0.0", "2.5.0", "exact pin"),),
                verify=VerifyResult("test", bool(i % 4)),
            )
        )
    html = render_html(results, ROOT, BRANCH, dry_run=False)
    assert len(html.encode("utf-8")) < 15 * 1024


def test_write_html_writes_the_rendered_string(tmp_path):
    target = tmp_path / "report.html"
    write_html(target, golden_results(), ROOT, BRANCH, dry_run=False)
    assert target.read_text() == render_html(golden_results(), ROOT, BRANCH, False)
