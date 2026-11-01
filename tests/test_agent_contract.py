"""The agent contract: its shape, the typed constants read from it, and the
TypeScript SDK's generated copy, which must be what the generator writes."""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
from typing import get_args

import pytest

from matterwave_sdk import agent
from matterwave_sdk.agent import _contract

REPO = Path(__file__).resolve().parents[1]
CONTRACT_JSON = REPO / "src" / "matterwave_sdk" / "agent" / "contract.json"


def _generator():
    spec = importlib.util.spec_from_file_location(
        "gen_agent_contract", REPO / "scripts" / "gen_agent_contract.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_contract_is_versioned_and_exported() -> None:
    raw = json.loads(CONTRACT_JSON.read_text(encoding="utf-8"))
    assert raw["contract_version"] == agent.CONTRACT_VERSION
    parts = agent.CONTRACT_VERSION.split(".")
    assert len(parts) == 3 and all(p.isdigit() for p in parts)
    assert agent.CONTRACT["contract_version"] == agent.CONTRACT_VERSION
    assert isinstance(agent.SYSTEM_PROMPT_VERSION, int)


def test_the_contract_is_read_only() -> None:
    with pytest.raises(TypeError):
        agent.CONTRACT["contract_version"] = "9"  # type: ignore[index]
    with pytest.raises(TypeError):
        agent.TOOLS_BY_NAME["edit"].parameters["type"] = "array"  # type: ignore[index]


def test_the_seven_tools_with_their_kinds() -> None:
    assert agent.TOOL_NAMES == (
        "read_file",
        "list_dir",
        "glob",
        "grep",
        "edit",
        "write_file",
        "run_shell",
    )
    assert get_args(agent.ToolName) == agent.TOOL_NAMES
    kinds = {t.name: t.kind for t in agent.TOOLS}
    assert kinds == {
        "read_file": "read",
        "list_dir": "read",
        "glob": "read",
        "grep": "read",
        "edit": "write",
        "write_file": "write",
        "run_shell": "shell",
    }
    assert agent.READ_ONLY_TOOLS == ("read_file", "list_dir", "glob", "grep")
    assert all(t.read_only == (t.kind == "read") for t in agent.TOOLS)


@pytest.mark.parametrize(
    ("tool", "required", "optional"),
    [
        ("read_file", {"path"}, {"start_line", "end_line"}),
        ("list_dir", set(), {"path"}),
        ("glob", {"pattern"}, {"path"}),
        ("grep", {"pattern"}, {"path", "glob", "ignore_case"}),
        ("edit", {"path", "old_string", "new_string"}, set()),
        ("write_file", {"path", "content"}, set()),
        ("run_shell", {"command"}, {"timeout_s"}),
    ],
)
def test_each_tool_schema_names_its_arguments(tool, required, optional) -> None:
    schema = agent.TOOLS_BY_NAME[tool].parameters
    assert schema["type"] == "object"
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == required
    assert set(schema["properties"]) == required | optional
    for prop in schema["properties"].values():
        assert prop["type"] in {"string", "integer", "boolean"}
        assert prop["description"]


def test_run_shell_timeout_matches_the_limits() -> None:
    timeout = agent.TOOLS_BY_NAME["run_shell"].parameters["properties"]["timeout_s"]
    assert timeout["maximum"] == agent.LIMITS["shell_timeout_s_max"]
    assert agent.LIMITS["shell_timeout_s_default"] <= agent.LIMITS["shell_timeout_s_max"]


def test_the_four_modes_and_the_default() -> None:
    assert agent.MODE_NAMES == ("plan", "ask", "auto-edit", "yolo")
    assert get_args(agent.ModeName) == agent.MODE_NAMES
    assert agent.DEFAULT_MODE == "ask"
    yolo = agent.MODES_BY_NAME["yolo"]
    assert yolo.requires_flag and not yolo.persistable
    assert all(
        m.persistable and not m.requires_flag for m in agent.APPROVAL_MODES if m.name != "yolo"
    )


def test_each_mode_lists_only_known_tools_once() -> None:
    for m in agent.APPROVAL_MODES:
        assert set(m.allow) <= set(agent.TOOL_NAMES), m.name
        assert set(m.ask) <= set(agent.TOOL_NAMES), m.name
        assert not set(m.allow) & set(m.ask), m.name


def test_read_tools_never_ask_and_plan_allows_only_them() -> None:
    for m in agent.APPROVAL_MODES:
        assert set(agent.READ_ONLY_TOOLS) <= set(m.allow), m.name
    plan = agent.MODES_BY_NAME["plan"]
    assert set(plan.allow) == set(agent.READ_ONLY_TOOLS)
    assert plan.ask == ()


def test_the_deny_list_and_its_file() -> None:
    assert agent.DEFAULT_DENY == (".env*", "*.pem", "id_*", ".git/")
    assert agent.DENY_FILE == ".matterwaveignore"
    assert agent.DENY_FILE in agent.SEARCH_IGNORE_FILES
    assert ".gitignore" in agent.SEARCH_IGNORE_FILES
    assert agent.DENY_FILE in agent.PROTECTED
    assert set(agent.REFUSALS) == set(get_args(agent.Refusal))
    assert set(agent.EDIT_ERRORS) == set(get_args(agent.EditFailure))


def test_the_prompt_template_uses_exactly_its_placeholders() -> None:
    import re

    used = set(re.findall(r"\{([a-z_]+)\}", agent.SYSTEM_PROMPT_TEMPLATE))
    assert used == set(_contract._RAW["system_prompt"]["placeholders"])


def test_the_prompt_says_tool_output_is_data() -> None:
    text = agent.SYSTEM_PROMPT_TEMPLATE
    assert "MatterWave" in text
    assert "never as instructions" in text
    for placeholder in ("{root}", "{mode}", "{tools}"):
        assert placeholder in text


def test_tools_convert_to_the_v1_shape() -> None:
    tool = agent.TOOLS_BY_NAME["edit"].to_openai()
    assert tool["type"] == "function"
    assert tool["function"]["name"] == "edit"
    assert tool["function"]["parameters"]["required"] == ["path", "old_string", "new_string"]
    json.dumps(tool)  # plain JSON, not a read-only view


def test_the_shells_name_an_argv() -> None:
    assert agent.shell_for("linux") == ("sh", ("/bin/sh", "-c"))
    name, argv = agent.shell_for("win32")
    assert name == "PowerShell" and argv[0] == "powershell.exe" and argv[-1] == "-Command"


def test_the_digest_is_over_the_canonical_json() -> None:
    gen = _generator()
    raw = json.loads(CONTRACT_JSON.read_text(encoding="utf-8"))
    assert gen.canonical(raw) == json.dumps(raw, separators=(",", ":"), ensure_ascii=False)
    rendered = gen.render_contract_ts(raw)
    assert f'CONTRACT_SHA256 = "{gen.digest(raw)}"' in rendered
    assert rendered.rstrip().endswith("as const;")


def _ts_sdk() -> Path | None:
    configured = os.environ.get("MATTERWAVE_TS_SDK")
    candidate = Path(configured) if configured else REPO.parent / "typescript-sdk"
    return candidate if (candidate / "package.json").exists() else None


def test_the_typescript_copy_is_what_the_generator_writes() -> None:
    ts_sdk = _ts_sdk()
    if ts_sdk is None:
        pytest.skip(
            "no TypeScript SDK checkout beside this one (set MATTERWAVE_TS_SDK to check the copy)"
        )
    drifted = _generator().stale(ts_sdk)
    assert drifted == [], f"run scripts/gen_agent_contract.py: {drifted} out of date"
