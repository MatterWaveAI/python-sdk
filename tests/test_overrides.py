"""Per-call overrides: `timeout=` and `max_retries=` on a call, beating the
client's own settings for that one operation; left out, the client's apply.

The timeout is read where httpx records it, on the request's extensions,
so the assertion is about what reached the transport rather than about the
client's internals."""

from __future__ import annotations

import httpx
import pytest
from conftest import completion_body, error_body

from matterwave_sdk import AsyncMatterWave, MatterWave, MatterWaveError, ServerError

RATE_LIMITED = error_body("Rate limit exceeded.", "rate_limit_error", "rate_limit_exceeded")


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
            httpx.Response(429, json=RATE_LIMITED),
            httpx.Response(429, json=RATE_LIMITED),
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
            httpx.Response(429, json=RATE_LIMITED),
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


# --- with_options: every method ------------------------------------------------


def test_with_options_gives_any_method_its_own_timeout(api, client: MatterWave) -> None:
    keys = api.get("/api/keys").mock(return_value=httpx.Response(200, json={"data": []}))
    model = api.get("/v1/models/grt-nano").mock(
        return_value=httpx.Response(200, json={"id": "grt-nano", "object": "model"})
    )
    client.with_options(timeout=300.0).keys.list()
    client.with_options(timeout=5.0).models.retrieve("grt-nano")
    assert timeout_sent(keys.calls.last.request)["read"] == 300.0
    assert timeout_sent(model.calls.last.request)["read"] == 5.0


def test_with_options_leaves_the_client_as_it_was(api, client: MatterWave) -> None:
    route = api.get("/api/keys").mock(return_value=httpx.Response(200, json={"data": []}))
    client.with_options(timeout=300.0, default_headers={"X-Trace": "t-1"})
    client.keys.list()
    assert timeout_sent(route.calls.last.request)["read"] == 60.0
    assert "x-trace" not in route.calls.last.request.headers


def test_with_options_retries_any_method_its_own_way(api, sleeps) -> None:
    route = api.get("/api/keys").mock(
        side_effect=[
            httpx.Response(503, json={"detail": "busy"}),
            httpx.Response(503, json={"detail": "busy"}),
            httpx.Response(200, json={"data": []}),
            httpx.Response(503, json={"detail": "busy"}),
        ]
    )
    client = MatterWave(api_key="mw_test_key", base_url="https://matterwave.test", max_retries=0)
    client.with_options(max_retries=2).keys.list()
    assert route.call_count == 3
    with pytest.raises(ServerError):
        client.keys.list()


def test_with_options_caps_the_wait_its_own_way(api, client: MatterWave, sleeps) -> None:
    route = api.get("/api/keys").mock(
        side_effect=[
            httpx.Response(429, json=RATE_LIMITED, headers={"Retry-After": "120"}),
            httpx.Response(200, json={"data": []}),
        ]
    )
    client.with_options(max_retry_wait=300).keys.list()
    assert route.call_count == 2
    assert sleeps == [120.0]


def test_with_options_adds_to_the_default_headers(api) -> None:
    client = MatterWave(
        api_key="mw_test_key",
        base_url="https://matterwave.test",
        default_headers={"X-Client": "cli/1"},
    )
    route = api.get("/api/me").mock(
        return_value=httpx.Response(
            200, json={"subject": "u-1", "kind": "key", "roles": [], "org_id": "o-1"}
        )
    )
    client.with_options(default_headers={"X-Trace": "t-1"}).me()
    sent = route.calls.last.request.headers
    assert sent["x-client"] == "cli/1"
    assert sent["x-trace"] == "t-1"


def test_with_options_shares_the_pool_and_ends_with_the_client(api) -> None:
    client = MatterWave(api_key="mw_test_key", base_url="https://matterwave.test")
    view = client.with_options(timeout=1.0)
    assert view._http is client._http
    view.close()
    assert not client._http.is_closed
    client.close()
    with pytest.raises(MatterWaveError, match="closed client"):
        client.with_options(timeout=1.0).keys.list()


def test_with_options_refuses_a_bad_setting(client: MatterWave) -> None:
    with pytest.raises(ValueError, match="max_retries"):
        client.with_options(max_retries=-1)
    with pytest.raises(ValueError, match="max_retry_wait"):
        client.with_options(max_retry_wait=-1)


@pytest.mark.asyncio
async def test_the_async_client_has_with_options_too(api, async_client: AsyncMatterWave) -> None:
    route = api.get("/api/keys").mock(return_value=httpx.Response(200, json={"data": []}))
    await async_client.with_options(timeout=300.0).keys.list()
    assert timeout_sent(route.calls.last.request)["read"] == 300.0
