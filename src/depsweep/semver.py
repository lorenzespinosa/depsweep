"""Just enough semver to compare npm and pip versions safely.

npm prints ``x.y.z`` with an optional hyphenated prerelease tail; pip prints
PEP 440 versions whose prerelease or post-release suffix has no hyphen
(``1.2.3rc1``, ``1.2.3.post2``). depsweep never computes range membership
here; it only decides which of two versions is newer and whether they share
a major number.
"""

from __future__ import annotations

import re

_VERSION_RE = re.compile(
    r"^v?(?P<num>\d+(?:\.\d+){0,2})"
    r"(?:-(?P<npm_pre>[0-9A-Za-z.-]+))?"
    r"(?:\.?(?P<pep_tail>(?:a|b|rc|c|dev|post)\.?\d*))?"
    r"(?:\+[0-9A-Za-z.-]+)?$"
)
_PRE_WORD_RE = re.compile(r"^[A-Za-z]+")
# PEP 440 prerelease ordering: dev < a < b < rc; anything else sorts last.
_PRE_ORDER = {"dev": 0, "a": 1, "b": 2, "rc": 3, "c": 3}


class Version:
    """A parsed version. Unparsable input raises ``ValueError``."""

    __slots__ = ("major", "minor", "patch", "prerelease", "_pep_tail")

    def __init__(self, text: str) -> None:
        match = _VERSION_RE.match(text.strip())
        if not match:
            raise ValueError(f"unparsable version: {text!r}")
        parts = match.group("num").split(".")
        self.major = int(parts[0])
        self.minor = int(parts[1]) if len(parts) > 1 else 0
        self.patch = int(parts[2]) if len(parts) > 2 else 0
        self._pep_tail = match.group("pep_tail") is not None
        self.prerelease = match.group("npm_pre") or match.group("pep_tail") or ""
        if self._pep_tail and re.match(r"c\d*$", self.prerelease):
            # PEP 440 shorthand: "1.2.3c1" means "1.2.3rc1".
            self.prerelease = "rc" + self.prerelease[1:]

    def _key(self) -> tuple:
        # Same x.y.z: prerelease < release < post-release. PEP 440 tails
        # (1.2.3rc1) get an explicit word rank so dev < a < b < rc holds;
        # npm-style hyphenated tails (1.2.3-rc.1) compare lexicographically
        # as npm does. A simplification that is enough for ordering, never
        # for range matching.
        if not self.prerelease:
            tier, sub = 1, (0, "")
        elif self.prerelease.startswith("post"):
            tier, sub = 2, (0, self.prerelease)
        elif self._pep_tail:
            word = _PRE_WORD_RE.match(self.prerelease)
            rank = _PRE_ORDER.get(word.group(0) if word else "", 4)
            tier, sub = 0, (rank, self.prerelease)
        else:
            tier, sub = 0, (0, self.prerelease)
        return (self.major, self.minor, self.patch, tier) + sub

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Version):
            return NotImplemented
        return self._key() == other._key()

    def __lt__(self, other: "Version") -> bool:
        return self._key() < other._key()

    def __le__(self, other: "Version") -> bool:
        return self._key() <= other._key()

    def __hash__(self) -> int:
        return hash(self._key())

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"Version({self.major}.{self.minor}.{self.patch}-{self.prerelease!r})"
