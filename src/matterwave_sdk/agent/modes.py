"""The approval check, the tools a mode offers, and the agent-mode system
prompt rendered for one run."""

from __future__ import annotations

import sys
from typing import Any

from ._contract import (
    MODE_NAMES,
    MODES_BY_NAME,
    SHELLS,
    SYSTEM_PROMPT_TEMPLATE,
    TOOLS,
    ApprovalMode,
    Decision,
)


def mode(name: str) -> ApprovalMode:
    """The mode called `name`; a name the contract does not know is a
    ValueError, since guessing a mode would guess at what may run."""
    found = MODES_BY_NAME.get(name)
    if found is None:
        raise ValueError(f"unknown approval mode {name!r}; one of {', '.join(MODE_NAMES)}")
    return found


def allowed(tool: str, mode_name: str) -> Decision:
    """Whether `tool` may run in `mode_name` without asking (`allow`), only
    after the user approves it (`ask`), or not at all (`deny`). A tool the
    contract does not name is denied: a model that invents one gets a
    refusal, not a prompt."""
    m = mode(mode_name)
    if tool in m.allow:
        return "allow"
    if tool in m.ask:
        return "ask"
    return "deny"


def tools_for_mode(mode_name: str) -> list[dict[str, Any]]:
    """The `tools` to send to `/v1` in this mode: every tool the mode does
    not deny, in contract order. `plan` gets the read-only tools alone, so
    the model is never offered a write or a command it could not run."""
    return [t.to_openai() for t in TOOLS if allowed(t.name, mode_name) != "deny"]


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


def system_prompt(mode_name: str, root: str, *, platform: str | None = None) -> str:
    """The agent-mode system prompt for one run: the contract's template
    with the workspace root, the platform and its shell, the mode and the
    tools the mode offers filled in. Placeholders are replaced literally,
    in one pass, so a brace in `root` stays as it is."""
    m = mode(mode_name)
    shell, _ = shell_for(platform)
    values = {
        "root": root,
        "platform": platform_name(platform),
        "shell": shell,
        "mode": m.name,
        "mode_summary": m.summary,
        "tools": ", ".join(t["function"]["name"] for t in tools_for_mode(m.name)),
    }
    return _fill(SYSTEM_PROMPT_TEMPLATE, values)


def _fill(template: str, values: dict[str, str]) -> str:
    out: list[str] = []
    i = 0
    while i < len(template):
        if template[i] == "{":
            end = template.find("}", i)
            key = template[i + 1 : end] if end != -1 else ""
            if key in values:
                out.append(values[key])
                i = end + 1
                continue
        out.append(template[i])
        i += 1
    return "".join(out)
