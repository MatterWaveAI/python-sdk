"""The contract, read from `contract.json` beside this module and held in
typed constants. The JSON is the one source: the TypeScript SDK's copy is
generated from it, and nothing here restates a value it carries."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from importlib import resources
from types import MappingProxyType
from typing import Any, Literal, cast

Decision = Literal["allow", "ask", "deny"]
ToolKind = Literal["read", "write", "shell"]
"""The kinds of the contract's own seven tools."""
ExtraToolKind = Literal["network", "mcp", "meta"]
"""The kinds a client's own tools may have beyond the contract's: web_fetch
(`network`), an MCP server's tool (`mcp`), todo_write, leaving plan mode or
dispatching a subagent (`meta`)."""
AnyToolKind = Literal["read", "write", "shell", "network", "mcp", "meta"]
ToolName = Literal["read_file", "list_dir", "glob", "grep", "edit", "write_file", "run_shell"]
ModeName = Literal["plan", "ask", "auto-edit", "yolo"]
Refusal = Literal[
    "empty",
    "parent",
    "outside",
    "symlink",
    "loop",
    "denied",
    "protected",
    "stream",
    "trailing",
    "device",
    "unc",
]
EditFailure = Literal["missing", "empty", "unchanged", "not_found", "not_unique"]
ApprovalRefusal = Literal["mode", "unknown_tool", "rejected", "headless", "needs_approval"]
RefusalCode = Literal[
    "empty",
    "parent",
    "outside",
    "symlink",
    "loop",
    "denied",
    "protected",
    "stream",
    "trailing",
    "device",
    "unc",
    "mode",
    "unknown_tool",
    "rejected",
    "headless",
    "needs_approval",
]
"""Every refusal `refusal()` words: the confinement codes and the approval ones."""
ToolFailure = Literal[
    "not_found",
    "is_directory",
    "not_directory",
    "binary",
    "too_large",
    "bad_line",
    "end_before_start",
    "past_end",
    "empty_pattern",
    "invalid_regex",
    "bad_timeout",
    "invalid_glob",
    "special_file",
    "timeout",
]


def _load() -> dict[str, Any]:
    text = resources.files(__package__).joinpath("contract.json").read_text(encoding="utf-8")
    return cast(dict[str, Any], json.loads(text))


def _freeze(value: Any) -> Any:
    """A read-only view of the decoded JSON, so a caller cannot edit the
    contract every other caller in the process reads."""
    if isinstance(value, dict):
        return MappingProxyType({k: _freeze(v) for k, v in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(v) for v in value)
    return value


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {k: _thaw(v) for k, v in value.items()}
    if isinstance(value, tuple):
        return [_thaw(v) for v in value]
    return value


def fill(template: str, values: Mapping[str, str]) -> str:
    """`template` with each `{name}` that `values` has replaced, literally
    and in one pass, so a brace in a value stays as it is; a `{name}` it
    does not have is left alone."""
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


_RAW = _load()

CONTRACT: Mapping[str, Any] = _freeze(_RAW)
"""The whole contract, as decoded from contract.json, read-only."""

CONTRACT_VERSION: str = _RAW["contract_version"]


@dataclass(frozen=True)
class AgentTool:
    """One local tool: its name, what it reads or changes (`kind`), and the
    JSON Schema of its arguments. `read_only` tools are the ones `plan`
    mode offers."""

    name: ToolName
    kind: ToolKind
    read_only: bool
    description: str
    parameters: Mapping[str, Any]

    def to_openai(self) -> dict[str, Any]:
        """The tool as `/v1/chat/completions` takes it in `tools`."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": _thaw(self.parameters),
            },
        }


@dataclass(frozen=True)
class ApprovalMode:
    """What a mode lets through without asking (`allow`) and what it asks
    the user about first (`ask`); every other tool is denied and is not
    offered to the model. `yolo` needs a flag each session
    (`requires_flag`) and is never saved as a default (`persistable`)."""

    name: ModeName
    summary: str
    allow: tuple[ToolName, ...]
    ask: tuple[ToolName, ...]
    persistable: bool
    requires_flag: bool


_EMPTY_OBJECT: Mapping[str, Any] = MappingProxyType(
    {"type": "object", "properties": MappingProxyType({}), "additionalProperties": False}
)


@dataclass(frozen=True)
class ExtraTool:
    """A client's own tool, beyond the contract's seven: web_fetch, an MCP
    server's tool, todo_write. `kind` decides it in each mode (see
    `allowed`); `read_only` is the tool's claim to change nothing, which
    in `plan` turns a `network` or `mcp` deny into ask. The name must not
    be a contract tool's."""

    name: str
    kind: AnyToolKind
    description: str
    parameters: Mapping[str, Any] = field(default=_EMPTY_OBJECT)
    read_only: bool = False

    def to_openai(self) -> dict[str, Any]:
        """The tool as `/v1/chat/completions` takes it in `tools`."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": _thaw(self.parameters),
            },
        }


@dataclass(frozen=True)
class ContextFile:
    """A file of instructions for the agent (MATTERWAVE.md, AGENTS.md,
    MATTERWAVE.local.md) for `system_prompt`: where it is, its text, and
    whether the user trusts it. An untrusted file is left out of the
    prompt. Pass them from the most shared to the most personal
    (`CONTEXT_FILES` order, MATTERWAVE.local.md last): the later file wins
    where two disagree."""

    path: str
    content: str
    trusted: bool


TOOLS: tuple[AgentTool, ...] = tuple(
    AgentTool(
        name=t["name"],
        kind=t["kind"],
        read_only=t["read_only"],
        description=t["description"],
        parameters=_freeze(t["parameters"]),
    )
    for t in _RAW["tools"]
)
TOOLS_BY_NAME: Mapping[str, AgentTool] = MappingProxyType({t.name: t for t in TOOLS})
TOOL_NAMES: tuple[ToolName, ...] = tuple(t.name for t in TOOLS)
READ_ONLY_TOOLS: tuple[ToolName, ...] = tuple(t.name for t in TOOLS if t.read_only)

APPROVAL_MODES: tuple[ApprovalMode, ...] = tuple(
    ApprovalMode(
        name=m["name"],
        summary=m["summary"],
        allow=tuple(m["allow"]),
        ask=tuple(m["ask"]),
        persistable=m["persistable"],
        requires_flag=m["requires_flag"],
    )
    for m in _RAW["approval_modes"]
)
MODES_BY_NAME: Mapping[str, ApprovalMode] = MappingProxyType({m.name: m for m in APPROVAL_MODES})
MODE_NAMES: tuple[ModeName, ...] = tuple(m.name for m in APPROVAL_MODES)
DEFAULT_MODE: ModeName = _RAW["default_mode"]

TOOL_KINDS: tuple[AnyToolKind, ...] = tuple(_RAW["tool_kinds"])
"""Every kind a tool may have: the contract's three, then network, mcp, meta."""
_APPROVAL = _RAW["approval"]
KIND_DECISIONS: Mapping[str, Mapping[str, Decision]] = _freeze(_APPROVAL["kind_decisions"])
"""Per mode, the decision for a client's own tool of each kind."""
READ_ONLY_DECISIONS: Mapping[str, Mapping[str, Decision]] = _freeze(
    _APPROVAL["read_only_decisions"]
)
"""Per mode, what a read-only tool of a kind gets where its kind is denied."""
APPROVAL_REFUSALS: Mapping[str, str] = MappingProxyType(dict(_APPROVAL["refusals"]))

_CONFINEMENT = _RAW["confinement"]
DEFAULT_DENY: tuple[str, ...] = tuple(_CONFINEMENT["deny"])
DENY_FILE: str = _CONFINEMENT["deny_file"]
SEARCH_IGNORE_FILES: tuple[str, ...] = tuple(_CONFINEMENT["search_ignore_files"])
PROTECTED: tuple[str, ...] = tuple(_CONFINEMENT["protected"])
REFUSALS: Mapping[str, str] = MappingProxyType(dict(_CONFINEMENT["refusals"]))
SETTINGS_DIR: str = _CONFINEMENT["settings_dir"]
CONTEXT_FILES: tuple[str, ...] = tuple(_CONFINEMENT["context_files"])
"""The context files a client reads from the root, in the order to pass
them to `system_prompt`: the shared MATTERWAVE.md and AGENTS.md, then the
user's own MATTERWAVE.local.md last, so that it wins where they disagree."""

EDIT_CRLF_FALLBACK: bool = _RAW["edit"]["crlf_fallback"]
EDIT_ERRORS: Mapping[str, str] = MappingProxyType(dict(_RAW["edit"]["errors"]))

DIFF_CONTEXT_LINES: int = _RAW["diff"]["context_lines"]
DIFF_MAX_EDIT_DISTANCE: int = _RAW["diff"]["max_edit_distance"]

LIMITS: Mapping[str, int] = MappingProxyType(dict(_RAW["limits"]))
SHELLS: Mapping[str, Mapping[str, Any]] = _freeze(_RAW["shell"])

OUTPUTS: Mapping[str, Any] = _freeze(_RAW["outputs"])
"""The read tools' and run_shell's output formats, markers and messages."""
TOOL_ERRORS: Mapping[str, str] = MappingProxyType(dict(_RAW["outputs"]["errors"]))

SYSTEM_PROMPT_VERSION: int = _RAW["system_prompt"]["version"]
SYSTEM_PROMPT_TEMPLATE: str = "\n".join(_RAW["system_prompt"]["template"])
PROMPT_EXTRA_TOOLS: Mapping[str, Any] = _freeze(_RAW["system_prompt"]["extra_tools"])
PROMPT_CONTEXT_FILES: Mapping[str, Any] = _freeze(_RAW["system_prompt"]["context_files"])
