"""Unit tests for the small semver comparison core."""

import pytest

from depsweep.semver import Version


def test_plain_ordering():
    assert Version("1.2.3") < Version("1.3.0")
    assert Version("1.3.0") < Version("2.0.0")
    assert Version("1.9.9") < Version("2.0.0")
    assert Version("0.1.0") < Version("0.2.0")


def test_equality_and_hash():
    assert Version("1.2.3") == Version("1.2.3")
    assert hash(Version("1.2.3")) == hash(Version("1.2.3"))
    assert Version("1.2.3") != Version("1.2.4")


def test_partial_versions_pad_with_zeros():
    assert Version("1.2") == Version("1.2.0")
    assert Version("1") == Version("1.0.0")
    assert Version("1.2") < Version("1.2.1")


def test_prerelease_ranks_below_release():
    assert Version("1.2.3-rc.1") < Version("1.2.3")
    assert Version("1.2.3-beta") < Version("1.2.3-rc") < Version("1.2.3")


def test_v_prefix_and_build_metadata_ignored():
    assert Version("v1.2.3") == Version("1.2.3")
    assert Version("1.2.3+build.5") == Version("1.2.3")
    assert Version("v1.2.3-rc.1+build.2") == Version("1.2.3-rc.1")


def test_major_extraction():
    assert Version("0.1.2").major == 0
    assert Version("2.0.0").major == 2


@pytest.mark.parametrize("bad", ["latest", "^1.2.3", "", "1.2.x", "next-thing", "*"])
def test_unparsable_raises(bad):
    with pytest.raises(ValueError):
        Version(bad)


# --- PEP 440 shapes pip prints -------------------------------------------------


def test_pep440_prerelease_ordering():
    assert Version("2.32.0.dev1") < Version("2.32.0a1") < Version("2.32.0b2")
    assert Version("2.32.0b2") < Version("2.32.0rc1") < Version("2.32.0")
    assert Version("1.2.3c1") == Version("1.2.3rc1")  # c is rc shorthand
    assert Version("2.32.0rc1") < Version("2.32.1.dev3")


def test_pep440_post_release_ranks_above_release():
    assert Version("2.32.0") < Version("2.32.0.post1")
    assert Version("2.32.0.post1") < Version("2.32.1")
    assert Version("2.32.0.post1") < Version("2.32.0.post2")


def test_pep440_prerelease_below_release_and_npm_style_untouched():
    assert Version("2.32.0rc1") < Version("2.32.0")
    assert Version("1.2.3-rc.1") < Version("1.2.3")
    assert Version("2.0") == Version("2.0.0")  # pip's two-part style still pads
    assert Version("2.0.7").major == 2
