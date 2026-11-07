"""pip support: parsing, classification, venv choice, apply/verify/restore."""

from pathlib import Path

from depsweep import gitops
from depsweep.models import (
    STATE_APPLIED,
    STATE_CLEAN,
    STATE_DRY_RUN,
    STATE_ERROR,
    STATE_REVERTED,
    STATE_SKIPPED,
    Major,
    Upgrade,
    VerifyResult,
)
from depsweep.pip import (
    Requirement,
    build_plan,
    norm,
    parse_requirement,
)
from depsweep.sweep import sweep_repo

from conftest import git

REQS = "requests>=2.0\nrich>=13.4\n"


def req(name, spec="", extras="", direct=False):
    return Requirement(name, extras, spec, direct)


# --- requirement line parsing -------------------------------------------------


def test_parse_plain_and_ranged_requirements():
    assert parse_requirement("requests>=2.0") == req("requests", ">=2.0")
    assert parse_requirement("rich ~=13.4") == req("rich", "~=13.4")
    assert parse_requirement("flask") == req("flask")
    assert parse_requirement("flask[async]>=3.0") == req("flask", ">=3.0", "[async]")
    assert parse_requirement("pkg (>=1.2, <2)") == req("pkg", ">=1.2,<2")
    assert parse_requirement("pkg>=1.0 ; python_version<'3.12'") == req("pkg", ">=1.0")


def test_parse_skips_comments_blanks_and_option_lines():
    assert parse_requirement("# a comment") is None
    assert parse_requirement("") is None
    assert parse_requirement("-r other.txt") is None
    assert parse_requirement("--index-url https://example.com/simple") is None
    assert parse_requirement("requests>=2.0  # inline comment") == req("requests", ">=2.0")


def test_parse_marks_direct_url_references():
    parsed = parse_requirement("mylib @ https://example.com/mylib.whl")
    assert parsed.direct is True
    assert parsed.spec.startswith("@")


def test_requirement_allowlist_rejects_exotic_text():
    assert req("requests", ">=2.0").safe is True
    assert req("pkg", ">=1.0,<2;rm -rf /").safe is False
    assert req("pkg@https://x/y.whl", direct=True).safe is False
    assert req("we ird", ">=1").safe is False


def test_norm_collapses_case_dots_and_separators():
    assert norm("Django_Pkg.Name") == "django-pkg-name"


# --- classification -----------------------------------------------------------


def declared(*pairs):
    return {norm(name): item for name, item in pairs}


def test_plan_holds_exact_pins_and_direct_urls():
    plan = build_plan(
        declared(("pinned", req("pinned", "==2.0.0")), ("triple", req("triple", "===1.5")),
                 ("prefix", req("prefix", "==3.1.*")), ("url", req("url", "@https://x", direct=True))),
        current={"pinned": "2.0.0", "triple": "1.5", "prefix": "3.1.0", "url": "1.0"},
        wanted={"pinned": "2.4.0", "triple": "1.9", "prefix": "3.2.0", "url": "2.0"},
    )
    assert plan.upgrades == ()
    assert [(h.name, h.reason) for h in plan.held] == [
        ("pinned", "exact pin"),
        ("prefix", "exact pin"),
        ("triple", "exact pin"),
        ("url", "direct URL"),
    ]


def test_plan_lists_majors_and_upgrades_in_range():
    plan = build_plan(
        declared(
            ("stay", req("stay", ">=2.0")),
            ("jump", req("jump", ">=1.0")),
            ("off", req("off", ">=1.0")),
        ),
        current={"stay": "2.10.0", "jump": "1.4.0", "off": "1.4.0"},
        wanted={"stay": "2.31.0", "jump": "2.0.0", "off": "1.9.9"},
    )
    assert plan.majors == (Major("jump", "1.4.0", "2.0.0"),)
    assert plan.upgrades == (
        Upgrade("off", "1.4.0", "1.9.9"),
        Upgrade("stay", "2.10.0", "2.31.0"),
    )


def test_plan_skips_up_to_date_and_unresolved_and_unparsable():
    plan = build_plan(
        declared(
            ("fresh", req("fresh", ">=1.0")),
            ("gone", req("gone", ">=1.0")),
            ("weird", req("weird", ">=1.0")),
        ),
        current={"fresh": "2.0.0", "weird": "not.a.version"},
        wanted={"fresh": "2.0.0", "weird": "2.5.0"},  # "gone" unresolved
    )
    assert plan.upgrades == ()
    assert [h.name for h in plan.held] == ["weird"]
    assert [h.reason for h in plan.held] == ["unparsable version"]


def test_plan_fresh_install_when_nothing_installed():
    plan = build_plan(
        declared(("new", req("new", ">=1.0")), ("pin", req("pin", "==1.0"))),
        current={},
        wanted={"new": "1.5.0", "pin": "1.2.0"},
    )
    # No current version: majors cannot be judged, the range is the mandate.
    assert plan.upgrades == (Upgrade("new", "", "1.5.0"),)
    assert [h.name for h in plan.held] == ["pin"]


# --- the sweep, project venv --------------------------------------------------


def test_pip_sweep_applies_upgrades_and_pip_check(pip_repo_factory, pip_env):
    repo = pip_repo_factory(
        "pyapp",
        requirements=REQS,
        installed={"requests": "2.31.0", "rich": "13.4.0"},
        resolve={"requests": "2.32.0", "rich": "13.9.4"},
    )
    result = sweep_repo(repo, "pyapp", "dep-sweep-test")

    assert result.status == STATE_APPLIED
    assert result.manager == "pip"
    assert result.branch is None  # pip sweeps never leave a branch
    assert result.upgrades == (
        Upgrade("requests", "2.31.0", "2.32.0"),
        Upgrade("rich", "13.4.0", "13.9.4"),
    )
    assert result.verify == VerifyResult("pip check", True)
    assert not result.failed

    # Upgrades ran through -r requirement files inside the project venv.
    installs = [c for c in pip_env.calls("pyapp") if "install" in c]
    assert any("requests==2.32.0" in c for c in installs)
    assert any("rich==13.9.4" in c for c in installs)
    assert "pyapp: pip check" in pip_env.calls("pyapp")
    # The repo itself never moved; the venv is the deliverable.
    assert gitops.current_branch(repo) == "main"
    assert gitops.status_porcelain(repo) == ""
    assert (repo / "requirements.txt").read_text() == REQS


def test_pip_resolver_noise_is_ignored(pip_repo_factory, pip_env):
    # Resolving "rich" makes the stub also report a transitive decoy entry;
    # depsweep must pick its own package out of the report.
    repo = pip_repo_factory(
        "noisy",
        requirements="rich>=13.4\n",
        installed={"rich": "13.4.0"},
        resolve={"rich": "13.9.4", "requests": "2.32.0"},
    )
    result = sweep_repo(repo, "noisy", "dep-sweep-test")
    assert result.upgrades == (Upgrade("rich", "13.4.0", "13.9.4"),)
    assert "transitive" not in [u.name for u in result.upgrades]


def test_pip_pins_are_held_and_repo_stays_clean(pip_repo_factory, pip_env):
    repo = pip_repo_factory(
        "pinned",
        requirements="requests==2.31.0\n",
        installed={"requests": "2.31.0"},
        resolve={"requests": "2.32.0"},
    )
    result = sweep_repo(repo, "pinned", "dep-sweep-test")
    assert result.status == STATE_CLEAN
    assert [h.reason for h in result.held] == ["exact pin"]
    assert not [c for c in pip_env.calls("pinned") if "install --upgrade" in c]


def test_pip_failing_check_reverts_and_restores_old_versions(
    pip_repo_factory, pip_env
):
    repo = pip_repo_factory(
        "sick",
        requirements="requests>=2.0\nrich>=13.4\n",
        installed={"requests": "2.31.0", "rich": "13.4.0"},
        resolve={"requests": "2.32.0", "rich": "13.9.4"},
        exits={"check": 1},
    )
    result = sweep_repo(repo, "sick", "dep-sweep-test")
    assert result.status == STATE_REVERTED
    assert result.reason == "verify failed: pip check"
    assert result.verify == VerifyResult("pip check", False)
    assert result.failed

    installs = [c for c in pip_env.calls("sick") if "install" in c and "--upgrade" in c]
    # Applied both, then put both back exactly where they were.
    assert any("requests==2.32.0" in c for c in installs)
    assert any("rich==13.9.4" in c for c in installs)
    assert any("requests==2.31.0" in c for c in installs)
    assert any("rich==13.4.0" in c for c in installs)


def test_pip_install_failure_names_the_package(pip_repo_factory, pip_env):
    repo = pip_repo_factory(
        "explode",
        requirements="requests>=2.0\n",
        installed={"requests": "2.31.0"},
        resolve={"requests": "2.32.0"},
        exits={"install": 1},
    )
    result = sweep_repo(repo, "explode", "dep-sweep-test")
    assert result.status == STATE_REVERTED
    assert "install failed: requests@2.32.0" in result.reason


def test_pip_dirty_repo_is_skipped_before_any_pip_call(pip_repo_factory, pip_env):
    repo = pip_repo_factory(
        "muddy",
        requirements="requests>=2.0\n",
        installed={"requests": "2.31.0"},
        resolve={"requests": "2.32.0"},
        dirty=True,
    )
    result = sweep_repo(repo, "muddy", "dep-sweep-test")
    assert result.status == STATE_SKIPPED
    assert result.reason == "dirty worktree"
    assert pip_env.calls("muddy") == []


def test_pip_venv_tracked_by_git_is_skipped(pip_repo_factory):
    repo = pip_repo_factory(
        "committed-venv",
        requirements="requests>=2.0\n",
        installed={"requests": "2.31.0"},
        resolve={"requests": "2.32.0"},
    )
    # A venv that git tracks would make every upgrade a commit; refuse.
    git(repo, "rm", "-q", "--cached", ".gitignore")
    (repo / ".gitignore").unlink()
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "track the venv")
    result = sweep_repo(repo, "committed-venv", "dep-sweep-test")
    assert result.status == STATE_SKIPPED
    assert "tracked by git" in result.reason


def test_pip_venv_without_pip_is_an_error(pip_repo_factory):
    repo = pip_repo_factory(
        "pipless",
        requirements="requests>=2.0\n",
        installed={"requests": "2.31.0"},
        resolve={"requests": "2.32.0"},
    )
    # The venv dir is git-ignored, so removing pip keeps the worktree clean.
    (repo / ".venv" / "bin" / "pip").unlink()
    result = sweep_repo(repo, "pipless", "dep-sweep-test")
    assert result.status == STATE_ERROR
    assert "has no pip" in result.reason


# --- the sweep, scratch venv --------------------------------------------------


def test_pip_without_venv_uses_scratch_venv(pip_repo_factory, pip_env):
    repo = pip_repo_factory(
        "barepy",
        requirements="requests>=2.0\n",
        # No installed fixture: a fresh scratch venv lists nothing at all.
        resolve={"requests": "2.32.0"},
        venv=False,
    )
    result = sweep_repo(repo, "barepy", "dep-sweep-test")
    assert result.status == STATE_APPLIED
    assert result.upgrades == (Upgrade("requests", "", "2.32.0"),)
    installs = [c for c in pip_env.calls("barepy") if "install" in c]
    assert any("requests>=2.0" in c for c in installs)  # fresh install by range
    assert not any("==2.32.0" in c for c in installs)   # never a pin install


def test_pip_dry_run_touches_nothing_not_even_the_venv(pip_repo_factory, pip_env):
    repo = pip_repo_factory(
        "preview",
        requirements="requests>=2.0\n",
        installed={"requests": "2.31.0"},
        resolve={"requests": "2.32.0"},
    )
    result = sweep_repo(repo, "preview", "dep-sweep-test", dry_run=True)
    assert result.status == STATE_DRY_RUN
    assert result.upgrades == (Upgrade("requests", "2.31.0", "2.32.0"),)
    assert result.verify is None
    # Resolver lookups are fine; real installs and checks never ran.
    calls = pip_env.calls("preview")
    assert not [
        c for c in calls if "install" in c and "--dry-run" not in c
    ]
    assert not [c for c in calls if c.endswith("pip check")]
    assert gitops.current_branch(repo) == "main"


def test_pip_scratch_dry_run_leaves_no_venv_behind(pip_repo_factory, pip_env):
    import tempfile

    def scratch_dirs():
        return set(Path(tempfile.gettempdir()).glob("depsweep-venv-*"))

    before = scratch_dirs()
    repo = pip_repo_factory(
        "evanescent",
        requirements="requests>=2.0\n",
        resolve={"requests": "2.32.0"},
        venv=False,
    )
    result = sweep_repo(repo, "evanescent", "dep-sweep-test", dry_run=True)
    assert result.status == STATE_DRY_RUN
    assert scratch_dirs() == before  # created, used, cleaned up


# --- PEP 621 pyproject projects -----------------------------------------------


def test_pep621_pyproject_project_is_swept(pip_repo_factory, pip_env):
    repo = pip_repo_factory(
        "modern",
        pyproject={
            "project": {
                "name": "modern",
                "dependencies": ["requests>=2.0", "rich==13.4.0"],
            },
            "build-system": {"requires": ["setuptools>=77"]},
        },
        installed={"requests": "2.31.0", "rich": "13.4.0"},
        resolve={"requests": "2.32.0", "rich": "13.9.4"},
    )
    result = sweep_repo(repo, "modern", "dep-sweep-test")
    assert result.status == STATE_APPLIED
    assert result.upgrades == (Upgrade("requests", "2.31.0", "2.32.0"),)
    assert [h.name for h in result.held] == ["rich"]


def test_requirements_file_wins_over_pyproject(pip_repo_factory, pip_env):
    repo = pip_repo_factory(
        "both",
        requirements="requests>=2.0\n",
        pyproject={"project": {"name": "both", "dependencies": ["rich>=13.4"]}},
        installed={"requests": "2.31.0"},
        resolve={"requests": "2.32.0", "rich": "13.9.4"},
    )
    result = sweep_repo(repo, "both", "dep-sweep-test")
    assert result.upgrades == (Upgrade("requests", "2.31.0", "2.32.0"),)
    assert "rich" not in [u.name for u in result.upgrades]


def test_broken_pyproject_is_an_error(pip_repo_factory):
    repo = pip_repo_factory("cracked", requirements=None, pyproject=None)
    (repo / "pyproject.toml").write_text("[project oops\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "break pyproject")
    result = sweep_repo(repo, "cracked", "dep-sweep-test")
    assert result.status == STATE_ERROR
