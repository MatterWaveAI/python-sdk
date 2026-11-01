"""The approval check, the tools a mode offers, and the agent-mode system
prompt rendered for one run."""

from __future__ import annotations

import re
import sys
from collections.abc import Iterable, Mapping
from typing import Any

from ._contract import (
    APPROVAL_REFUSALS,
    KIND_DECISIONS,
    MODE_NAMES,
    MODES_BY_NAME,
    PROMPT_CONTEXT_FILES,
    PROMPT_EXTRA_TOOLS,
    READ_ONLY_DECISIONS,
    REFUSALS,
    SHELLS,
    SYSTEM_PROMPT_TEMPLATE,
    TOOL_KINDS,
    TOOLS,
    TOOLS_BY_NAME,
    ApprovalMode,
    ContextFile,
    Decision,
    ExtraTool,
    RefusalCode,
    fill,
)
from .confine import is_protected


def mode(name: str) -> ApprovalMode:
    """The mode called `name`; a name the contract does not know is a
    ValueError, since guessing a mode would guess at what may run."""
    found = MODES_BY_NAME.get(name)
    if found is None:
        raise ValueError(f"unknown approval mode {name!r}; one of {', '.join(MODE_NAMES)}")
    return found


def allowed(
    tool: str,
    mode_name: str,
    kind: str | None = None,
    *,
    read_only: bool = False,
    path: str | None = None,
) -> Decision:
    """Whether `tool` may run in `mode_name` without asking (`allow`), only
    after the user approves it (`ask`), or not at all (`deny`).

    A contract tool is decided by the mode's lists, whatever `kind` says. A
    client's own tool (web_fetch, an MCP server's tool, todo_write) is
    decided by its `kind` (`read`, `write`, `shell`, `network`, `mcp`,
    `meta`); `read_only=True` turns plan mode's deny of a `network` or
    `mcp` tool into ask. A tool with neither a contract name nor a kind is
    denied: a model that invents one gets a refusal, not a prompt.

    `path`, the file a write tool would change (relative to the root),
    turns an allow into ask when the path is protected (the deny file, the
    agent's settings, a context file), in every mode."""
    m = mode(mode_name)
    decision: Decision
    contract_tool = TOOLS_BY_NAME.get(tool)
    if contract_tool is not None:
        tool_kind: str = contract_tool.kind
        decision = "allow" if tool in m.allow else "ask" if tool in m.ask else "deny"
    elif kind is not None:
        if kind not in TOOL_KINDS:
            raise ValueError(f"unknown tool kind {kind!r}; one of {', '.join(TOOL_KINDS)}")
        tool_kind = kind
        decision = KIND_DECISIONS[m.name][kind]
        if decision == "deny" and read_only:
            decision = READ_ONLY_DECISIONS.get(m.name, {}).get(kind, "deny")
    else:
        return "deny"
    if decision == "allow" and tool_kind == "write" and path and is_protected(path):
        return "ask"
    return decision


def tools_for_mode(mode_name: str, extra: Iterable[ExtraTool] = ()) -> list[dict[str, Any]]:
    """The `tools` to send to `/v1` in this mode: every contract tool the
    mode does not deny, in contract order, then each of the client's own
    `extra` tools its kind does not deny, in the order given. `plan` gets
    the read-only tools (and the client's read and meta ones) alone, so the
    model is never offered a write or a command it could not run."""
    offered = [t.to_openai() for t in TOOLS if allowed(t.name, mode_name) != "deny"]
    for tool in extra:
        if tool.name in TOOLS_BY_NAME:
            raise ValueError(
                f"{tool.name} is a contract tool; a client's own tool needs its own name"
            )
        if allowed(tool.name, mode_name, tool.kind, read_only=tool.read_only) != "deny":
            offered.append(tool.to_openai())
    return offered


def refusal(code: RefusalCode, *, tool: str = "", mode: str = "", path: str = "") -> str:
    """The contract's sentence for a refusal, with `tool`, `mode` and
    `path` filled in: a confinement code (`outside`, `denied`, ...) or an
    approval one (`mode`, `unknown_tool`, `rejected`, `headless`,
    `needs_approval`)."""
    template = REFUSALS.get(code) or APPROVAL_REFUSALS.get(code)
    if template is None:
        raise ValueError(f"unknown refusal {code!r}")
    return fill(template, {"tool": tool, "mode": mode, "path": path})


def platform_name(platform: str | None = None) -> str:
    """`windows`, `macos` or `linux` (any other POSIX reads as linux), from
    `sys.platform` or Node's `process.platform` spelling."""
    value = sys.platform if platform is None else platform
    if value.startswith("win"):
        return "windows"
    if value == "darwin" or value == "macos":
        return "macos"
    return "linux"


def shell_for(platform: str | None = None) -> tuple[str, tuple[str, ...]]:
    """The shell `run_shell` uses on this platform: its name, as the prompt
    gives it to the model, and the argv to put before the command."""
    entry = SHELLS["windows" if platform_name(platform) == "windows" else "posix"]
    return str(entry["name"]), tuple(str(a) for a in entry["argv"])


PromptTool = ExtraTool | tuple[str, str] | Mapping[str, Any]
"""An extra tool as `system_prompt` takes it: an `ExtraTool`, a
`(name, description)` pair, or a mapping with `name` and `description`
(the `function` of an OpenAI-shaped tool, say)."""

_WHITESPACE = re.compile(r"[ \t\n\r\f\v]+")
_INSTRUCTION_TAG = re.compile(r"<(/?project-instructions)", re.IGNORECASE)


def _prompt_tool(tool: PromptTool) -> tuple[str, str]:
    if isinstance(tool, ExtraTool):
        name, description = tool.name, tool.description
    elif isinstance(tool, tuple):
        name, description = tool
    else:
        name, description = str(tool.get("name", "")), str(tool.get("description", ""))
    text = _WHITESPACE.sub(" ", description).strip(" ")
    limit = int(PROMPT_EXTRA_TOOLS["description_max_chars"])
    if len(text) > limit:
        text = text[: limit - 1] + "…"
    return name, text


def _attribute(value: str) -> str:
    for raw, ref in (
        ("&", "&amp;"),
        ('"', "&quot;"),
        ("<", "&lt;"),
        (">", "&gt;"),
        ("\r", "&#13;"),
        ("\n", "&#10;"),
    ):
        value = value.replace(raw, ref)
    return value


def _context_block(file: ContextFile) -> str:
    spec = PROMPT_CONTEXT_FILES
    limit = int(spec["max_chars"])
    body = file.content.rstrip("\r\n")
    if len(body) > limit:
        body = (
            body[:limit]
            + "\n"
            + fill(str(spec["cut"]), {"path": file.path, "max_chars": str(limit)})
        )
    body = _INSTRUCTION_TAG.sub(lambda m: "<\\" + m.group(1), body)
    lines = [fill(str(spec["open"]), {"path": _attribute(file.path)})]
    if body:
        lines.append(body)
    lines.append(str(spec["close"]))
    return "\n".join(lines)


def system_prompt(
    mode_name: str,
    root: str,
    *,
    platform: str | None = None,
    extra_tools: Iterable[PromptTool] = (),
    context_files: Iterable[ContextFile] = (),
) -> str:
    """The agent-mode system prompt for one run: the contract's template
    with the workspace root, the platform and its shell, the mode and the
    tools the mode offers filled in. Placeholders are replaced literally,
    in one pass, so a brace in `root` stays as it is.

    `extra_tools` are the client's own tools offered in this mode (filter
    them with `allowed` or `tools_for_mode` first): their names join the
    tool list and a "More tools" section gives each its one-line
    description. `context_files` are the instruction files the client
    read; the trusted ones follow in a "Project instructions" section, in
    the order given (later wins), each fenced so its text cannot end it.
    With neither, the prompt is the template alone."""
    m = mode(mode_name)
    shell, _ = shell_for(platform)
    extras = [_prompt_tool(t) for t in extra_tools]
    names = [t["function"]["name"] for t in tools_for_mode(m.name)] + [n for n, _ in extras]
    values = {
        "root": root,
        "platform": platform_name(platform),
        "shell": shell,
        "mode": m.name,
        "mode_summary": m.summary,
        "tools": ", ".join(names),
    }
    sections = [fill(SYSTEM_PROMPT_TEMPLATE, values)]
    if extras:
        line = str(PROMPT_EXTRA_TOOLS["line"])
        sections.append(
            "\n".join(
                [
                    *PROMPT_EXTRA_TOOLS["heading"],
                    *(fill(line, {"name": n, "description": d}) for n, d in extras),
                ]
            )
        )
    trusted = [f for f in context_files if f.trusted]
    if trusted:
        heading = "\n".join(PROMPT_CONTEXT_FILES["heading"])
        sections.append(heading + "\n\n" + "\n\n".join(_context_block(f) for f in trusted))
    return "\n\n".join(sections)
