"""scripts/release.py: the tag gate and the release notes it hands the
GitHub release."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("release", REPO / "scripts" / "release.py")
assert _spec is not None and _spec.loader is not None
release = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(release)

CHANGELOG = """# Changelog

## [Unreleased]

- Something still to ship.

## [0.6.0] - 2026-11-02

### Added

- One thing.

## [0.5.0] - 2026-10-31

- An older thing.
"""


def test_the_tag_must_be_v_and_the_version() -> None:
    assert release.check_tag("v0.6.0", "0.6.0") is None
    assert "expected v0.6.0" in release.check_tag("v0.6.1", "0.6.0")
    assert release.check_tag("0.6.0", "0.6.0") is not None


def test_the_version_is_read_from_the_package() -> None:
    from matterwave_sdk import __version__

    assert release.package_version() == __version__


def test_notes_are_the_versions_section_alone() -> None:
    assert release.notes(CHANGELOG, "0.6.0") == "### Added\n\n- One thing.\n"
    assert release.notes(CHANGELOG, "0.5.0") == "- An older thing.\n"


def test_a_version_with_no_section_is_refused() -> None:
    with pytest.raises(SystemExit, match="no section for 0.7.0"):
        release.notes(CHANGELOG, "0.7.0")


def test_the_real_changelog_has_a_section_for_the_current_version() -> None:
    text = (REPO / "CHANGELOG.md").read_text(encoding="utf-8")
    assert release.notes(text, release.package_version()).strip()


def test_the_cli_exits_by_the_gate(capsys) -> None:
    version = release.package_version()
    assert release.main(["check-tag", f"v{version}"]) == 0
    assert release.main(["check-tag", "v0.0.0-not-it"]) == 1
    assert "does not name" in capsys.readouterr().err
