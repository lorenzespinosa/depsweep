"""Pure classification: package.json ranges plus npm outdated data make a Plan.

Rules (v1, npm only):

- exact version pin in package.json  -> HOLD ("exact pin"), even across majors
- wanted crosses a major             -> MAJOR (listed, never installed)
- caret/tilde range, same major,
  wanted newer than current          -> UPGRADE via ``npm install pkg@wanted``
- any other range style              -> HOLD ("range not auto-upgraded")
- wanted missing, equal or older,
  package not installed, or the
  package not declared in
  package.json (transitive)          -> no action
"""

from __future__ import annotations

import re

from .models import Held, Major, Plan, Upgrade
from .semver import Version

# "1.2.3", "1.2", "1", "v1.2.3", "=1.2.3", with optional prerelease/build.
_EXACT_RE = re.compile(
    r"^=?v?\d+(?:\.\d+){0,2}(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$"
)
_CARET_TILDE_RE = re.compile(r"^[~^]\s*v?\d")


def declared_ranges(package_json: dict) -> dict[str, str]:
    """Merge dependencies and devDependencies declared in package.json."""
    merged: dict[str, str] = {}
    for section in ("dependencies", "devDependencies"):
        block = package_json.get(section) or {}
        if isinstance(block, dict):
            merged.update(block)
    return merged


def classify_package(
    name: str, current: str, wanted: str, declared: dict[str, str]
) -> Upgrade | Major | Held | None:
    """Classify one package from ``npm outdated`` against its declared range."""
    range_str = declared.get(name)
    if not isinstance(range_str, str) or not range_str.strip():
        return None  # not a direct dependency (transitive), not ours to touch
    current = (current or "").strip()
    wanted = (wanted or "").strip()
    if not current or not wanted:
        return None  # not installed, or the range admits nothing newer
    try:
        cur, want = Version(current), Version(wanted)
    except ValueError:
        return Held(name, current, wanted, "unparsable version")
    if _EXACT_RE.match(range_str.strip()):
        return Held(name, current, wanted, "exact pin")
    if not want > cur:
        return None  # nothing to do
    if want.major != cur.major:
        return Major(name, current, wanted)
    if _CARET_TILDE_RE.match(range_str.strip()):
        return Upgrade(name, current, wanted)
    return Held(name, current, wanted, "range not auto-upgraded")


def build_plan(package_json: dict, outdated: dict) -> Plan:
    """Turn an ``npm outdated --json`` payload into a Plan."""
    declared = declared_ranges(package_json)
    upgrades: list[Upgrade] = []
    majors: list[Major] = []
    held: list[Held] = []
    for name in sorted(outdated):
        info = outdated[name]
        if not isinstance(info, dict):
            continue
        item = classify_package(
            name,
            str(info.get("current") or ""),
            str(info.get("wanted") or ""),
            declared,
        )
        if isinstance(item, Upgrade):
            upgrades.append(item)
        elif isinstance(item, Major):
            majors.append(item)
        elif isinstance(item, Held):
            held.append(item)
    return Plan(tuple(upgrades), tuple(majors), tuple(held))
