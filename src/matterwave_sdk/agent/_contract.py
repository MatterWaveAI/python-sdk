"""The contract, read from `contract.json` beside this module and held in
typed constants. The JSON is the one source: the TypeScript SDK's copy is
generated from it, and nothing here restates a value it carries."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from importlib import resources
from types import MappingProxyType
from typing import Any, Literal, cast

Decision = Literal["allow", "ask", "deny"]
ToolKind = Literal["read", "write", "shell"]
ToolName = Literal["read_file", "list_dir", "glob", "grep", "edit", "write_file", "run_shell"]
ModeName = Literal["plan", "ask", "auto-edit", "yolo"]
Refusal = Literal["empty", "parent", "outside", "symlink", "denied", "protected"]
EditFailure = Literal["missing", "empty", "unchanged", "not_found", "not_unique"]


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

_CONFINEMENT = _RAW["confinement"]
DEFAULT_DENY: tuple[str, ...] = tuple(_CONFINEMENT["deny"])
DENY_FILE: str = _CONFINEMENT["deny_file"]
SEARCH_IGNORE_FILES: tuple[str, ...] = tuple(_CONFINEMENT["search_ignore_files"])
PROTECTED: tuple[str, ...] = tuple(_CONFINEMENT["protected"])
REFUSALS: Mapping[str, str] = MappingProxyType(dict(_CONFINEMENT["refusals"]))

EDIT_CRLF_FALLBACK: bool = _RAW["edit"]["crlf_fallback"]
EDIT_ERRORS: Mapping[str, str] = MappingProxyType(dict(_RAW["edit"]["errors"]))

DIFF_CONTEXT_LINES: int = _RAW["diff"]["context_lines"]
DIFF_MAX_EDIT_DISTANCE: int = _RAW["diff"]["max_edit_distance"]

LIMITS: Mapping[str, int] = MappingProxyType(dict(_RAW["limits"]))
SHELLS: Mapping[str, Mapping[str, Any]] = _freeze(_RAW["shell"])

SYSTEM_PROMPT_VERSION: int = _RAW["system_prompt"]["version"]
SYSTEM_PROMPT_TEMPLATE: str = "\n".join(_RAW["system_prompt"]["template"])
