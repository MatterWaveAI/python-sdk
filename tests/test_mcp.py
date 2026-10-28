"""The MCP wrapper: the handshake that precedes the first call, the modern
per-request envelope and its mirrored headers, scope-filtered tool listings,
and the two shapes a refusal takes -- a JSON-RPC error before any method
runs (with its HTTP status, so 429 is a `RateLimitError`) and a tool failure
that arrives as an `isError` result. The stand-in answers the bodies
api/matterwave/routes_mcp.py documents."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from conftest import REQUEST_ID

from matterwave_sdk import (
    AsyncMatterWave,
    BadRequestError,
    MatterWave,
    McpError,
    McpTool,
    McpToolError,
    McpToolResult,
    RateLimitError,
    SendMessageResult,
)
from matterwave_sdk._mcp import B64_PREFIX, B64_SUFFIX, SEND_MESSAGE_TIMEOUT_S, _name_header

TOOLS = {
    "tools": [
        {
            "name": "list_conversations",
            "title": "List conversations",
            "description": "One page of the user's conversations, most recent first.",
            "inputSchema": {
                "type": "object",
                "properties": {"filter": {"type": "string", "enum": ["default", "all", "pinned", "archived"]}},
                "additionalProperties": False,
            },
            "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True},
        },
        {
            "name": "send_message",
            "title": "Send a message",
            "description": "Append a user message to a conversation and return MatterWave's answer.",
            "inputSchema": {
                "type": "object",
                "properties": {"conversation_id": {"type": "string"}, "content": {"type": "string"}},
                "required": ["conversation_id", "content"],
                "additionalProperties": False,
            },
            "annotations": {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False},
        },
    ]
}


def body_of(request: httpx.Request) -> dict[str, Any]:
    return json.loads(request.content)


def mount(api, table: dict[str, dict[str, Any]], *, status: int | None = None):
    """A JSON-RPC endpoint: each method answers with its canned payload --
    `{"result": ...}` or `{"error": ..., "status": 401}` -- echoing the id
    the request carried."""

    def handler(request: httpx.Request) -> httpx.Response:
        sent = body_of(request)
        assert sent["jsonrpc"] == "2.0" and isinstance(sent.get("id"), int)
        payload = table[sent["method"]]
        answer: dict[str, Any] = {"jsonrpc": "2.0", "id": sent["id"]}
        if "error" in payload:
            answer["error"] = payload["error"]
            return httpx.Response(
                payload.get("status", 200), json=answer, headers={"X-Request-Id": REQUEST_ID}
            )
        answer["result"] = payload["result"]
        return httpx.Response(200, json=answer, headers={"X-Request-Id": REQUEST_ID})

    return api.post("/mcp").mock(side_effect=handler)


def text_result(text: str, structured: dict[str, Any]) -> dict[str, Any]:
    return {
        "content": [{"type": "text", "text": text}],
        "structuredContent": structured,
        "isError": False,
    }


# --- the handshake era ----------------------------------------------------------


def test_the_handshake_precedes_the_first_call_and_is_not_repeated(
    api, client: MatterWave
) -> None:
    route = mount(
        api,
        {
            "initialize": {"result": {"protocolVersion": "2025-06-18", "serverInfo": {"name": "matterwave"}}},
            "tools/list": {"result": TOOLS},
        },
    )
    tools = client.mcp.list_tools()
    assert [t.name for t in tools] == ["list_conversations", "send_message"]
    methods = [body_of(call.request)["method"] for call in route.calls]
    assert methods == ["initialize", "tools/list"]
    opened = body_of(route.calls[0].request)
    assert opened["params"]["protocolVersion"] == "2025-06-18"
    assert opened["params"]["clientInfo"]["name"] == "matterwave-sdk"
    # Statelessly stateful: the second call on the same wrapper speaks for itself.
    client.mcp.list_tools()
    assert [body_of(call.request)["method"] for call in route.calls][-1] == "tools/list"


def test_an_explicit_initialize_returns_the_server_s_answer(api, client: MatterWave) -> None:
    route = mount(
        api,
        {
            "initialize": {
                "result": {
                    "protocolVersion": "2025-11-25",
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": "matterwave", "title": "MatterWave", "version": "1.0"},
                    "instructions": "MatterWave holds this user's chat conversations.",
                }
            }
        },
    )
    answer = client.mcp.initialize(protocol_version="2025-11-25")
    assert answer["protocolVersion"] == "2025-11-25"
    assert answer["serverInfo"]["name"] == "matterwave"
    # An explicit initialize always goes to the wire; it is the lazy one
    # inside call_tool that fires once per version and then never again.
    assert client.mcp.initialize(protocol_version="2025-11-25") == answer
    assert route.call_count == 2


def test_a_version_the_server_has_never_heard_of_is_answered_with_its_default(
    api, client: MatterWave
) -> None:
    # The server answers an unknown requested revision with 2025-06-18 and
    # decides; the wrapper relays whatever came back.
    route = mount(
        api,
        {"initialize": {"result": {"protocolVersion": "2025-06-18"}}},
    )
    answer = client.mcp.initialize(protocol_version="2025-03-26")
    assert answer["protocolVersion"] == "2025-06-18"
    assert body_of(route.calls[0].request)["params"]["protocolVersion"] == "2025-03-26"


def test_list_tools_honours_the_key_s_scopes(api, client: MatterWave) -> None:
    # A read-only key is shown no write: the server's tools/list is already
    # filtered, and the wrapper relays the tools it names.
    read_only = {k: v for k, v in TOOLS.items()}
    read_only["tools"] = [TOOLS["tools"][0]]
    route = mount(
        api,
        {
            "initialize": {"result": {"protocolVersion": "2025-06-18"}},
            "tools/list": {"result": read_only},
        },
    )
    tools = client.mcp.list_tools()
    assert [t.name for t in tools] == ["list_conversations"]
    tool = tools[0]
    assert isinstance(tool, McpTool)
    assert tool.title == "List conversations"
    assert tool.input_schema["properties"]["filter"]["enum"] == ["default", "all", "pinned", "archived"]
    assert tool.annotations["readOnlyHint"] is True
    assert body_of(route.calls.last.request)["method"] == "tools/list"


def test_call_tool_returns_the_text_blocks_and_the_structured_content(
    api, client: MatterWave
) -> None:
    page = {"conversations": [{"id": "c-1", "title": "Plans"}], "filter": "default", "next_cursor": None}
    route = mount(
        api,
        {
            "initialize": {"result": {"protocolVersion": "2025-06-18"}},
            "tools/call": {
                "result": {
                    "content": [
                        {"type": "text", "text": "- c-1 · Plans · 2 messages"},
                        {"type": "text", "text": "More: call again with cursor."},
                    ],
                    "structuredContent": page,
                }
            },
        },
    )
    got = client.mcp.call_tool("list_conversations", {"filter": "default"})
    assert isinstance(got, McpToolResult)
    assert got.text == "- c-1 · Plans · 2 messagesMore: call again with cursor."
    assert got.structured == page
    assert got.raw["structuredContent"] == page
    sent = body_of(route.calls.last.request)
    assert sent["params"] == {"name": "list_conversations", "arguments": {"filter": "default"}}


def test_a_tool_failure_is_a_result_that_raises_mcp_tool_error(api, client: MatterWave) -> None:
    route = mount(
        api,
        {
            "initialize": {"result": {"protocolVersion": "2025-06-18"}},
            "tools/call": {
                "result": {
                    "isError": True,
                    "content": [{"type": "text", "text": "The org is over its budget."}],
                    "structuredContent": {
                        "error": {
                            "code": "budget",
                            "detail": "The org is over its budget.",
                            "request_id": REQUEST_ID,
                            "status": 402,
                        }
                    },
                }
            },
        },
    )
    with pytest.raises(McpToolError) as caught:
        client.mcp.call_tool("send_message", {"conversation_id": "c-1", "content": "hi"})
    err = caught.value
    assert err.tool == "send_message"
    assert err.error_code == "budget"
    assert err.detail == "The org is over its budget."
    assert str(err) == "The org is over its budget."
    assert err.request_id == REQUEST_ID
    assert err.status == 402
    assert err.error["code"] == "budget"
    # The call itself succeeded: the JSON-RPC layer answered 200 with a result.
    assert route.calls.last.response.status_code == 200


def test_a_tool_failure_without_a_structured_error_still_raises(api, client: MatterWave) -> None:
    mount(
        api,
        {
            "initialize": {"result": {"protocolVersion": "2025-06-18"}},
            "tools/call": {
                "result": {"isError": True, "content": [{"type": "text", "text": "Refused."}]}
            },
        },
    )
    with pytest.raises(McpToolError) as caught:
        client.mcp.call_tool("send_message", {"conversation_id": "c-1", "content": "hi"})
    assert caught.value.error_code == "tool_error"
    assert caught.value.detail == "Refused."


def test_a_json_rpc_error_in_a_200_is_an_mcp_error(api, client: MatterWave) -> None:
    # The handshake era puts every JSON-RPC error in a 200.
    mount(
        api,
        {
            "initialize": {"result": {"protocolVersion": "2025-06-18"}},
            "tools/call": {
                "error": {"code": -32602, "message": "Unknown tool: 'nope'"},
            },
        },
    )
    with pytest.raises(McpError) as caught:
        client.mcp.call_tool("nope", {})
    assert caught.value.code == -32602
    assert "Unknown tool" in str(caught.value)


def test_the_key_s_rate_limit_is_a_json_rpc_error_with_its_http_status(
    api, client: MatterWave, sleeps
) -> None:
    mount(
        api,
        {
            "tools/call": {
                "status": 429,
                "error": {
                    "code": 429,
                    "message": "Rate limit of 60 requests per minute exceeded for this MCP client.",
                    "data": {"status": 429, "request_id": REQUEST_ID, "retry_after_s": 30},
                },
            }
        },
    )
    with pytest.raises(RateLimitError) as caught:
        client.mcp.call_tool("list_conversations", {}, protocol_version="2026-07-28")
    assert caught.value.status_code == 429
    assert caught.value.message.startswith("Rate limit of 60 requests per minute")
    assert caught.value.request_id == REQUEST_ID


# --- the modern era -------------------------------------------------------------


def test_modern_requests_carry_the_envelope_and_the_mirrored_headers(
    api, client: MatterWave
) -> None:
    route = mount(api, {"tools/call": {"result": text_result("ok", {})}})
    client.mcp.call_tool("list_conversations", {"filter": "all"}, protocol_version="2026-07-28")
    assert [body_of(call.request)["method"] for call in route.calls] == ["tools/call"]
    sent = body_of(route.calls.last.request)
    meta = sent["params"]["_meta"]
    assert meta["io.modelcontextprotocol/protocolVersion"] == "2026-07-28"
    assert "io.modelcontextprotocol/clientCapabilities" in meta
    headers = route.calls.last.request.headers
    assert headers["MCP-Protocol-Version"] == "2026-07-28"
    assert headers["Mcp-Method"] == "tools/call"
    assert headers["Mcp-Name"] == "list_conversations"


def test_modern_names_that_are_not_header_safe_travel_base64(api, client: MatterWave) -> None:
    header = _name_header("konversation://über")
    assert header.startswith(B64_PREFIX) and header.endswith(B64_SUFFIX)
    assert _name_header("list_conversations") == "list_conversations"
    # A `tools/call` whose name is plain ASCII mirrors it verbatim.
    route = mount(api, {"tools/call": {"result": text_result("ok", {})}})
    client.mcp.call_tool("list_conversations", {}, protocol_version="2026-07-28")
    assert route.calls.last.request.headers["Mcp-Name"] == "list_conversations"


def test_an_unsupported_modern_version_is_refused_with_its_status(api, client: MatterWave) -> None:
    mount(
        api,
        {
            "tools/call": {
                "status": 400,
                "error": {
                    "code": -32022,
                    "message": "Unsupported protocol version",
                    "data": {"supported": ["2026-07-28"], "requested": "2026-01-01"},
                },
            }
        },
    )
    with pytest.raises(BadRequestError) as caught:
        client.mcp.call_tool("list_conversations", {}, protocol_version="2026-01-01")
    assert caught.value.status_code == 400
    assert caught.value.message == "Unsupported protocol version"


def test_send_message_returns_the_whole_answer_with_the_long_timeout(
    api, client: MatterWave
) -> None:
    message = {
        "id": "m-asst",
        "role": "assistant",
        "content": "One: coffee. Two: sugar. Three: cream.",
        "created_at": "2026-10-27T09:00:00+00:00",
        "model": "grt-nano",
        "parent_id": "m-user",
        "truncated": False,
    }
    route = mount(
        api,
        {
            "initialize": {"result": {"protocolVersion": "2025-06-18"}},
            "tools/call": {
                "result": text_result(
                    "One: coffee. Two: sugar. Three: cream.",
                    {
                        "conversation_id": "c-1",
                        "message": message,
                        "title": "Shopping list",
                        "notices": ["Older messages may be cut."],
                        "truncated": False,
                    },
                )
            },
        },
    )
    answer = client.mcp.send_message("c-1", "Shopping list, please.", model="grt-nano")
    assert isinstance(answer, SendMessageResult)
    assert answer.conversation_id == "c-1"
    assert answer.text.startswith("One: coffee.")
    assert answer.message is not None and answer.message["id"] == "m-asst"
    assert answer.title == "Shopping list"
    assert answer.notices == ["Older messages may be cut."]
    assert not answer.truncated
    sent = body_of(route.calls.last.request)
    assert sent["params"]["name"] == "send_message"
    assert sent["params"]["arguments"] == {
        "conversation_id": "c-1",
        "content": "Shopping list, please.",
        "model": "grt-nano",
    }
    # The one long tool gets the long timeout, not the client's 60s.
    assert route.calls.last.request.extensions["timeout"]["read"] == SEND_MESSAGE_TIMEOUT_S


def test_an_empty_protocol_version_is_refused_before_the_wire(client: MatterWave) -> None:
    with pytest.raises(ValueError):
        client.mcp.call_tool("list_conversations", {}, protocol_version="")


def test_initialize_refuses_a_modern_version(client: MatterWave) -> None:
    with pytest.raises(ValueError, match="handshake revisions"):
        client.mcp.initialize(protocol_version="2026-07-28")


# --- the async wrapper ----------------------------------------------------------


@pytest.mark.asyncio
async def test_the_async_wrapper_speaks_the_same_protocol(api, async_client: AsyncMatterWave) -> None:
    route = mount(
        api,
        {
            "initialize": {"result": {"protocolVersion": "2025-06-18"}},
            "tools/call": {
                "result": text_result("Created conversation c-2 · Fresh", {"conversation": {"id": "c-2"}})
            },
        },
    )
    answer = await async_client.mcp.call_tool(
        "create_conversation", {"title": "Fresh"}, protocol_version="2026-07-28"
    )
    assert answer.text == "Created conversation c-2 · Fresh"
    methods = [body_of(call.request)["method"] for call in route.calls]
    assert methods == ["tools/call"]
    headers = route.calls.last.request.headers
    assert headers["MCP-Protocol-Version"] == "2026-07-28"
    assert headers["Mcp-Name"] == "create_conversation"


@pytest.mark.asyncio
async def test_the_async_send_message_reads_the_whole_answer(
    api, async_client: AsyncMatterWave
) -> None:
    mount(
        api,
        {
            "initialize": {"result": {"protocolVersion": "2025-06-18"}},
            "tools/call": {
                "result": text_result(
                    "Half an answer, cut by the limit.",
                    {"conversation_id": "c-1", "message": None, "title": None, "notices": [], "truncated": True},
                )
            },
        },
    )
    answer = await async_client.mcp.send_message("c-1", "go on")
    assert answer.text == "Half an answer, cut by the limit."
    assert answer.message is None and answer.title is None
    assert answer.truncated
