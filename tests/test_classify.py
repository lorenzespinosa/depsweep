"""Classification unit tests: current/wanted/pin logic against package.json ranges."""

from depsweep.classify import build_plan, classify_package, declared_ranges
from depsweep.models import Held, Major, Upgrade


def test_caret_in_range_same_major_is_upgrade():
    item = classify_package("left", "1.2.3", "1.4.0", {"left": "^1.2.3"})
    assert item == Upgrade("left", "1.2.3", "1.4.0")


def test_tilde_in_range_same_major_is_upgrade():
    item = classify_package("left", "1.2.3", "1.2.9", {"left": "~1.2.3"})
    assert item == Upgrade("left", "1.2.3", "1.2.9")


def test_prerelease_wanted_same_major_is_upgrade():
    item = classify_package("left", "1.2.3", "1.3.0-rc.1", {"left": "^1.2.3"})
    assert isinstance(item, Upgrade)


def test_major_cross_is_listed_not_installed():
    item = classify_package("big", "1.9.0", "2.5.0", {"big": "*"})
    assert item == Major("big", "1.9.0", "2.5.0")


def test_major_cross_even_for_caret_range_is_listed():
    # A caret range cannot normally admit a new major, but if npm says the
    # wanted version crosses one, we list instead of install.
    item = classify_package("big", "1.9.0", "2.0.0", {"big": "^1.9.0"})
    assert isinstance(item, Major)


def test_exact_pin_is_held():
    item = classify_package("kit", "3.1.0", "3.2.0", {"kit": "3.1.0"})
    assert item == Held("kit", "3.1.0", "3.2.0", "exact pin")


def test_exact_pin_with_equals_or_v_prefix_is_held():
    assert classify_package("a", "1.0.0", "1.1.0", {"a": "=1.0.0"}).reason == "exact pin"
    assert classify_package("b", "1.0.0", "1.1.0", {"b": "v1.0.0"}).reason == "exact pin"


def test_exact_pin_holds_even_when_wanted_crosses_major():
    item = classify_package("kit", "1.0.0", "2.0.0", {"kit": "1.0.0"})
    assert isinstance(item, Held) and item.reason == "exact pin"


def test_other_range_styles_are_held():
    item = classify_package("wide", "1.2.0", "1.9.0", {"wide": ">=1.0.0"})
    assert item == Held("wide", "1.2.0", "1.9.0", "range not auto-upgraded")
    item = classify_package("rng", "1.2.0", "1.4.0", {"rng": "1.x"})
    assert item.reason == "range not auto-upgraded"


def test_wanted_equal_or_older_is_no_action():
    assert classify_package("a", "1.4.0", "1.4.0", {"a": "^1.2.0"}) is None
    assert classify_package("b", "1.5.0", "1.4.0", {"b": "^1.2.0"}) is None


def test_not_declared_is_no_action():
    # Transitive dependency reported by npm outdated: not ours to touch.
    assert classify_package("transitive", "1.0.0", "1.1.0", {}) is None


def test_not_installed_or_missing_wanted_is_no_action():
    assert classify_package("a", "", "1.1.0", {"a": "^1.0.0"}) is None
    assert classify_package("b", "1.0.0", "", {"b": "^1.0.0"}) is None


def test_unparsable_version_is_held():
    item = classify_package("odd", "1.0.0", "latest", {"odd": "^1.0.0"})
    assert item.reason == "unparsable version"


def test_declared_ranges_merges_deps_and_dev_deps():
    package = {
        "dependencies": {"a": "^1.0.0"},
        "devDependencies": {"b": "~2.0.0"},
        "peerDependencies": {"c": "^3.0.0"},  # ignored in v1
    }
    assert declared_ranges(package) == {"a": "^1.0.0", "b": "~2.0.0"}


def test_declared_ranges_tolerates_missing_or_broken_sections():
    assert declared_ranges({}) == {}
    assert declared_ranges({"dependencies": None, "devDependencies": "broken"}) == {}


def test_build_plan_sorts_and_buckets():
    package = {
        "dependencies": {
            "zeta": "^1.0.0",
            "pin": "2.0.0",
            "big": "*",
            "alpha": "^1.0.0",
        }
    }
    outdated = {
        "zeta": {"current": "1.0.0", "wanted": "1.1.0", "latest": "1.1.0"},
        "pin": {"current": "2.0.0", "wanted": "2.3.0", "latest": "3.0.0"},
        "big": {"current": "1.9.0", "wanted": "2.5.0", "latest": "2.5.0"},
        "alpha": {"current": "1.0.0", "wanted": "1.0.1", "latest": "1.0.1"},
        "not-a-dict": "garbage",
    }
    plan = build_plan(package, outdated)
    assert [u.name for u in plan.upgrades] == ["alpha", "zeta"]
    assert [m.name for m in plan.majors] == ["big"]
    assert [(h.name, h.reason) for h in plan.held] == [("pin", "exact pin")]


def test_build_plan_empty_outdated():
    plan = build_plan({"dependencies": {"a": "^1.0.0"}}, {})
    assert plan.upgrades == () and plan.majors == () and plan.held == ()
