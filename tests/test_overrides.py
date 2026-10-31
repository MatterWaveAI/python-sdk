"""Per-call overrides: `timeout=` and `max_retries=` on a call, beating the
client's own settings for that one operation; left out, the client's apply.

The timeout is read where httpx records it, on the request's extensions,
so the assertion is about what reached the transport rather than about the
client's internals."""

from __future__ import annotations

import httpx
import pytest
from conftest import completion_body

from matterwave_sdk import AsyncMatterWave, MatterWave, ServerError


def timeout_sent(route_call: object) -> dict:
    request = route_call  # respx records an httpx.Request
    return dict(request.extensions["timeout"])  # type: ignore[attr-defined]


def test_a_per_call_timeout_reaches_the_transport(api, client: MatterWave) -> None:
    route = api.post("/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=completion_body())
    )
    client.chat.completions.create(
        messages=[{"role": "user", "content": "hi"}], timeout=120.0
    )
    assert timeout_sent(route.calls.last.request)["read"] == 120.0


def test_without_a_per_call_timeout_the_clients_applies(api, client: MatterWave) -> None:
    route = api.post("/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=completion_body())
    )
    client.chat.completions.create(messages=[{"role": "user", "content": "hi"}])
    assert timeout_sent(route.calls.last.request)["read"] == 60.0


def test_a_per_call_timeout_on_the_conversation_stream(api, client: MatterWave) -> None:
    route = api.post("/api/conversations/c-1/messages").mock(
        return_value=httpx.Response(
            200,
            content=(
                b'event: delta\ndata: {"type": "delta", "content": "He"}\n\n'
                b'event: done\ndata: {"type": "done", "message": {"id": "m1",'
                b' "role": "assistant", "content": "He"}}\n\n'
            ),
            headers={"Content-Type": "text/event-stream"},
        )
    )
    stream = client.conversations.send("c-1", "Hi", timeout=600.0)
    for _ in stream:
        pass
    assert timeout_sent(route.calls.last.request)["read"] == 600.0


def test_a_per_call_max_retries_retries_where_the_client_would_not(
    api, sleeps
) -> None:
    route = api.post("/v1/chat/completions").mock(
        side_effect=[
            httpx.Response(500, json={"error": {"message": "boom"}}),
            httpx.Response(500, json={"error": {"message": "boom"}}),
            httpx.Response(200, json=completion_body()),
        ]
    )
    client = MatterWave(api_key="mw_test_key", base_url="https://matterwave.test", max_retries=0)
    completion = client.chat.completions.create(
        messages=[{"role": "user", "content": "hi"}], max_retries=2
    )
    assert completion.content == "Hello there, friend of mine."
    assert route.call_count == 3
    assert len(sleeps) == 2


def test_a_per_call_max_retries_can_refuse_the_clients_retries(api, sleeps) -> None:
    route = api.post("/v1/chat/completions").mock(
        return_value=httpx.Response(500, json={"error": {"message": "boom"}})
    )
    client = MatterWave(api_key="mw_test_key", base_url="https://matterwave.test", max_retries=2)
    with pytest.raises(ServerError):
        client.chat.completions.create(
            messages=[{"role": "user", "content": "hi"}], max_retries=0
        )
    assert route.call_count == 1


def test_without_an_override_the_clients_max_retries_apply(api, sleeps) -> None:
    route = api.post("/v1/chat/completions").mock(
        side_effect=[
            httpx.Response(500, json={"error": {"message": "boom"}}),
            httpx.Response(200, json=completion_body()),
        ]
    )
    client = MatterWave(api_key="mw_test_key", base_url="https://matterwave.test", max_retries=1)
    client.chat.completions.create(messages=[{"role": "user", "content": "hi"}])
    assert route.call_count == 2


@pytest.mark.asyncio
async def test_the_async_client_overrides_the_same_way(api, async_client: AsyncMatterWave) -> None:
    route = api.post("/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=completion_body())
    )
    await async_client.chat.completions.create(
        messages=[{"role": "user", "content": "hi"}], timeout=300.0, max_retries=5
    )
    assert timeout_sent(route.calls.last.request)["read"] == 300.0


def test_an_httpx_timeout_object_is_accepted(api, client: MatterWave) -> None:
    route = api.post("/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=completion_body())
    )
    client.chat.completions.create(
        messages=[{"role": "user", "content": "hi"}], timeout=httpx.Timeout(30.0, connect=5.0)
    )
    sent = timeout_sent(route.calls.last.request)
    assert sent["read"] == 30.0
    assert sent["connect"] == 5.0
