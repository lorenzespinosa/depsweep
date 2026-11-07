"""Just enough semver to compare npm versions safely.

npm prints ``x.y.z`` with an optional prerelease tail. depsweep never computes
range membership here; it only decides which of two versions is newer and
whether they share a major number.
"""

from __future__ import annotations

import re

_VERSION_RE = re.compile(
    r"^v?(\d+)(?:\.(\d+))?(?:\.(\d+))?"
    r"(?:-([0-9A-Za-z.-]+))?(?:\+[0-9A-Za-z.-]+)?$"
)


class Version:
    """A parsed npm version. Unparsable input raises ``ValueError``."""

    __slots__ = ("major", "minor", "patch", "prerelease")

    def __init__(self, text: str) -> None:
        match = _VERSION_RE.match(text.strip())
        if not match:
            raise ValueError(f"unparsable version: {text!r}")
        self.major = int(match.group(1))
        self.minor = int(match.group(2) or 0)
        self.patch = int(match.group(3) or 0)
        self.prerelease = match.group(4) or ""

    def _key(self) -> tuple:
        # A release outranks any prerelease of the same x.y.z; prereleases
        # compare lexicographically (a simplification of npm's rules that is
        # enough for ordering, never for range matching).
        return (
            self.major,
            self.minor,
            self.patch,
            self.prerelease == "",
            self.prerelease,
        )

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
