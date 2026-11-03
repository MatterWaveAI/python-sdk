"""The workflows keep what supply-chain and release hygiene asked of them:
every action pinned to a commit, a read-only token unless a job needs
more, the checks a developer runs locally gating CI, and a release that
cannot ship a version under another's tag. Read as text: the files are
small and a YAML parser is not among this package's dependencies."""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
WORKFLOWS = REPO / ".github" / "workflows"
CI = (WORKFLOWS / "ci.yml").read_text(encoding="utf-8")
PUBLISH = (WORKFLOWS / "publish-pypi.yml").read_text(encoding="utf-8")
DOCS = (WORKFLOWS / "docs.yml").read_text(encoding="utf-8")
DRIFT = (WORKFLOWS / "route-drift.yml").read_text(encoding="utf-8")
ALL = (CI, PUBLISH, DOCS, DRIFT)
USES = re.compile(r"^\s*-?\s*uses:\s*(\S+)(.*)$", re.M)


def test_every_action_is_pinned_to_a_commit_with_its_tag_beside_it() -> None:
    for text in ALL:
        found = USES.findall(text)
        assert found
        for ref, rest in found:
            assert re.fullmatch(r"[\w.-]+/[\w.-]+@[0-9a-f]{40}", ref), ref
            assert re.match(r"\s*#\s*v\d", rest), f"{ref} has no tag comment"


def test_the_token_is_read_only_unless_a_job_says_otherwise() -> None:
    for text in ALL:
        assert re.search(r"^permissions:\n  contents: read\n(?! )", text, re.M)
    widened = re.findall(r"^    permissions:\n((?:      .+\n)+)", "".join(ALL), re.M)
    grants = {line.strip() for block in widened for line in block.splitlines()}
    assert grants <= {"id-token: write", "contents: write", "contents: read", "issues: write"}
    # Only the release job may write to the repository, and only live route-drift may write issues.
    assert re.search(
        r"^  release:\n(?:    .*\n)*?    permissions:\n      contents: write", PUBLISH, re.M
    )
    assert re.search(
        r"^  live:\n(?:    .*\n)*?    permissions:\n      contents: read\n      issues: write",
        DRIFT,
        re.M,
    )
    assert "contents: write" not in CI


def test_branch_triggers_are_aligned_across_ci_and_docs() -> None:
    expected = "on:\n  push:\n    branches: [develop, main]\n  pull_request:\n    branches: [develop, main]"
    assert expected in CI
    assert expected in DOCS
    assert "schedule:" in DRIFT
    assert "workflow_dispatch:" in DRIFT


def test_ci_runs_every_local_check_on_every_python() -> None:
    for check in (
        "uv run ruff check",
        "uv run ruff format --check",
        "uv run mypy",
        "uv run pytest",
        "sh scripts/check-ticket-ids.sh",
        "MATTERWAVE_SIBLING_SDK",
    ):
        assert check in CI, check
    assert re.search(r'python: \["3\.10", "3\.11", "3\.12", "3\.13"\]', CI)


def test_ci_scans_the_history_with_a_verified_gitleaks() -> None:
    assert "fetch-depth: 0" in CI
    assert "sha256sum -c" in CI
    assert "./gitleaks git" in CI


def test_a_release_checks_its_tag_and_ships_an_sbom_and_notes() -> None:
    assert "git fetch origin main" in PUBLISH
    assert 'git merge-base --is-ancestor "$GITHUB_SHA" origin/main' in PUBLISH
    assert "if: github.ref_type == 'tag'" in PUBLISH
    assert "scripts/release.py check-tag" in PUBLISH
    assert "--format cyclonedx1.5" in PUBLISH
    assert "scripts/release.py notes" in PUBLISH
    assert "gh release create" in PUBLISH
    for check in (
        "uv run ruff check",
        "uv run ruff format --check",
        "uv run mypy",
        "uv run pytest",
    ):
        assert check in PUBLISH, check


def test_releasing_doc_covers_the_release_process() -> None:
    releasing = (REPO / "RELEASING.md").read_text(encoding="utf-8")
    for item in (
        "develop",
        "main",
        "pyproject.toml",
        "src/matterwave_sdk/_version.py",
        "CHANGELOG.md",
        "vX.Y.Z",
        "publish-pypi.yml",
        "git merge-base --is-ancestor",
    ):
        assert item in releasing, item


def test_codeowners_names_an_owner_for_every_path() -> None:
    lines = (REPO / ".github" / "CODEOWNERS").read_text(encoding="utf-8").splitlines()
    rules = [line.split() for line in lines if line.strip() and not line.startswith("#")]
    assert rules[0][0] == "*"
    assert all(len(rule) >= 2 and rule[1].startswith("@") for rule in rules)


def test_the_pre_commit_hook_runs_a_pinned_gitleaks() -> None:
    config = (REPO / ".pre-commit-config.yaml").read_text(encoding="utf-8")
    assert "https://github.com/gitleaks/gitleaks" in config
    assert re.search(r"rev: [0-9a-f]{40}", config)
    assert "id: gitleaks" in config
