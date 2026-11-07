"""Output tests: table column order/content and the --json report shape."""

import json
import re
from pathlib import Path

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
from depsweep.report import (
    COLUMNS,
    render_json,
    render_table,
    summary_line,
    to_json_payload,
)


def make_results() -> list[RepoResult]:
    return [
        RepoResult(
            repo="alpha",
            status=STATE_BRANCH_LEFT,
            branch="dep-sweep-20261106",
            upgrades=(Upgrade("left-pad", "1.2.3", "1.4.0"),),
            majors=(Major("big", "1.9.0", "2.5.0"),),
            held=(Held("kit", "3.1.0", "3.2.0", "exact pin"),),
            verify=VerifyResult("test", True),
        ),
        RepoResult(repo="beta", status=STATE_REVERTED, reason="verify failed: test",
                   verify=VerifyResult("test", False)),
        RepoResult(repo="gamma", status=STATE_SKIPPED, reason="dirty worktree"),
        RepoResult(repo="delta", status=STATE_CLEAN),
        RepoResult(repo="eps", status=STATE_ERROR, reason="npm outdated printed invalid JSON (exit 1)"),
    ]


def test_table_column_order_is_fixed():
    table = render_table(make_results())
    header = table.splitlines()[0]
    fields = re.split(r"\s{2,}", header.strip())
    assert fields == list(COLUMNS)
    assert list(COLUMNS) == ["repo", "upgraded", "majors", "held", "verify", "state"]


def test_table_row_content():
    table = render_table(make_results())
    rows = table.splitlines()
    alpha = rows[2]
    assert "alpha" in alpha
    assert "left-pad 1.2.3->1.4.0" in alpha
    assert "big 1.9.0->2.5.0" in alpha
    assert "kit (exact pin)" in alpha
    assert "test ok" in alpha
    assert "branch left: dep-sweep-20261106" in alpha


def test_table_state_and_verify_cells():
    table = render_table(make_results())
    rows = {}
    for line in table.splitlines()[2:]:
        fields = re.split(r"\s{2,}", line.strip())
        rows[fields[0]] = fields
    assert "reverted (verify failed: test)" in rows["beta"]
    assert "test failed" in rows["beta"]
    assert "skipped (dirty worktree)" in rows["gamma"]
    assert rows["gamma"][1:5] == ["-", "-", "-", "-"]  # nothing done, no verify
    assert rows["gamma"][5] == "skipped (dirty worktree)"
    assert "clean" in rows["delta"]


def test_table_clips_wide_cells():
    wide = RepoResult(
        repo="wide",
        status=STATE_BRANCH_LEFT,
        branch="dep-sweep-x",
        upgrades=tuple(
            Upgrade(f"pkg-{i}", "1.0.0", "1.0.1") for i in range(30)
        ),
    )
    table = render_table([wide])
    for line in table.splitlines():
        assert len(line) < 400
    assert "..." in table.splitlines()[2]


def test_summary_line_counts_states():
    line = summary_line(make_results())
    assert "5 repo(s)" in line
    assert "1 branch left" in line
    assert "1 clean" in line
    assert "1 skipped" in line
    assert "2 failed" in line
    assert summary_line([]) == "0 repos found"


def test_json_payload_shape():
    payload = to_json_payload(Path("/tmp/x"), "dep-sweep-d", False, make_results())
    assert payload["root"] == "/tmp/x"
    assert payload["branch"] == "dep-sweep-d"
    assert payload["dry_run"] is False
    assert payload["totals"] == {
        "repos": 5,
        "branch_left": 1,
        "applied": 0,
        "clean": 1,
        "skipped": 1,
        "failed": 2,
    }
    alpha = payload["repos"][0]
    assert alpha["repo"] == "alpha"
    assert alpha["manager"] == "npm"
    assert alpha["upgraded"] == [{"name": "left-pad", "from": "1.2.3", "to": "1.4.0"}]
    assert alpha["majors"] == [{"name": "big", "from": "1.9.0", "to": "2.5.0"}]
    assert alpha["held"] == [
        {"name": "kit", "current": "3.1.0", "wanted": "3.2.0", "reason": "exact pin"}
    ]
    assert alpha["verify"] == {"step": "test", "passed": True}
    assert alpha["state"] == {
        "status": "branch-left",
        "branch": "dep-sweep-20261106",
        "reason": None,
    }
    # Every repo carries the same fields as the table, plus its manager.
    for repo in payload["repos"]:
        assert set(repo) == {"repo", "manager", "upgraded", "majors", "held", "verify", "state"}


def test_render_json_round_trips():
    payload = to_json_payload(Path("/tmp/x"), "b", True, make_results()[:1])
    text = render_json(payload)
    assert json.loads(text) == payload
    assert json.loads(text)["dry_run"] is True


def test_applied_pip_result_renders():
    results = [
        RepoResult(
            repo="pyapp",
            status=STATE_APPLIED,
            manager="pip",
            upgrades=(Upgrade("requests", "", "2.32.0"),),
            verify=VerifyResult("pip check", True),
        ),
        RepoResult(
            repo="pyold",
            status=STATE_APPLIED,
            manager="pip",
            upgrades=(Upgrade("rich", "13.4.0", "13.9.4"),),
        ),
    ]
    table = render_table(results)
    assert "applied" in table
    assert "requests new->2.32.0" in table  # fresh install, no current version
    assert "rich 13.4.0->13.9.4" in table
    assert "pip check ok" in table
    assert "2 applied" in summary_line(results)
    payload = to_json_payload(Path("/tmp/x"), "b", False, results)
    assert payload["totals"]["applied"] == 2
    assert payload["repos"][0]["manager"] == "pip"
    assert payload["repos"][0]["upgraded"] == [
        {"name": "requests", "from": "", "to": "2.32.0"}
    ]


def test_json_reports_reverted_reason_and_verify():
    payload = to_json_payload(Path("/tmp/x"), "b", False, make_results()[1:2])
    beta = payload["repos"][0]
    assert beta["verify"] == {"step": "test", "passed": False}
    assert beta["state"]["status"] == "reverted"
    assert beta["state"]["reason"] == "verify failed: test"
