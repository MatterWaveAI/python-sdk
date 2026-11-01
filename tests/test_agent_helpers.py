"""The agent helpers: the mode check, the tools each mode offers, the deny
list, path confinement, `edit`'s exact replace, the unified diff and the
rendered prompt.

Most cases come from tests/agent_conformance.json, which the TypeScript
SDK's suite runs too (scripts/gen_agent_contract.py copies it there), so
the two clients answer the same way. The link cases are here because they
need the filesystem's permission to make links."""

from __future__ import annotations

import json
import os
import random
import re
import sys
from pathlib import Path

import pytest

from matterwave_sdk import agent
from matterwave_sdk.agent import diff as diff_module

CASES = json.loads(
    (Path(__file__).resolve().parent / "agent_conformance.json").read_text(encoding="utf-8")
)


# --- modes -------------------------------------------------------------------


@pytest.mark.parametrize("mode_name", sorted(CASES["decisions"]))
def test_each_decision(mode_name: str) -> None:
    for tool, decision in CASES["decisions"][mode_name].items():
        assert agent.allowed(tool, mode_name) == decision, (tool, mode_name)


@pytest.mark.parametrize("mode_name", sorted(CASES["tools_for_mode"]))
def test_the_tools_each_mode_offers(mode_name: str) -> None:
    names = [t["function"]["name"] for t in agent.tools_for_mode(mode_name)]
    assert names == CASES["tools_for_mode"][mode_name]


def test_plan_mode_offers_the_model_only_read_tools() -> None:
    offered = agent.tools_for_mode("plan")
    names = {t["function"]["name"] for t in offered}
    assert names == set(agent.READ_ONLY_TOOLS)
    assert not names & {"edit", "write_file", "run_shell"}
    assert all(agent.TOOLS_BY_NAME[n].read_only for n in names)


def test_an_unknown_mode_is_an_error_not_a_guess() -> None:
    with pytest.raises(ValueError, match="unknown approval mode"):
        agent.allowed("read_file", "trusted")
    with pytest.raises(ValueError):
        agent.tools_for_mode("YOLO")


@pytest.mark.parametrize("case", CASES["prompts"], ids=lambda c: c["mode"])
def test_the_prompt_renders_the_same_in_both_sdks(case: dict) -> None:
    rendered = agent.system_prompt(case["mode"], case["root"], platform=case["platform"])
    assert rendered == case["expected"]


def test_the_prompt_names_root_mode_and_tools() -> None:
    text = agent.system_prompt("plan", "/srv/repo", platform="linux")
    assert "Workspace root: /srv/repo" in text
    assert "Approval mode: plan." in text
    assert "Tools in this mode: read_file, list_dir, glob, grep." in text
    assert "{" not in text.replace("{draft}", "")


# --- deny list ---------------------------------------------------------------


@pytest.mark.parametrize("case", CASES["deny"]["cases"], ids=lambda c: c["path"])
def test_the_deny_list(case: dict) -> None:
    deny = agent.DenyList([*agent.DEFAULT_DENY, *CASES["deny"]["extra_patterns"]])
    assert deny.matches(case["path"], is_dir=case.get("is_dir", False)) is case["denied"]


def test_the_deny_list_reads_matterwaveignore(tmp_path: Path) -> None:
    (tmp_path / ".matterwaveignore").write_text("# mine\nprivate/\n*.sqlite\n", encoding="utf-8")
    deny = agent.DenyList.from_root(tmp_path)
    assert deny.patterns[: len(agent.DEFAULT_DENY)] == agent.DEFAULT_DENY
    assert deny.matches("private/a.md")
    assert deny.matches("db/x.sqlite")
    assert deny.matches(".env")
    assert not deny.matches("src/a.py")
    assert agent.DenyList.from_root(tmp_path / "missing").patterns == agent.DEFAULT_DENY


@pytest.mark.parametrize(
    ("glob", "path", "hit"),
    [
        ("*.py", "a.py", True),
        ("*.py", "a/b.py", False),
        ("src/**/*.ts", "src/a/b/c.ts", True),
        ("src/**/*.ts", "src/c.ts", True),
        ("**", "anything/at/all", True),
        ("file?.txt", "file1.txt", True),
        ("file?.txt", "file10.txt", False),
        ("[!a]bc", "xbc", True),
        ("[!a]bc", "abc", False),
        ("a+b(c).txt", "a+b(c).txt", True),
    ],
)
def test_glob_to_regex(glob: str, path: str, hit: bool) -> None:
    assert (re.fullmatch(agent.glob_to_regex(glob), path) is not None) is hit


# --- confinement -------------------------------------------------------------


@pytest.fixture()
def workspace(tmp_path: Path) -> Path:
    root = tmp_path / "root"
    for relative, content in CASES["confinement"]["tree"].items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    (tmp_path / "outside").mkdir()
    (tmp_path / "outside" / "secret.txt").write_text("not yours\n", encoding="utf-8")
    return root


@pytest.mark.parametrize(
    "case",
    CASES["confinement"]["cases"],
    ids=lambda c: f"{c['path'] or '<empty>'}{' (write)' if c.get('write') else ''}",
)
def test_confinement(workspace: Path, case: dict) -> None:
    write = case.get("write", False)
    if "refused" in case:
        with pytest.raises(agent.PathRefused) as refused:
            agent.confine(workspace, case["path"], write=write)
        assert refused.value.reason == case["refused"]
        if "message" in case:
            assert str(refused.value) == case["message"]
        return
    confined = agent.confine(workspace, case["path"], write=write)
    assert confined.relative == case["relative"]
    assert confined.absolute == agent.resolve_links(workspace / case["relative"])


def test_a_path_through_dotdot_is_refused_even_when_it_lands_inside(workspace: Path) -> None:
    with pytest.raises(agent.PathRefused) as refused:
        agent.confine(workspace, "src/../README.md")
    assert refused.value.reason == "parent"
    with pytest.raises(agent.PathRefused):
        agent.confine(workspace, "src\\..\\..\\outside\\secret.txt")


def test_an_absolute_path_outside_the_root_is_refused(workspace: Path) -> None:
    outside = workspace.parent / "outside" / "secret.txt"
    with pytest.raises(agent.PathRefused) as refused:
        agent.confine(workspace, str(outside))
    assert refused.value.reason == "outside"


def test_an_absolute_path_inside_the_root_is_allowed(workspace: Path) -> None:
    confined = agent.confine(workspace, str(workspace / "src" / "app.py"))
    assert confined.relative == "src/app.py"


def test_a_read_of_dotenv_is_refused_before_it_is_read(workspace: Path) -> None:
    with pytest.raises(agent.PathRefused) as refused:
        agent.confine(workspace, ".env")
    assert refused.value.reason == "denied"
    assert ".env" in str(refused.value)


def test_a_drive_or_root_relative_path_is_absolute_everywhere() -> None:
    for path in ("/etc/passwd", "\\Windows\\win.ini", "C:\\Windows", "c:relative", "D:/x"):
        assert agent.is_absolute(path), path
    for path in ("src/a.py", "./a", "a:b" if sys.platform != "win32" else "ab", ".env"):
        if path != "a:b":
            assert not agent.is_absolute(path), path


def _make_link(target: Path, link: Path, *, directory: bool) -> None:
    try:
        os.symlink(target, link, target_is_directory=directory)
    except (OSError, NotImplementedError) as exc:
        if sys.platform == "win32":
            pytest.skip(f"symbolic links need admin or Developer Mode on Windows: {exc}")
        raise


def test_a_symlinked_directory_out_of_the_root_is_refused(workspace: Path) -> None:
    _make_link(workspace.parent / "outside", workspace / "escape", directory=True)
    for path in ("escape/secret.txt", "escape/new.txt", "escape"):
        with pytest.raises(agent.PathRefused) as refused:
            agent.confine(workspace, path)
        assert refused.value.reason == "symlink", path


def test_a_symlinked_file_out_of_the_root_is_refused(workspace: Path) -> None:
    _make_link(
        workspace.parent / "outside" / "secret.txt", workspace / "notes.txt", directory=False
    )
    with pytest.raises(agent.PathRefused) as refused:
        agent.confine(workspace, "notes.txt")
    assert refused.value.reason == "symlink"


def test_a_dangling_symlink_out_of_the_root_is_refused_for_a_write(workspace: Path) -> None:
    _make_link(
        workspace.parent / "outside" / "planted.txt", workspace / "planted.txt", directory=False
    )
    with pytest.raises(agent.PathRefused) as refused:
        agent.confine(workspace, "planted.txt", write=True)
    assert refused.value.reason == "symlink"


def test_a_symlink_inside_the_root_to_a_denied_file_is_refused(workspace: Path) -> None:
    _make_link(workspace / ".env", workspace / "harmless.txt", directory=False)
    with pytest.raises(agent.PathRefused) as refused:
        agent.confine(workspace, "harmless.txt")
    assert refused.value.reason == "denied"


def test_a_symlink_inside_the_root_is_followed(workspace: Path) -> None:
    _make_link(workspace / "src", workspace / "source", directory=True)
    assert agent.confine(workspace, "source/app.py").relative == "src/app.py"


@pytest.mark.skipif(sys.platform != "win32", reason="junctions are a Windows link")
def test_a_junction_out_of_the_root_is_refused(workspace: Path) -> None:
    # A junction needs no privilege, so Windows CI covers the escape even
    # where it cannot make a symbolic link.
    import _winapi

    _winapi.CreateJunction(str(workspace.parent / "outside"), str(workspace / "escape"))
    for path in ("escape/secret.txt", "escape/new.txt"):
        with pytest.raises(agent.PathRefused) as refused:
            agent.confine(workspace, path)
        assert refused.value.reason == "symlink", path


def test_a_symlinked_root_still_confines(tmp_path: Path, workspace: Path) -> None:
    _make_link(workspace, tmp_path / "root-link", directory=True)
    linked = tmp_path / "root-link"
    assert agent.confine(linked, "src/app.py").relative == "src/app.py"
    assert agent.confine(linked, str(linked / "src" / "app.py")).relative == "src/app.py"
    with pytest.raises(agent.PathRefused):
        agent.confine(linked, ".env")


# --- edit --------------------------------------------------------------------


@pytest.mark.parametrize("case", CASES["edits"], ids=lambda c: c["name"])
def test_edit(case: dict) -> None:
    path = case.get("path", "")
    if "error" in case:
        with pytest.raises(agent.EditError) as failed:
            agent.apply_edit(case["text"], case["old"], case["new"], path=path)
        assert failed.value.reason == case["error"]
        if "count" in case:
            assert failed.value.count == case["count"]
        if "message" in case:
            assert str(failed.value) == case["message"]
        return
    assert agent.apply_edit(case["text"], case["old"], case["new"], path=path) == case["result"]


def test_a_failed_edit_leaves_the_text_alone() -> None:
    text = "x = 1\nx = 1\n"
    with pytest.raises(agent.EditError):
        agent.apply_edit(text, "x = 1", "x = 2")
    assert text == "x = 1\nx = 1\n"


# --- diff --------------------------------------------------------------------


@pytest.mark.parametrize("case", CASES["diffs"], ids=lambda c: c["name"])
def test_diff(case: dict) -> None:
    kwargs = {"context": case["context"]} if "context" in case else {}
    assert (
        agent.unified_diff(case["old"], case["new"], case.get("path", "file"), **kwargs)
        == case["expected"]
    )


_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


def _apply(old: str, patch: str) -> str:
    """Apply a unified diff of ours to `old`, to prove it says what changed."""
    lines = agent.split_lines(old)
    out: list[str] = []
    rows = patch.splitlines(keepends=True)[2:]
    pos = i = 0
    while i < len(rows):
        match = _HUNK.match(rows[i])
        assert match, rows[i]
        start, count = int(match.group(1)), int(match.group(2) or 1)
        begin = start - 1 if count else start
        out.extend(lines[pos:begin])
        pos = begin
        i += 1
        while i < len(rows) and not rows[i].startswith("@@"):
            row = rows[i]
            i += 1
            body = row[1:]
            if i < len(rows) and rows[i].startswith("\\ No newline"):
                body = body[:-1]
                i += 1
            if row[0] == " ":
                out.append(body)
                pos += 1
            elif row[0] == "-":
                pos += 1
            else:
                out.append(body)
    out.extend(lines[pos:])
    return "".join(out)


def test_random_diffs_apply_back_to_the_new_text() -> None:
    rng = random.Random(1501)  # noqa: S311 - seeded test data, not secrets
    for _ in range(400):
        old = "".join(rng.choice("abcde") + "\n" for _ in range(rng.randint(0, 25)))
        new = "".join(rng.choice("abcde") + "\n" for _ in range(rng.randint(0, 25)))
        if old and rng.random() < 0.3:
            old = old[:-1]
        if new and rng.random() < 0.3:
            new = new[:-1]
        for context in (0, 1, 3):
            patch = agent.unified_diff(old, new, "f", context=context)
            assert (patch == "") is (old == new)
            if patch:
                assert _apply(old, patch) == new


def test_a_change_past_the_edit_distance_cap_is_one_block(monkeypatch) -> None:
    monkeypatch.setattr(diff_module, "DIFF_MAX_EDIT_DISTANCE", 2)
    old = "keep\na\nb\nc\nkeep2\n"
    new = "keep\nx\nb\ny\nkeep2\n"
    patch = agent.unified_diff(old, new, "f")
    assert patch.splitlines()[3:] == [" keep", "-a", "-b", "-c", "+x", "+b", "+y", " keep2"]
    assert _apply(old, patch) == new
