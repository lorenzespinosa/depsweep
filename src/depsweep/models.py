"""Data shapes shared across depsweep modules."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Upgrade:
    """A package depsweep will move to ``wanted`` (same major, in range)."""

    name: str
    current: str
    wanted: str


@dataclass(frozen=True)
class Major:
    """A package whose wanted version crosses a major. Listed, never installed."""

    name: str
    current: str
    wanted: str


@dataclass(frozen=True)
class Held:
    """A package depsweep refuses to touch, with the reason why."""

    name: str
    current: str
    wanted: str
    reason: str


@dataclass(frozen=True)
class Plan:
    """The full classification for one repo."""

    upgrades: tuple[Upgrade, ...] = ()
    majors: tuple[Major, ...] = ()
    held: tuple[Held, ...] = ()


@dataclass(frozen=True)
class VerifyResult:
    """Outcome of the repo's own verification bar."""

    step: str  # "test" | "build" | "install" (npm/pnpm) | "pip check" (pip)
    passed: bool


# Final per-repo states.
STATE_BRANCH_LEFT = "branch-left"
STATE_REVERTED = "reverted"
STATE_SKIPPED = "skipped"
STATE_ERROR = "error"
STATE_CLEAN = "clean"
STATE_DRY_RUN = "dry-run"
# pip repos upgrade the venv, not tracked files, so nothing lands on a branch.
STATE_APPLIED = "applied"


@dataclass
class RepoResult:
    """Everything depsweep knows about one repo after the sweep."""

    repo: str
    status: str
    reason: str | None = None
    branch: str | None = None
    manager: str = "npm"  # "npm" | "pnpm" | "pip"
    upgrades: tuple[Upgrade, ...] = ()
    majors: tuple[Major, ...] = ()
    held: tuple[Held, ...] = ()
    verify: VerifyResult | None = None

    @property
    def failed(self) -> bool:
        return self.status in (STATE_REVERTED, STATE_ERROR)
