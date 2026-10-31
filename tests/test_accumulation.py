"""Accumulation and item-level pagination: `stream.final_text()` for the
whole answer, `conversations.items()` for every conversation of every page,
and the client's `on_request` / `on_response` hooks."""

from __future__ import annotations

import json

import httpx
import pytest
from conftest import chunk

from matterwave_sdk import AsyncMatterWave, MatterWave, RawResponse


def _stream_body(*texts: str) -> bytes:
    frames = [chunk({"role": "assistant", "content": ""})]
    for text in texts:
        frames.append(chunk({"content": text}))
    frames.append(chunk(None, finish="stop"))
    frames.append(b"data: [DONE]\n\n")
    return b"".join(f.encode() if isinstance(f, str) else f for f in frames)


def _message(text: str) -> dict:
    return {
        "id": "m1",
        "conversation_id": "c-1",
        "role": "assistant",
        "parts": [{"type": "text", "text": text}],
        "content": text,
        "content_version": 2,
        "created_at": "2026-10-18T09:00:00+00:00",
        "parent_id": None,
        "model": None,
        "truncated": False,
        "finish_reason": None,
    }


def _turn_body(*texts: str) -> bytes:
    events: list[bytes] = []
    for text in texts:
        events.append(
            f'event: delta\ndata: {json.dumps({"type": "delta", "content": text})}\n\n'.encode()
        )
    events.append(
        f'event: done\ndata: {json.dumps(_message("".join(texts)))}\n\n'.encode()
    )
    return b"".join(events)


def test_the_v1_stream_accumulates_to_final_text(api, client: MatterWave) -> None:
    api.post("/v1/chat/completions").mock(
        return_value=httpx.Response(
            200, content=_stream_body("Hello", " there", "!")
        )
    )
    stream = client.chat.completions.create(
        messages=[{"role": "user", "content": "hi"}], stream=True
    )
    assert stream.final_text() == "Hello there!"


def test_the_conversation_stream_accumulates_to_final_text(api, client: MatterWave) -> None:
    api.post("/api/conversations/c-1/messages").mock(
        return_value=httpx.Response(200, content=_turn_body("He", "llo"))
    )
    stream = client.conversations.send("c-1", "Hi")
    assert stream.final_text() == "Hello"
    assert stream.result is not None and stream.result.content == "Hello"


@pytest.mark.asyncio
async def test_the_async_streams_accumulate_the_same_way(api, async_client: AsyncMatterWave) -> None:
    api.post("/v1/chat/completions").mock(
        return_value=httpx.Response(
            200, content=_stream_body("Good", "bye")
        )
    )
    api.post("/api/conversations/c-1/messages").mock(
        return_value=httpx.Response(200, content=_turn_body("Good", "bye"))
    )
    completion_stream = await async_client.chat.completions.create(
        messages=[{"role": "user", "content": "hi"}], stream=True
    )
    assert await completion_stream.final_text() == "Goodbye"
    turn = await async_client.conversations.send("c-1", "Hi")
    assert await turn.final_text() == "Goodbye"


def _conversation_page(conversation_ids: list[str], next_cursor: str | None) -> dict:
    return {
        "data": [
            {"id": cid, "title": f"Conversation {cid}", "filter": "default"} for cid in conversation_ids
        ],
        "filter": "default",
        **({"next_cursor": next_cursor} if next_cursor else {}),
    }


def test_items_walks_every_page_one_conversation_at_a_time(api, client: MatterWave) -> None:
    api.get("/api/conversations").mock(
        side_effect=[
            httpx.Response(200, json=_conversation_page(["c-1", "c-2"], "cursor-1")),
            httpx.Response(200, json=_conversation_page(["c-3"], None)),
        ]
    )
    items = [c.id for c in client.conversations.items()]
    assert items == ["c-1", "c-2", "c-3"]


@pytest.mark.asyncio
async def test_the_async_items_walk_the_same_way(api, async_client: AsyncMatterWave) -> None:
    api.get("/api/conversations").mock(
        side_effect=[
            httpx.Response(200, json=_conversation_page(["c-1"], "cursor-1")),
            httpx.Response(200, json=_conversation_page(["c-2"], None)),
        ]
    )
    items = [c.id async for c in async_client.conversations.items()]
    assert items == ["c-1", "c-2"]


def test_the_hooks_see_every_request_and_response(api, sleeps) -> None:
    seen_requests: list[str] = []
    seen_responses: list[RawResponse] = []
    client = MatterWave(
        api_key="mw_test_key",
        base_url="https://matterwave.test",
        on_request=lambda request: seen_requests.append(f"{request.method} {request.path}"),
        on_response=seen_responses.append,
    )
    api.post("/v1/chat/completions").mock(
        side_effect=[
            httpx.Response(500, json={"error": {"message": "boom"}}),
            httpx.Response(
                200,
                json={
                    "id": "chatcmpl-01",
                    "object": "chat.completion",
                    "created": 1790621573,
                    "model": "grt-nano",
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": "ok"},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                },
                headers={"X-Request-Id": "req-final"},
            ),
        ]
    )
    client.chat.completions.create(messages=[{"role": "user", "content": "hi"}])
    assert seen_requests == ["POST /v1/chat/completions", "POST /v1/chat/completions"]
    assert [r.status_code for r in seen_responses] == [500, 200]
    assert seen_responses[-1].request_id == "req-final"


def test_the_hooks_fire_once_without_a_retry(api) -> None:
    seen_requests: list[str] = []
    client = MatterWave(
        api_key="mw_test_key",
        base_url="https://matterwave.test",
        on_request=lambda request: seen_requests.append(request.path),
    )
    api.get("/api/me").mock(
        return_value=httpx.Response(
            200, json={"subject": "u-1", "kind": "key", "roles": [], "org_id": "o-1"}
        )
    )
    client.me()
    assert seen_requests == ["/api/me"]


@pytest.mark.asyncio
async def test_the_async_hooks_fire_the_same_way(api) -> None:
    seen_requests: list[str] = []
    seen_responses: list[RawResponse] = []
    client = AsyncMatterWave(
        api_key="mw_test_key",
        base_url="https://matterwave.test",
        on_request=lambda request: seen_requests.append(f"{request.method} {request.path}"),
        on_response=seen_responses.append,
    )
    api.get("/api/me").mock(
        return_value=httpx.Response(
            200, json={"subject": "u-1", "kind": "key", "roles": [], "org_id": "o-1"}
        )
    )
    await client.me()
    assert seen_requests == ["GET /api/me"]
    assert [r.status_code for r in seen_responses] == [200]
