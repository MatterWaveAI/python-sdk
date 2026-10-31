"""The tool loop from the caller's side: tool calls reassembled from a
stream's fragments, the assistant message and `tool` answers sent back,
and the two flags a client reads before offering its agent mode
(`supports_tools` on a model row, `local_agent_tools` on `/api/me`)."""

from __future__ import annotations

import json

import httpx
import pytest
from conftest import chunk, completion_body

from matterwave_sdk import (
    AppModel,
    AppModelList,
    AsyncMatterWave,
    ChatCompletionAccumulator,
    ChatCompletionChunk,
    MatterWave,
    Me,
    ToolCall,
    tool_message,
)

PROMPT = [{"role": "user", "content": "Read the README and the licence."}]

# Two calls whose fragments interleave, the way a model making parallel
# calls streams them: each index opens with its id and name, then its
# arguments arrive in pieces.
TWO_CALLS = (
    chunk({"role": "assistant", "content": ""})
    + chunk({"content": "Reading both."})
    + chunk({"tool_calls": [{"index": 0, "id": "call_a", "type": "function", "function": {"name": "read_file", "arguments": ""}}]})
    + chunk({"tool_calls": [{"index": 1, "id": "call_b", "type": "function", "function": {"name": "read_file"}}]})
    + chunk({"tool_calls": [{"index": 0, "function": {"arguments": "{\"path\":"}}]})
    + chunk({"tool_calls": [{"index": 1, "function": {"arguments": "{\"path\":\"LICENSE\"}"}}]})
    + chunk({"tool_calls": [{"index": 0, "function": {"arguments": "\"README.md\"}"}}]})
    + chunk({}, "tool_calls")
    + chunk(None, usage={"prompt_tokens": 30, "completion_tokens": 12, "total_tokens": 42})
    + "data: [DONE]\n\n"
)


def _stream(api, body: str):
    return api.post("/v1/chat/completions").mock(
        return_value=httpx.Response(200, headers={"Content-Type": "text/event-stream"}, content=body)
    )


def test_final_message_reassembles_interleaved_tool_calls(api, client: MatterWave) -> None:
    _stream(api, TWO_CALLS)
    message = client.chat.completions.create(messages=PROMPT, stream=True).final_message()
    assert message.content == "Reading both."
    assert message.tool_calls is not None
    assert [(c.id, c.function.name, c.function.arguments) for c in message.tool_calls] == [
        ("call_a", "read_file", '{"path":"README.md"}'),
        ("call_b", "read_file", '{"path":"LICENSE"}'),
    ]
    assert json.loads(message.tool_calls[0].function.arguments) == {"path": "README.md"}


def test_the_accumulator_keeps_finish_reason_and_usage(api, client: MatterWave) -> None:
    _stream(api, TWO_CALLS)
    accumulator = ChatCompletionAccumulator()
    seen: list[ChatCompletionChunk] = []
    for piece in client.chat.completions.create(messages=PROMPT, stream=True, include_usage=True):
        seen.append(piece)
        accumulator.add(piece)
    assert accumulator.finish_reason == "tool_calls"
    assert accumulator.usage is not None and accumulator.usage.total_tokens == 42
    assert accumulator.model == "grt-nano"
    assert len(accumulator.tool_calls) == 2
    assert len(seen) == 9


def test_a_pure_tool_call_turn_has_no_content(api, client: MatterWave) -> None:
    body = (
        chunk({"role": "assistant"})
        + chunk({"tool_calls": [{"index": 0, "id": "call_0", "type": "function", "function": {"name": "list_dir", "arguments": "{}"}}]})
        + chunk({}, "tool_calls")
        + "data: [DONE]\n\n"
    )
    _stream(api, body)
    message = client.chat.completions.create(messages=PROMPT, stream=True).final_message()
    assert message.content is None
    assert message.to_message() == {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {"id": "call_0", "type": "function", "function": {"name": "list_dir", "arguments": "{}"}}
        ],
    }


def test_a_plain_answer_has_no_tool_calls(api, client: MatterWave) -> None:
    _stream(api, chunk({"content": "Hi"}) + chunk({}, "stop") + "data: [DONE]\n\n")
    message = client.chat.completions.create(messages=PROMPT, stream=True).final_message()
    assert message.content == "Hi"
    assert message.tool_calls is None
    assert message.to_message() == {"role": "assistant", "content": "Hi"}


@pytest.mark.asyncio
async def test_the_async_stream_reassembles_the_same_way(api, async_client: AsyncMatterWave) -> None:
    _stream(api, TWO_CALLS)
    stream = await async_client.chat.completions.create(messages=PROMPT, stream=True)
    message = await stream.final_message()
    assert message.tool_calls is not None
    assert [c.id for c in message.tool_calls] == ["call_a", "call_b"]


def test_the_tool_round_goes_back_on_the_wire(api, client: MatterWave) -> None:
    """The loop's second request: the assistant message with its calls,
    then a `tool` message per call, sent as the caller built them."""
    first = completion_body()
    first["choices"][0]["message"] = {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {"id": "call_0", "type": "function", "function": {"name": "read_file", "arguments": "{\"path\":\"a.txt\"}"}}
        ],
    }
    first["choices"][0]["finish_reason"] = "tool_calls"
    route = api.post("/v1/chat/completions").mock(
        side_effect=[httpx.Response(200, json=first), httpx.Response(200, json=completion_body("Done."))]
    )
    tools = [{"type": "function", "function": {"name": "read_file", "parameters": {"type": "object"}}}]
    completion = client.chat.completions.create(messages=PROMPT, tools=tools, tool_choice="auto")
    call = completion.tool_calls[0]
    assert isinstance(call, ToolCall)
    messages = [
        *PROMPT,
        completion.choices[0].message.to_message(),
        tool_message(call.id, "hello from a.txt"),
    ]
    answer = client.chat.completions.create(messages=messages, tools=tools)
    assert answer.content == "Done."
    sent = json.loads(route.calls.last.request.content)
    assert sent["messages"][1] == {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {"id": "call_0", "type": "function", "function": {"name": "read_file", "arguments": "{\"path\":\"a.txt\"}"}}
        ],
    }
    assert sent["messages"][2] == {
        "role": "tool",
        "tool_call_id": "call_0",
        "content": "hello from a.txt",
    }
    assert sent["tools"] == tools


def test_tool_message_needs_the_call_id() -> None:
    with pytest.raises(ValueError, match="tool_call_id"):
        tool_message("", "result")


@pytest.mark.parametrize(("sent", "read"), [(True, True), (False, False), (None, None), ("yes", None)])
def test_supports_tools_is_read_as_sent(sent, read) -> None:
    row: dict = {"id": "grt", "label": "GRT", "included": True}
    if sent is not None:
        row["supports_tools"] = sent
    assert AppModel.from_dict(row).supports_tools is read


def test_supports_tools_on_the_model_list(api, client: MatterWave) -> None:
    api.get("/api/models").mock(
        return_value=httpx.Response(
            200,
            json={
                "object": "list",
                "default_model": "grt",
                "data": [
                    {"id": "grt", "label": "GRT", "included": True, "supports_tools": True},
                    {"id": "old", "label": "Old", "included": True, "supports_tools": False},
                    {"id": "mum", "label": "Mum", "included": True},
                ],
            },
        )
    )
    models = client.app_models()
    assert isinstance(models, AppModelList)
    assert [m.supports_tools for m in models] == [True, False, None]


@pytest.mark.parametrize(("sent", "read"), [(True, True), (False, False), (None, None)])
def test_local_agent_tools_on_me(api, client: MatterWave, sent, read) -> None:
    body: dict = {"subject": "u-1", "kind": "key", "roles": [], "org_id": "o-1"}
    if sent is not None:
        body["local_agent_tools"] = sent
    api.get("/api/me").mock(return_value=httpx.Response(200, json=body))
    me = client.me()
    assert isinstance(me, Me)
    assert me.local_agent_tools is read
