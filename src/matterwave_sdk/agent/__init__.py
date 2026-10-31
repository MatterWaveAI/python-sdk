"""The agent tool contract and the pure helpers a coding client builds on.

A MatterWave coding client (the TUI's `code` mode, the VS Code agent) runs
its own loop against `/v1/chat/completions`: it sends the tools below, runs
the calls the model makes on the user's machine, and sends the results
back. This module is what the clients share, so the Python and TypeScript
clients offer the same tools, gate them the same way and refuse the same
paths:

    from matterwave_sdk import agent

    agent.CONTRACT_VERSION                    # "1.0.0"
    tools = agent.tools_for_mode("plan")      # the `tools` for /v1: read-only
    agent.allowed("edit", "ask")              # "ask"
    prompt = agent.system_prompt("ask", root="/home/me/project")
    target = agent.confine(root, "src/app.py")          # or PathRefused
    new_text = agent.apply_edit(text, old, new)          # or EditError
    print(agent.unified_diff(text, new_text, target.relative))

The contract itself is `contract.json` beside this module, the one source
the TypeScript SDK's copy is generated from (scripts/gen_agent_contract.py).
"""

from ._contract import (
    APPROVAL_MODES,
    CONTRACT,
    CONTRACT_VERSION,
    DEFAULT_DENY,
    DEFAULT_MODE,
    DENY_FILE,
    DIFF_CONTEXT_LINES,
    DIFF_MAX_EDIT_DISTANCE,
    EDIT_ERRORS,
    LIMITS,
    MODE_NAMES,
    MODES_BY_NAME,
    PROTECTED,
    READ_ONLY_TOOLS,
    REFUSALS,
    SEARCH_IGNORE_FILES,
    SHELLS,
    SYSTEM_PROMPT_TEMPLATE,
    SYSTEM_PROMPT_VERSION,
    TOOL_NAMES,
    TOOLS,
    TOOLS_BY_NAME,
    AgentTool,
    ApprovalMode,
    Decision,
    EditFailure,
    ModeName,
    Refusal,
    ToolKind,
    ToolName,
)
from .confine import (
    ConfinedPath,
    DenyList,
    PathRefused,
    confine,
    glob_to_regex,
    is_absolute,
    load_deny_list,
    resolve_links,
)
from .diff import split_lines, unified_diff
from .edit import EditError, apply_edit, occurrences
from .modes import allowed, mode, platform_name, shell_for, system_prompt, tools_for_mode

__all__ = [
    "APPROVAL_MODES",
    "CONTRACT",
    "CONTRACT_VERSION",
    "DEFAULT_DENY",
    "DEFAULT_MODE",
    "DENY_FILE",
    "DIFF_CONTEXT_LINES",
    "DIFF_MAX_EDIT_DISTANCE",
    "EDIT_ERRORS",
    "LIMITS",
    "MODE_NAMES",
    "MODES_BY_NAME",
    "PROTECTED",
    "READ_ONLY_TOOLS",
    "REFUSALS",
    "SEARCH_IGNORE_FILES",
    "SHELLS",
    "SYSTEM_PROMPT_TEMPLATE",
    "SYSTEM_PROMPT_VERSION",
    "TOOL_NAMES",
    "TOOLS",
    "TOOLS_BY_NAME",
    "AgentTool",
    "ApprovalMode",
    "ConfinedPath",
    "Decision",
    "DenyList",
    "EditError",
    "EditFailure",
    "ModeName",
    "PathRefused",
    "Refusal",
    "ToolKind",
    "ToolName",
    "allowed",
    "apply_edit",
    "confine",
    "glob_to_regex",
    "is_absolute",
    "load_deny_list",
    "mode",
    "occurrences",
    "platform_name",
    "resolve_links",
    "shell_for",
    "split_lines",
    "system_prompt",
    "tools_for_mode",
    "unified_diff",
]
