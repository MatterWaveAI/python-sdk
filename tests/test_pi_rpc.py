"""Tests for `matterwave_sdk.pi`: environment/provider config helpers and JSONL RPC sessions."""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from matterwave_sdk import AsyncMatterWave, MatterWave, MissingAPIKeyError, Model, ModelList, pi
from matterwave_sdk.agent import PathRefused
from matterwave_sdk.pi import (
    AsyncPiResource,
    AsyncPiRpcSession,
    PiEvent,
    PiModelConfig,
    PiResource,
    PiRpcSession,
    build_pi_env,
    build_pi_provider_config,
)

MOCK_PI_RPC_SCRIPT = textwrap.dedent(
    """
    import json
    import sys

    state = {
        "sessionId": "sess_mock_1",
        "provider": "matterwave",
        "modelId": "grt-1",
        "isStreaming": False,
        "messageCount": 0,
    }

    def send(obj):
        sys.stdout.write(json.dumps(obj) + "\\n")
        sys.stdout.flush()

    for raw in sys.stdin:
        line = raw.strip()
        if not line:
            continue
        cmd = json.loads(line)
        cmd_id = cmd.get("id")
        cmd_type = cmd.get("type")

        if cmd_type == "get_state":
            send({
                "id": cmd_id,
                "type": "response",
                "command": "get_state",
                "success": True,
                "data": dict(state),
            })
        elif cmd_type == "set_model":
            state["provider"] = cmd.get("provider", "matterwave")
            state["modelId"] = cmd.get("modelId", "grt-1")
            send({
                "id": cmd_id,
                "type": "response",
                "command": "set_model",
                "success": True,
                "data": {"provider": state["provider"], "id": state["modelId"]},
            })
        elif cmd_type == "abort":
            state["isStreaming"] = False
            send({
                "id": cmd_id,
                "type": "response",
                "command": "abort",
                "success": True,
            })
        elif cmd_type == "prompt":
            msg = cmd.get("message", "")
            state["messageCount"] += 1
            send({
                "id": cmd_id,
                "type": "response",
                "command": "prompt",
                "success": True,
                "data": {"disposition": "started"},
            })
            send({"type": "agent_start"})
            send({
                "type": "message_update",
                "assistantMessageEvent": {"type": "text_delta", "delta": "Hello "},
            })
            if "escape" in msg:
                send({
                    "type": "tool_execution_start",
                    "toolCallId": "call_bad",
                    "toolName": "read",
                    "args": {"path": "../outside.txt"},
                })
                continue
            if "dotenv" in msg:
                send({
                    "type": "tool_execution_start",
                    "toolCallId": "call_env",
                    "toolName": "read",
                    "args": {"path": ".env"},
                })
                continue
            send({
                "type": "tool_execution_start",
                "toolCallId": "call_1",
                "toolName": "read",
                "args": {"path": "src/main.py"},
            })
            send({
                "type": "tool_execution_end",
                "toolCallId": "call_1",
                "toolName": "read",
                "result": {"content": [{"type": "text", "text": "print(42)"}]},
                "isError": False,
            })
            send({
                "type": "message_update",
                "delta": "world!",
            })
            send({
                "type": "agent_end",
                "messages": [{"role": "assistant", "content": "Hello world!"}],
            })
    """
)


def _mock_command() -> list[str]:
    return [sys.executable, "-u", "-c", MOCK_PI_RPC_SCRIPT]


# --- config helpers ----------------------------------------------------------


def test_build_pi_env_from_client_and_explicit_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    client = MatterWave(api_key="mw_test_123", base_url="https://api.matterwave.test/v1")
    env = build_pi_env(
        client,
        preset_id="preset_coding",
        temporary=True,
        extra_env={"CUSTOM_FLAG": "enabled"},
    )
    assert env["MATTERWAVE_API_KEY"] == "mw_test_123"
    assert env["MATTERWAVE_BASE_URL"] == "https://api.matterwave.test"
    assert env["MATTERWAVE_PRESET_ID"] == "preset_coding"
    assert env["MATTERWAVE_TEMPORARY"] == "1"
    assert env["CUSTOM_FLAG"] == "enabled"

    async_client = AsyncMatterWave(api_key="mw_async_456", base_url="https://async.matterwave.test")
    env2 = build_pi_env(async_client)
    assert env2["MATTERWAVE_API_KEY"] == "mw_async_456"
    assert env2["MATTERWAVE_BASE_URL"] == "https://async.matterwave.test"
    assert env2["MATTERWAVE_PRESET_ID"] == ""
    assert env2["MATTERWAVE_TEMPORARY"] == "0"

    monkeypatch.delenv("MATTERWAVE_API_KEY", raising=False)
    with pytest.raises(MissingAPIKeyError):
        build_pi_env(None, base_url="https://api.matterwave.test")


def test_build_pi_provider_config_queries_models_with_fallback() -> None:
    mock_client = MagicMock(spec=MatterWave)
    mock_client.base_url = "https://api.matterwave.test"
    mock_client._config = MagicMock(
        api_key="mw_secret",
        base_url="https://api.matterwave.test",
        default_headers={"X-Org": "org_1"},
    )
    mock_client.models = MagicMock()
    mock_client.models.list.return_value = ModelList(
        data=[
            Model(id="grt-1", created=1700000000, owned_by="matterwave"),
            Model(id="custom-coder-v2", created=1700000001, owned_by="matterwave"),
        ]
    )

    cfg = build_pi_provider_config(mock_client, preset_id="preset_99", temporary=True)
    assert cfg["baseUrl"] == "https://api.matterwave.test/v1"
    assert cfg["apiKey"] == "mw_secret"
    assert cfg["api"] == "openai-completions"
    assert cfg["headers"]["X-Org"] == "org_1"
    assert cfg["headers"]["X-MatterWave-Preset-Id"] == "preset_99"
    assert cfg["headers"]["X-MatterWave-Temporary"] == "true"

    models: list[PiModelConfig] = cfg["models"]
    assert [m["id"] for m in models] == ["grt-1", "custom-coder-v2"]
    assert models[0]["reasoning"] is True
    assert models[0]["contextWindow"] == 262144

    # Graceful fallback when models.list() raises
    mock_client.models.list.side_effect = RuntimeError("network offline")
    fallback_cfg = build_pi_provider_config(mock_client)
    fallback_ids = [m["id"] for m in fallback_cfg["models"]]
    assert "grt-1" in fallback_ids
    assert "grt-1-mini" in fallback_ids
    assert "matterwave-default" in fallback_ids


# --- synchronous RPC session -------------------------------------------------


def test_sync_pi_rpc_session_prompt_state_model_and_abort(tmp_path: Path) -> None:
    assert pi.PiRpcSession is PiRpcSession
    with PiRpcSession(command=_mock_command(), workspace_root=tmp_path) as session:
        state = session.get_state()
        assert state["sessionId"] == "sess_mock_1"
        assert state["modelId"] == "grt-1"

        updated = session.set_model("matterwave", "grt-1-mini")
        assert updated == {"provider": "matterwave", "id": "grt-1-mini"}
        assert session.get_state()["modelId"] == "grt-1-mini"

        events: list[PiEvent] = list(session.prompt("Summarize main.py"))
        event_types = [e.type for e in events]
        assert event_types == [
            "agent_start",
            "message_update",
            "tool_execution_start",
            "tool_execution_end",
            "message_update",
            "agent_end",
        ]

        deltas = [e.delta for e in events if e.type == "message_update"]
        assert deltas == ["Hello ", "world!"]

        tool_start = next(e for e in events if e.type == "tool_execution_start")
        assert tool_start.tool_name == "read"
        assert tool_start.tool_call_id == "call_1"
        assert tool_start.args == {"path": "src/main.py"}

        tool_end = next(e for e in events if e.type == "tool_execution_end")
        assert tool_end.tool_name == "read"
        assert tool_end.tool_call_id == "call_1"
        assert tool_end.is_error is False
        assert tool_end.result == {"content": [{"type": "text", "text": "print(42)"}]}

        abort_resp = session.abort()
        assert abort_resp["command"] == "abort"
        assert abort_resp["success"] is True


def test_sync_pi_rpc_session_enforces_path_confinement_and_deny_list(tmp_path: Path) -> None:
    with PiRpcSession(command=_mock_command(), workspace_root=tmp_path) as session:
        with pytest.raises(PathRefused) as exc_info:
            list(session.prompt("Please escape workspace"))
        assert exc_info.value.reason == "parent"

    with PiRpcSession(command=_mock_command(), workspace_root=tmp_path) as session:
        with pytest.raises(PathRefused) as exc_info_env:
            list(session.prompt("Please read dotenv"))
        assert exc_info_env.value.reason == "denied"


# --- asynchronous RPC session ------------------------------------------------


@pytest.mark.asyncio
async def test_async_pi_rpc_session_prompt_state_model_and_abort(tmp_path: Path) -> None:
    assert pi.AsyncPiRpcSession is AsyncPiRpcSession
    async with AsyncPiRpcSession(command=_mock_command(), workspace_root=tmp_path) as session:
        state = await session.get_state()
        assert state["sessionId"] == "sess_mock_1"

        updated = await session.set_model("matterwave", "grt-1-fast")
        assert updated == {"provider": "matterwave", "id": "grt-1-fast"}
        assert (await session.get_state())["modelId"] == "grt-1-fast"

        events: list[PiEvent] = [e async for e in session.prompt("Async run")]
        assert [e.type for e in events] == [
            "agent_start",
            "message_update",
            "tool_execution_start",
            "tool_execution_end",
            "message_update",
            "agent_end",
        ]
        assert "".join(e.delta or "" for e in events) == "Hello world!"

        abort_resp = await session.abort()
        assert abort_resp["command"] == "abort"
        assert abort_resp["success"] is True

        with pytest.raises(PathRefused) as exc_info:
            _ = [e async for e in session.prompt("escape")]
        assert exc_info.value.reason == "parent"


# --- client.pi resource namespace --------------------------------------------


def test_sync_client_pi_namespace(tmp_path: Path) -> None:
    client = MatterWave(
        api_key="mw_bound_sync_key",
        base_url="https://api.matterwave.test",
        default_headers={"X-Bound": "sync"},
    )
    assert isinstance(client.pi, PiResource)
    assert client.pi is client.pi

    env = client.pi.build_env(
        preset_id="preset_sync",
        temporary=True,
        extra_env={"EXTRA_KEY": "val"},
    )
    assert env["MATTERWAVE_API_KEY"] == "mw_bound_sync_key"
    assert env["MATTERWAVE_BASE_URL"] == "https://api.matterwave.test"
    assert env["MATTERWAVE_PRESET_ID"] == "preset_sync"
    assert env["MATTERWAVE_TEMPORARY"] == "1"
    assert env["EXTRA_KEY"] == "val"

    client.models = MagicMock()  # type: ignore[misc]
    client.models.list.return_value = ModelList(
        data=[Model(id="grt-1", created=1700000000, owned_by="matterwave")]
    )
    cfg = client.pi.build_provider_config(preset_id="preset_sync", temporary=True)
    assert cfg["baseUrl"] == "https://api.matterwave.test/v1"
    assert cfg["apiKey"] == "mw_bound_sync_key"
    assert cfg["headers"]["X-Bound"] == "sync"
    assert cfg["headers"]["X-MatterWave-Preset-Id"] == "preset_sync"
    assert cfg["headers"]["X-MatterWave-Temporary"] == "true"
    assert [m["id"] for m in cfg["models"]] == ["grt-1"]

    with client.pi.create_rpc_session(command=_mock_command(), workspace_root=tmp_path) as session:
        assert isinstance(session, PiRpcSession)
        assert session._env["MATTERWAVE_API_KEY"] == "mw_bound_sync_key"
        state = session.get_state()
        assert state["sessionId"] == "sess_mock_1"
    client.close()


@pytest.mark.asyncio
async def test_async_client_pi_namespace(tmp_path: Path) -> None:
    from unittest.mock import AsyncMock

    client = AsyncMatterWave(
        api_key="mw_bound_async_key",
        base_url="https://async.matterwave.test",
        default_headers={"X-Bound": "async"},
    )
    assert isinstance(client.pi, AsyncPiResource)
    assert client.pi is client.pi

    env = client.pi.build_env(preset_id="preset_async", temporary=False)
    assert env["MATTERWAVE_API_KEY"] == "mw_bound_async_key"
    assert env["MATTERWAVE_BASE_URL"] == "https://async.matterwave.test"
    assert env["MATTERWAVE_PRESET_ID"] == "preset_async"
    assert env["MATTERWAVE_TEMPORARY"] == "0"

    client.models = MagicMock()  # type: ignore[misc]
    client.models.list = AsyncMock(
        return_value=ModelList(
            data=[Model(id="grt-1-mini", created=1700000000, owned_by="matterwave")]
        )
    )
    cfg = await client.pi.build_provider_config(preset_id="preset_async", temporary=True)
    assert cfg["baseUrl"] == "https://async.matterwave.test/v1"
    assert cfg["apiKey"] == "mw_bound_async_key"
    assert cfg["headers"]["X-Bound"] == "async"
    assert cfg["headers"]["X-MatterWave-Preset-Id"] == "preset_async"
    assert cfg["headers"]["X-MatterWave-Temporary"] == "true"
    assert [m["id"] for m in cfg["models"]] == ["grt-1-mini"]

    async with client.pi.create_rpc_session(
        command=_mock_command(), workspace_root=tmp_path
    ) as session:
        assert isinstance(session, AsyncPiRpcSession)
        assert session._env["MATTERWAVE_API_KEY"] == "mw_bound_async_key"
        state = await session.get_state()
        assert state["sessionId"] == "sess_mock_1"
    await client.close()
