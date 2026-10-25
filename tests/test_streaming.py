"""The stream, frame by frame: role, deltas, stop, usage, [DONE], and the
error envelope the API sends when the upstream dies after the headers."""

from __future__ import annotations

import json

import httpx
import pytest
from conftest import REQUEST_ID, chunk

from matterwave_sdk import APIError, AsyncMatterWave, ChatCompletionChunk, MatterWave

FRAMES = (
    chunk({"role": "assistant", "content": ""})
    + chunk({"content": "Hello"})
    + chunk({"content": ", world"})
    + chunk({}, "stop")
    + chunk(None, usage={"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7})
    + "data: [DONE]\n\n"
)

ERROR_FRAME = (
    'data: {"error":{"message":"gateway died","type":"server_error",'
    '"param":null,"code":"upstream_error"}}\n\n'
)

PROMPT = [{"role": "user", "content": "hi"}]


def _stream(api, body: str) -> None:
    api.post("/v1/chat/completions").mock(
        return_value=httpx.Response(
            200,
            headers={"Content-Type": "text/event-stream", "X-Request-Id": REQUEST_ID},
            content=body,
        )
    )


def test_chunks_arrive_typed_and_in_order(api, client: MatterWave) -> None:
    _stream(api, FRAMES)
    stream = client.chat.completions.create(messages=PROMPT, stream=True, include_usage=True)
    chunks = list(stream)
    assert all(isinstance(c, ChatCompletionChunk) for c in chunks)
    assert [c.content for c in chunks] == ["", "Hello", ", world", "", ""]
    assert chunks[0].choices[0].delta.role == "assistant"
    assert chunks[3].choices[0].finish_reason == "stop"
    assert chunks[4].choices == []
    assert chunks[4].usage is not None and chunks[4].usage.total_tokens == 7
    assert all(c.request_id == REQUEST_ID for c in chunks)
    assert stream.request_id == REQUEST_ID


def test_tool_call_fragments_arrive_typed(api, client: MatterWave) -> None:
    body = (
        chunk({"role": "assistant", "content": ""})
        + chunk({"tool_calls": [{"index": 0, "id": "call_0", "type": "function", "function": {"name": "weather"}}]})
        + chunk({"tool_calls": [{"index": 0, "function": {"arguments": "{\"city\""}}]})
        + chunk({"tool_calls": [{"index": 0, "function": {"arguments": ":\"Oslo\"}"}}]})
        + chunk({}, "tool_calls")
        + "data: [DONE]\n\n"
    )
    _stream(api, body)
    chunks = list(client.chat.completions.create(messages=PROMPT, stream=True))
    fragments = [c.choices[0].delta.tool_calls[0] for c in chunks[1:4]]
    assert fragments[0].id == "call_0" and fragments[0].function.name == "weather"
    assert "".join(f.function.arguments or "" for f in fragments) == "{\"city\":\"Oslo\"}"
    assert chunks[4].choices[0].finish_reason == "tool_calls"
    assert all(c.content == "" for c in chunks)


def test_text_yields_only_the_words(api, client: MatterWave) -> None:
    _stream(api, FRAMES)
    stream = client.chat.completions.create(messages=PROMPT, stream=True)
    assert "".join(stream.text()) == "Hello, world"


def test_the_request_asks_for_a_stream_and_usage(api, client: MatterWave) -> None:
    _stream(api, FRAMES)
    list(client.chat.completions.create(messages=PROMPT, stream=True, include_usage=True))
    sent = api.calls.last.request
    assert sent.headers["Accept"] == "text/event-stream"
    body = json.loads(sent.content)
    assert body["stream"] is True
    assert body["stream_options"] == {"include_usage": True}


def test_an_error_frame_mid_stream_is_raised_after_what_arrived_before_it(
    api, client: MatterWave
) -> None:
    _stream(api, chunk({"role": "assistant", "content": ""}) + chunk({"content": "partial"}) + ERROR_FRAME)
    stream = client.chat.completions.create(messages=PROMPT, stream=True)
    seen: list[str] = []
    with pytest.raises(APIError) as caught:
        for c in stream:
            seen.append(c.content)
    assert seen == ["", "partial"]
    assert caught.value.status_code is None
    assert caught.value.code == "upstream_error"
    assert caught.value.type == "server_error"
    assert caught.value.request_id == REQUEST_ID


def test_sse_comments_crlf_and_a_missing_final_blank_line_are_handled(
    api, client: MatterWave
) -> None:
    body = ": keep-alive\r\n\r\n" + chunk({"content": "a"}).replace("\n", "\r\n") + "data: [DONE]"
    _stream(api, body)
    stream = client.chat.completions.create(messages=PROMPT, stream=True)
    assert "".join(stream.text()) == "a"


def test_closing_early_releases_the_response(api, client: MatterWave) -> None:
    _stream(api, FRAMES)
    stream = client.chat.completions.create(messages=PROMPT, stream=True)
    with stream:
        first = next(iter(stream.text()))
    assert first == "Hello"
    assert stream._response.is_closed


@pytest.mark.asyncio
async def test_the_async_stream_yields_the_same_frames(api, async_client: AsyncMatterWave) -> None:
    _stream(api, FRAMES)
    stream = await async_client.chat.completions.create(
        messages=PROMPT, stream=True, include_usage=True
    )
    chunks = [c async for c in stream]
    assert [c.content for c in chunks] == ["", "Hello", ", world", "", ""]
    assert chunks[-1].usage is not None and chunks[-1].usage.total_tokens == 7


@pytest.mark.asyncio
async def test_the_async_text_helper_and_error_frame(api, async_client: AsyncMatterWave) -> None:
    _stream(api, FRAMES)
    stream = await async_client.chat.completions.create(messages=PROMPT, stream=True)
    assert "".join([t async for t in stream.text()]) == "Hello, world"

    _stream(api, chunk({"content": "partial"}) + ERROR_FRAME)
    stream = await async_client.chat.completions.create(messages=PROMPT, stream=True)
    with pytest.raises(APIError) as caught:
        async for _ in stream:
            pass
    assert caught.value.code == "upstream_error"
