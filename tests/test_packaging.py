"""What the published package carries: the sdist has the CHANGELOG, the
docs and the licence beside the source and tests, and the metadata names
every Python the CI matrix runs. Built with `uv build`, as the release
workflow builds it; skipped where uv is not on the PATH."""

from __future__ import annotations

import email
import shutil
import subprocess
import tarfile
import zipfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent

UV = shutil.which("uv")
pytestmark = pytest.mark.skipif(UV is None, reason="uv builds the package")


@pytest.fixture(scope="module")
def dist(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("dist")
    assert UV is not None
    # A fixed command line: uv's own path and two directories of ours.
    subprocess.run(  # noqa: S603
        [UV, "build", "--quiet", "--out-dir", str(out), str(REPO)],
        check=True,
        capture_output=True,
    )
    return out


def test_the_sdist_carries_the_changelog_docs_and_licence(dist: Path) -> None:
    (sdist,) = dist.glob("*.tar.gz")
    with tarfile.open(sdist) as archive:
        names = {name.split("/", 1)[1] for name in archive.getnames() if "/" in name}
    for wanted in (
        "CHANGELOG.md",
        "VERSIONING.md",
        "LICENSE",
        "NOTICE",
        "README.md",
        "docs/mcp.md",
        "src/matterwave_sdk/py.typed",
    ):
        assert wanted in names, f"{wanted} missing from the sdist"


def test_the_metadata_names_the_pythons_and_the_licence(dist: Path) -> None:
    (wheel,) = dist.glob("*.whl")
    with zipfile.ZipFile(wheel) as archive:
        (metadata,) = [n for n in archive.namelist() if n.endswith(".dist-info/METADATA")]
        message = email.message_from_bytes(archive.read(metadata))
    classifiers = message.get_all("Classifier") or []
    for minor in (10, 11, 12, 13):
        assert f"Programming Language :: Python :: 3.{minor}" in classifiers
    assert message["License-Expression"] == "Apache-2.0"
    assert sorted(message.get_all("License-File") or []) == ["LICENSE", "NOTICE"]
    assert message["Requires-Python"] == ">=3.10"
