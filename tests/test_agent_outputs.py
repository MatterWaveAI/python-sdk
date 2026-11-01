"""Contract 1.1: the read tools' output, the search rules, run_shell's
text, tool kinds, protected files, the refusal sentences and the prompt's
extra-tool and context-file slots.

The output, ignore and shell cases are the contract's own
(`CONTRACT["conformance"]`), which every client must reproduce; the kind,
protected, refusal and prompt-slot cases are in tests/agent_conformance.json.
The TypeScript SDK's suite runs the same cases."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, get_args

import pytest

from matterwave_sdk import agent

CONFORMANCE: Any = json.loads(
    (Path(agent.__file__).parent / "contract.json").read_text(encoding="utf-8")
)["conformance"]
CASES = json.loads(
    (Path(__file__).resolve().parent / "agent_conformance.json").read_text(encoding="utf-8")
)


def _write_tree(root: Path, tree: Any) -> None:
    for relative, content in tree.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content.encode("utf-8"))


def _make_specials(root: Path) -> set[str]:
    """The conformance `fifos` and `links`, where this platform can make
    them: the names of the kinds it made."""
    made: set[str] = set()
    mkfifo = getattr(os, "mkfifo", None)
    if mkfifo is not None:
        for relative in CONFORMANCE["fifos"]:
            mkfifo(root / relative)
        made.add("fifos")
    try:
        for relative, target in CONFORMANCE["links"].items():
            os.symlink(target, root / relative)
        made.add("links")
    except OSError:
        pass  # Windows without Developer Mode cannot make links
    return made


MADE: set[str] = set()


@pytest.fixture(scope="module")
def tree(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("conformance")
    _write_tree(root, CONFORMANCE["tree"])
    MADE.update(_make_specials(root))
    return root


def _needs(case: Any) -> None:
    missing = set(case.get("needs", ())) - MADE
    if missing:
        pytest.skip(f"this platform cannot make {', '.join(sorted(missing))}")


def _run(root: Path, case: Any) -> str:
    args = case["args"]
    limits = dict(case["limits"]) if "limits" in case else None
    tool = case["tool"]
    if tool == "read_file":
        return agent.run_read_file(
            root, args["path"], args.get("start_line"), args.get("end_line"), limits=limits
        )
    if tool == "list_dir":
        return agent.run_list_dir(root, args.get("path", "."), limits=limits)
    if tool == "glob":
        return agent.run_glob(root, args["pattern"], args.get("path", "."), limits=limits)
    assert tool == "grep"
    return agent.run_grep(
        root,
        args["pattern"],
        args.get("path", "."),
        glob=args.get("glob"),
        ignore_case=args.get("ignore_case", False),
        limits=limits,
    )


def _case_id(case: Any) -> str:
    return f"{case['tool']} {json.dumps(case['args'])}{' limits' if 'limits' in case else ''}"


# --- the read tools' output ---------------------------------------------------


@pytest.mark.parametrize("case", CONFORMANCE["tools"], ids=_case_id)
def test_tool_output(tree: Path, case: Any) -> None:
    _needs(case)
    if "expected" in case:
        assert _run(tree, case) == case["expected"]
        return
    error: type[Exception] = agent.PathRefused if "refused" in case else agent.ToolError
    with pytest.raises(error) as raised:
        _run(tree, case)
    reason = case.get("refused", case.get("error"))
    assert getattr(raised.value, "reason", None) == reason
    if "message" in case:
        assert str(raised.value) == case["message"]
    else:
        assert str(raised.value).startswith(case["message_prefix"])


def test_every_tool_error_has_a_case() -> None:
    covered = {c["error"] for c in CONFORMANCE["tools"] if "error" in c}
    # run_shell's bad_timeout is no read tool's, and grep's timeout depends
    # on the machine; both have tests of their own.
    assert covered == set(agent.TOOL_ERRORS) - {"bad_timeout", "timeout"}
    assert set(agent.TOOL_ERRORS) == set(get_args(agent.ToolFailure))


def test_a_walk_skips_what_search_ignores(tree: Path) -> None:
    walked = list(agent.walk(tree))
    assert "src/main.py" in walked and "keep.log" in walked
    assert not any(p.startswith(("build/", ".git/", "private/", "src/generated/")) for p in walked)
    assert ".env" not in walked and "app.log" not in walked
    assert list(agent.walk(tree, "build")) == ["build/a.txt", "build/keep.txt"]
    assert list(agent.walk(tree, "src/main.py")) == ["src/main.py"]
    with pytest.raises(agent.ToolError) as missing:
        list(agent.walk(tree, "nowhere"))
    assert missing.value.reason == "not_found"


def test_a_long_read_is_cut_at_the_line_limit(tmp_path: Path) -> None:
    (tmp_path / "big.txt").write_bytes(b"".join(b"line %d\n" % n for n in range(1, 2501)))
    text = agent.run_read_file(tmp_path, "big.txt")
    lines = text.split("\n")
    assert len(lines) == agent.LIMITS["read_max_lines"] + 1
    assert lines[-1] == (
        "[Cut off at the read limit: showing lines 1-2000 of 2500. Pass start_line=2001 to read on.]"
    )
    assert agent.run_read_file(tmp_path, "big.txt", 2001).endswith("2500\tline 2500")


def test_a_byte_cut_keeps_whole_characters() -> None:
    assert agent.utf8_head("aé€😀", 4) == "aé"
    assert agent.utf8_tail("aé€😀", 5) == "😀"
    assert agent.utf8_head("abc", 9) == "abc"
    assert agent.format_read_file("é" * 10, "x", limits={"read_max_bytes": 6}).startswith("1\téé\n")


# --- what confine makes of a path -------------------------------------------


@pytest.mark.parametrize(
    "case",
    CONFORMANCE["paths"],
    ids=lambda c: f"{c['path']!r}{' (write)' if c.get('write') else ''}",
)
def test_confine_paths(tree: Path, case: Any) -> None:
    _needs(case)
    write = case.get("write", False)
    if "relative" in case:
        assert agent.confine(tree, case["path"], write=write).relative == case["relative"]
        return
    with pytest.raises(agent.PathRefused) as refused:
        agent.confine(tree, case["path"], write=write)
    assert refused.value.reason == case["refused"]
    assert str(refused.value) == case["message"]


def test_windows_aliases_are_named_without_the_filesystem() -> None:
    for case in CONFORMANCE["paths"]:
        if case.get("refused") in {"stream", "trailing", "device", "unc"}:
            assert agent.windows_alias(case["path"]) == case["refused"], case
        elif "relative" in case:
            assert agent.windows_alias(case["path"]) is None, case
    assert {"stream", "trailing", "device", "unc"} <= set(agent.REFUSALS)


def test_a_path_the_os_cannot_follow_is_still_a_refusal(tmp_path: Path) -> None:
    for path in ("nul", "NUL.txt", "con", "COM1", "x:y", r"\\.\nul", r"src\aux.txt"):
        with pytest.raises(agent.PathRefused):
            agent.confine(tmp_path, path)
    with pytest.raises(agent.PathRefused) as refused:
        agent.confine(tmp_path, r"Z:\elsewhere\file.txt")
    assert refused.value.reason == "outside"


# --- the search rules --------------------------------------------------------


def _ignore_rules() -> agent.SearchIgnore:
    files = CONFORMANCE["tree"]
    return agent.SearchIgnore(
        files.get, deny=agent.DenyList.with_ignore_file(files[agent.DENY_FILE])
    )


@pytest.mark.parametrize(
    "case",
    CONFORMANCE["ignore"],
    ids=lambda c: (
        f"{c['path']}{'/' if c.get('is_dir') else ''}{' alone' if c.get('ancestors') is False else ''}"
    ),
)
def test_search_ignore(case: Any) -> None:
    rules = _ignore_rules()
    got = rules.ignored(
        case["path"], is_dir=case.get("is_dir", False), ancestors=case.get("ancestors", True)
    )
    assert got is case["ignored"]


def test_search_ignore_reads_the_files_from_disk(tree: Path) -> None:
    rules = agent.load_search_ignore(tree)
    for case in CONFORMANCE["ignore"]:
        assert (
            rules.ignored(
                case["path"],
                is_dir=case.get("is_dir", False),
                ancestors=case.get("ancestors", True),
            )
            is case["ignored"]
        ), case


def test_an_ignore_file_on_its_own() -> None:
    rules = agent.IgnoreFile("*.log\n!keep.log\n/out/\n", base="pkg")
    assert rules.match("pkg/a.log") is True
    assert rules.match("pkg/keep.log") is False
    assert rules.match("pkg/out", is_dir=True) is True
    assert rules.match("pkg/sub/out", is_dir=True) is None
    assert rules.match("other/a.log") is None
    assert rules.match("pkg/a.txt") is None


@pytest.mark.parametrize(
    ("pattern", "path", "hit"),
    [
        ("*.py", "a.py", True),
        ("*.py", "deep/er/a.PY", True),
        ("src/*.py", "src/a.py", True),
        ("src/*.py", "src/x/a.py", False),
        ("/a.py", "x/a.py", False),
        ("./src/**", "src/x/y.ts", True),
        ("**/test_*.py", "test_a.py", True),
    ],
)
def test_glob_matches(pattern: str, path: str, hit: bool) -> None:
    assert agent.glob_matches(pattern, path) is hit


# --- run_shell's text --------------------------------------------------------


@pytest.mark.parametrize("case", CONFORMANCE["shell"], ids=lambda c: json.dumps(c)[:60])
def test_shell_output(case: Any) -> None:
    rendered = agent.format_shell(
        case["output"],
        exit_code=case.get("exit_code"),
        timed_out=case.get("timed_out", False),
        stopped=case.get("stopped", False),
        timeout_s=case.get("timeout_s"),
        limits=dict(case["limits"]) if "limits" in case else None,
        omitted=case.get("omitted", 0),
    )
    assert rendered == case["expected"]


# --- kinds, protected files and refusals --------------------------------------


@pytest.mark.parametrize("case", CASES["kinds"], ids=lambda c: c["tool"])
def test_kind_decisions(case: Any) -> None:
    for mode_name, decision in case["decisions"].items():
        got = agent.allowed(
            case["tool"], mode_name, case.get("kind"), read_only=case.get("read_only", False)
        )
        assert got == decision, (mode_name, case)


def test_the_contract_tools_follow_their_kinds() -> None:
    for m in agent.APPROVAL_MODES:
        for tool in agent.TOOLS:
            assert agent.allowed(tool.name, m.name) == agent.KIND_DECISIONS[m.name][tool.kind]


def test_each_mode_allows_at_least_what_plan_does() -> None:
    rank = {"deny": 0, "ask": 1, "allow": 2}
    order = agent.MODE_NAMES
    for kind in agent.TOOL_KINDS:
        for read_only in (False, True):
            decisions = [agent.allowed("own_tool", m, kind, read_only=read_only) for m in order]
            ranks = [rank[d] for d in decisions]
            assert ranks == sorted(ranks), (kind, read_only, decisions)


def test_kinds_are_known() -> None:
    assert get_args(agent.AnyToolKind) == agent.TOOL_KINDS
    assert set(get_args(agent.ToolKind)) | set(get_args(agent.ExtraToolKind)) == set(
        agent.TOOL_KINDS
    )
    for decisions in agent.KIND_DECISIONS.values():
        assert set(decisions) == set(agent.TOOL_KINDS)
    with pytest.raises(ValueError, match="unknown tool kind"):
        agent.allowed("own_tool", "ask", "telepathy")


def test_extra_tools_join_the_mode_tools() -> None:
    fetch = agent.ExtraTool("web_fetch", "network", "Fetch a URL.")
    todo = agent.ExtraTool("todo_write", "meta", "Keep a to-do list.")
    search = agent.ExtraTool("mcp__gh__search", "mcp", "Search code.", read_only=True)
    plan = [t["function"]["name"] for t in agent.tools_for_mode("plan", [fetch, todo, search])]
    assert plan == [*agent.READ_ONLY_TOOLS, "todo_write", "mcp__gh__search"]
    yolo = agent.tools_for_mode("yolo", [fetch])
    assert yolo[-1] == {
        "type": "function",
        "function": {
            "name": "web_fetch",
            "description": "Fetch a URL.",
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        },
    }
    json.dumps(yolo)
    with pytest.raises(ValueError, match="contract tool"):
        agent.tools_for_mode("ask", [agent.ExtraTool("edit", "read", "A fake edit.")])


@pytest.mark.parametrize("case", CASES["protected"], ids=lambda c: c["path"])
def test_protected_files(case: Any) -> None:
    assert agent.is_protected(case["path"]) is case["protected"]
    for key, decision in case["decisions"].items():
        tool, mode_name = key.split(" ")
        assert agent.allowed(tool, mode_name, path=case["path"]) == decision, key


def test_the_protected_list() -> None:
    assert agent.PROTECTED == (
        ".matterwaveignore",
        ".matterwave/",
        "MATTERWAVE.md",
        "MATTERWAVE.local.md",
        "AGENTS.md",
        ".bashrc",
        ".bash_profile",
        ".profile",
        ".zshrc",
        ".zprofile",
        ".ssh/",
        ".aws/",
        "**/.config/matterwave",
    )
    # The personal file comes last, so it wins where it and a shared one disagree.
    assert agent.CONTEXT_FILES == ("MATTERWAVE.md", "AGENTS.md", "MATTERWAVE.local.md")
    assert agent.SETTINGS_DIR == ".matterwave/"
    assert agent.PROTECTED_LIST.patterns == agent.PROTECTED
    assert agent.EDIT_CRLF_FALLBACK is True


def test_a_protected_write_needs_approval(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text("x", encoding="utf-8")
    (tmp_path / ".matterwave").mkdir()
    for path in ("AGENTS.md", ".matterwave/settings.json", ".matterwaveignore"):
        with pytest.raises(agent.PathRefused) as refused:
            agent.confine(tmp_path, path, write=True)
        assert refused.value.reason == "protected"
        assert agent.confine(tmp_path, path, write=True, approved=True).relative == path
        assert agent.confine(tmp_path, path).relative == path
    with pytest.raises(agent.PathRefused):
        agent.confine(tmp_path, ".env", write=True, approved=True)


@pytest.mark.parametrize("case", CASES["refusals"], ids=lambda c: c["code"])
def test_refusal_wording(case: Any) -> None:
    assert agent.refusal(case["code"], **case["values"]) == case["expected"]


def test_every_refusal_is_worded() -> None:
    codes = set(agent.REFUSALS) | set(agent.APPROVAL_REFUSALS)
    assert codes == set(get_args(agent.RefusalCode))
    assert set(agent.APPROVAL_REFUSALS) == set(get_args(agent.ApprovalRefusal))
    assert {c["code"] for c in CASES["refusals"]} >= set(agent.APPROVAL_REFUSALS)
    with pytest.raises(ValueError):
        agent.refusal("nope")  # type: ignore[arg-type]


# --- the prompt's slots ------------------------------------------------------


@pytest.mark.parametrize("case", CASES["prompt_slots"], ids=lambda c: c["name"])
def test_prompt_slots(case: Any) -> None:
    rendered = agent.system_prompt(
        case["mode"],
        case["root"],
        platform=case["platform"],
        extra_tools=case["extra_tools"],
        context_files=[agent.ContextFile(**f) for f in case["context_files"]],
    )
    assert rendered == case["expected"]


def test_the_slots_take_each_form_of_tool() -> None:
    as_tool = agent.ExtraTool("web_fetch", "network", "Fetch a URL.")
    forms: list[agent.PromptTool] = [
        as_tool,
        ("web_fetch", "Fetch a URL."),
        {"name": "web_fetch", "description": "Fetch a URL."},
    ]
    rendered = {agent.system_prompt("ask", "/r", platform="linux", extra_tools=[f]) for f in forms}
    assert len(rendered) == 1
    text = rendered.pop()
    assert (
        "Tools in this mode: read_file, list_dir, glob, grep, edit, write_file, run_shell, web_fetch."
        in text
    )
    assert text.endswith("- web_fetch: Fetch a URL.")


def test_an_empty_slot_leaves_the_prompt_as_it_was() -> None:
    for case in CASES["prompts"]:
        rendered = agent.system_prompt(
            case["mode"], case["root"], platform=case["platform"], extra_tools=[], context_files=[]
        )
        assert rendered == case["expected"]


def test_a_long_context_file_is_cut() -> None:
    limit = int(agent.CONTRACT["system_prompt"]["context_files"]["max_chars"])
    content = "ab" * limit
    rendered = agent.system_prompt(
        "ask",
        "/r",
        platform="linux",
        context_files=[agent.ContextFile("AGENTS.md", content, trusted=True)],
    )
    block = rendered.split('<project-instructions path="AGENTS.md">\n', 1)[1]
    body, _, rest = block.partition("\n[AGENTS.md is cut off here")
    assert body == content[:limit]
    assert rest == f": it is longer than {limit} characters.]\n</project-instructions>"


# --- links -------------------------------------------------------------------


@pytest.mark.skipif(os.name == "nt" and not hasattr(os, "symlink"), reason="no symlinks")
def test_a_walk_does_not_follow_a_link_out(tmp_path: Path) -> None:
    root = tmp_path / "root"
    (root / "src").mkdir(parents=True)
    (root / "src" / "a.py").write_text("x\n", encoding="utf-8")
    (tmp_path / "outside").mkdir()
    (tmp_path / "outside" / "b.py").write_text("y\n", encoding="utf-8")
    try:
        os.symlink(tmp_path / "outside", root / "escape", target_is_directory=True)
        os.symlink(tmp_path / "outside" / "b.py", root / "b.py")
        os.symlink(root / "src", root / "again", target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symbolic links need admin or Developer Mode on Windows: {exc}")
    assert list(agent.walk(root)) == ["src/a.py"]
    assert agent.run_list_dir(root) == "again/\nsrc/"


# --- 1.1.1 fixes -------------------------------------------------------------


@pytest.mark.parametrize(
    "case",
    CONFORMANCE["alias_roots"],
    ids=lambda c: f"{c['path']!r} in {c['root']!r}",
)
def test_only_the_names_below_the_root_are_aliases(case: Any) -> None:
    assert agent.windows_alias(case["path"], case["root"]) == case["refused"]


@pytest.mark.skipif(os.name == "nt", reason="Windows cannot name a folder con, a:b or x.")
@pytest.mark.parametrize("parent", ["con", "a:b", "x.", "NUL.d"])
def test_a_root_inside_an_alias_named_folder_takes_absolute_paths(
    tmp_path: Path, parent: str
) -> None:
    root = tmp_path / parent / "repo"
    (root / "src").mkdir(parents=True)
    (root / "src" / "a.py").write_text("x = 1\n", encoding="utf-8")
    absolute = str(root / "src" / "a.py")
    assert agent.confine(root, absolute).relative == "src/a.py"
    assert agent.run_read_file(root, absolute) == "1\tx = 1"
    assert agent.run_grep(root, "x", str(root)) == "src/a.py:1: x = 1"
    with pytest.raises(agent.PathRefused) as below:
        agent.confine(root, str(root / "nul.txt"))
    assert below.value.reason == "device"


def test_a_byte_order_mark_does_not_hide_the_first_rule() -> None:
    assert agent.IgnoreFile("\ufeff*.log\n").match("a.log") is True
    assert agent.DenyList.with_ignore_file("\ufeffsecrets/\n").matches("secrets/x")
    rules = agent.SearchIgnore({".gitignore": "\ufeffbuild/\n"}.get)
    assert rules.ignored("build/a.txt")


def test_an_invalid_ignore_line_is_skipped() -> None:
    rules = agent.IgnoreFile("[z-a]\n*.log\n!keep.log\n")
    assert rules.match("a.log") is True and rules.match("keep.log") is False
    assert rules.match("z") is None
    deny = agent.DenyList.with_ignore_file("[9-0]*\nprivate/\n")
    assert deny.matches("private/x") and not deny.matches("9")


def test_an_invalid_ignore_file_does_not_break_search_or_confine(tmp_path: Path) -> None:
    (tmp_path / ".gitignore").write_text("[z-a]\n*.log\n", encoding="utf-8")
    (tmp_path / ".matterwaveignore").write_text("[z-a]\nsecret/\n", encoding="utf-8")
    (tmp_path / "a.txt").write_text("hit\n", encoding="utf-8")
    (tmp_path / "b.log").write_text("hit\n", encoding="utf-8")
    assert agent.run_grep(tmp_path, "hit") == "a.txt:1: hit"
    assert agent.confine(tmp_path, "a.txt").relative == "a.txt"
    with pytest.raises(agent.PathRefused):
        agent.confine(tmp_path, "secret/x")


def test_glob_classes_read_alike_in_every_dialect() -> None:
    assert agent.glob_matches("[]x]", "]")
    assert agent.glob_matches("[!]x]", "a") and not agent.glob_matches("[!]x]", "]")
    assert agent.glob_matches("[^a]", "b") and not agent.glob_matches("[^a]", "A")
    assert agent.glob_matches("[a-]", "-")
    assert agent.glob_matches("[\\]", "\\")
    assert agent.glob_matches("[!]", "[!]")
    assert agent.glob_matches("a[b", "a[b")
    with pytest.raises(agent.GlobError):
        agent.glob_to_regex("[z-a]")
    with pytest.raises(agent.ToolError) as invalid:
        agent.glob_matches("[z-a]", "x")
    assert invalid.value.reason == "invalid_glob"


def test_an_invalid_glob_is_a_tool_error_before_any_search(tmp_path: Path) -> None:
    for call in (
        lambda: agent.run_glob(tmp_path, "[z-a]", "nowhere"),
        lambda: agent.run_grep(tmp_path, "x", "nowhere", glob="[z-a]"),
    ):
        with pytest.raises(agent.ToolError) as invalid:
            call()
        assert invalid.value.reason == "invalid_glob"


def test_a_link_loop_is_a_refusal(tmp_path: Path) -> None:
    try:
        os.symlink("loop", tmp_path / "loop")
        os.symlink("pong", tmp_path / "ping")
        os.symlink("ping", tmp_path / "pong")
    except OSError as exc:
        pytest.skip(f"symbolic links need admin or Developer Mode on Windows: {exc}")
    (tmp_path / "a.txt").write_text("x\n", encoding="utf-8")
    for path in ("loop", "ping", "pong/x", str(tmp_path / "ping")):
        with pytest.raises(agent.PathRefused) as refused:
            agent.confine(tmp_path, path, write=True)
        assert refused.value.reason == "loop"
    assert list(agent.walk(tmp_path)) == ["a.txt"]
    assert agent.run_list_dir(tmp_path) == "a.txt"


def test_a_file_that_grows_past_the_cap_is_still_too_large(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from matterwave_sdk.agent import search

    (tmp_path / "a.txt").write_bytes(b"x" * 100)
    real_fstat = os.fstat

    def stale_fstat(fd: int) -> os.stat_result:
        # The size from before the file grew: under the cap.
        info = real_fstat(fd)
        return os.stat_result((*info[:6], 5, *info[7:10]))

    monkeypatch.setattr(search.os, "fstat", stale_fstat)
    with pytest.raises(agent.ToolError) as large:
        agent.run_read_file(tmp_path, "a.txt", limits={"read_max_file_bytes": 10})
    assert large.value.reason == "too_large"
    assert str(large.value) == "a.txt is 11 bytes, over the 10 bytes read_file reads."


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="no named pipes")
def test_a_named_pipe_is_never_waited_on(tmp_path: Path) -> None:
    os.mkfifo(tmp_path / "pipe")
    (tmp_path / "a.txt").write_text("x\n", encoding="utf-8")
    for call in (
        lambda: agent.run_read_file(tmp_path, "pipe"),
        lambda: agent.run_grep(tmp_path, "x", "pipe"),
        lambda: agent.run_list_dir(tmp_path, "pipe"),
        lambda: list(agent.walk(tmp_path, "pipe")),
    ):
        with pytest.raises(agent.ToolError) as special:
            call()
        assert special.value.reason == "special_file"
    assert agent.run_grep(tmp_path, "x") == "a.txt:1: x"
    assert agent.run_list_dir(tmp_path) == "a.txt"


def test_grep_stops_at_its_deadline(tmp_path: Path) -> None:
    import time

    # Each line takes a while to fail on its own, and there are many.
    (tmp_path / "slow.txt").write_text(("a" * 18 + "b\n") * 400, encoding="utf-8")
    started = time.monotonic()
    with pytest.raises(agent.ToolError) as late:
        agent.run_grep(tmp_path, "(a+)+$", timeout_s=0.2)
    assert time.monotonic() - started < 5
    assert late.value.reason == "timeout"
    assert str(late.value) == (
        "The search for (a+)+$ stopped after 0.2 s without finishing. "
        "Try a simpler pattern, a narrower path or a glob."
    )
    assert agent.run_grep(tmp_path, "b$", "slow.txt", timeout_s=30).startswith("slow.txt:1: aaaa")
    with pytest.raises(ValueError):
        agent.run_grep(tmp_path, "x", timeout_s=0)


def test_format_shell_takes_a_head_and_tail_kept_by_the_client() -> None:
    limits = {"shell_output_max_bytes": 64}
    whole = "".join(f"line {n} é😀\n" for n in range(200))
    data = whole.encode("utf-8")
    for keep in (32, 33, 40, 64):
        head = data[:keep].decode("utf-8", "ignore")
        tail = data[len(data) - keep :].decode("utf-8", "ignore")
        dropped = len(data) - len(head.encode()) - len(tail.encode())
        assert agent.format_shell(
            head + tail, exit_code=0, limits=limits, omitted=dropped
        ) == agent.format_shell(whole, exit_code=0, limits=limits), keep
    assert agent.format_shell("", exit_code=0, omitted=4) == (
        "Exit code 0\n\n[... 4 bytes of output cut from the middle ...]\n"
    )
    with pytest.raises(ValueError):
        agent.format_shell("x", exit_code=0, omitted=-1)


def test_the_helpers_clients_reimplemented_are_exported() -> None:
    assert agent.fill("{a} {b}", {"a": "1"}) == "1 {b}"
    assert agent.limit("read_max_lines") == agent.LIMITS["read_max_lines"]
    assert agent.limit("read_max_lines", {"read_max_lines": 3}) == 3
    agent.check_lines(1, 1)
    for start, end, reason in ((0, None, "bad_line"), (3, 2, "end_before_start")):
        with pytest.raises(agent.ToolError) as bad:
            agent.check_lines(start, end)
        assert bad.value.reason == reason
