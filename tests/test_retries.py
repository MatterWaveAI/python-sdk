"""429 and 5xx are retried with backoff; Retry-After wins; the budget 429
and every 4xx are not retried; the retry count is a ceiling. A write (POST,
PATCH) is retried on a 429 and on a connection that was never made, but not
on a 5xx or a failure after it was sent: the first copy may have landed."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime

import httpx
import pytest
from conftest import BASE_URL, REQUEST_ID, chunk, completion_body, error_body

from matterwave_sdk import (
    APIConnectionError,
    APIError,
    APITimeoutError,
    AsyncMatterWave,
    MatterWave,
    RateLimitError,
    ServerError,
)
from matterwave_sdk._base import BACKOFF_BASE_S, BACKOFF_CAP_S, backoff_s, retry_after_of

RATE_LIMITED = error_body("Rate limit exceeded.", "rate_limit_error", "rate_limit_exceeded")
BUDGET = error_body("Budget used up.", "insufficient_quota", "insufficient_quota")
UPSTREAM = error_body("gateway failed", "server_error", "upstream_error")


ME = {"subject": "u-1", "kind": "key", "roles": [], "org_id": "o-1"}


def _ask(client: MatterWave):
    return client.chat.completions.create(messages=[{"role": "user", "content": "hi"}])


def test_a_429_with_retry_after_waits_exactly_that_long_then_succeeds(
    api, client, sleeps
) -> None:
    route = api.post("/v1/chat/completions").mock(
        side_effect=[
            httpx.Response(429, json=RATE_LIMITED, headers={"Retry-After": "3"}),
            httpx.Response(200, json=completion_body()),
        ]
    )
    assert _ask(client).content == "Hello there, friend of mine."
    assert route.call_count == 2
    assert sleeps == [3.0]


def test_a_5xx_is_retried_with_exponential_backoff(api, client, sleeps) -> None:
    route = api.get("/api/me").mock(
        side_effect=[
            httpx.Response(502, json=UPSTREAM),
            httpx.Response(503, text="bad gateway"),
            httpx.Response(200, json=ME),
        ]
    )
    client.me()
    assert route.call_count == 3
    # Jittered: between half and all of 0.5s, then of 1s.
    assert 0.25 <= sleeps[0] <= 0.5
    assert 0.5 <= sleeps[1] <= 1.0


def test_retries_stop_at_max_retries_and_raise_the_last_answer(api, client, sleeps) -> None:
    route = api.get("/api/me").mock(
        return_value=httpx.Response(502, json=UPSTREAM, headers={"X-Request-Id": REQUEST_ID})
    )
    with pytest.raises(ServerError) as caught:
        client.me()
    assert route.call_count == 3  # the call plus max_retries=2
    assert len(sleeps) == 2
    assert caught.value.request_id == REQUEST_ID


def test_the_budget_429_is_not_retried(api, client, sleeps) -> None:
    route = api.post("/v1/chat/completions").mock(return_value=httpx.Response(429, json=BUDGET))
    with pytest.raises(RateLimitError) as caught:
        _ask(client)
    assert route.call_count == 1
    assert sleeps == []
    assert caught.value.code == "insufficient_quota"


def test_a_4xx_is_not_retried(api, client, sleeps) -> None:
    route = api.post("/v1/chat/completions").mock(
        return_value=httpx.Response(
            400, json=error_body("bad", "invalid_request_error", "invalid_value")
        )
    )
    with pytest.raises(APIError):
        _ask(client)
    assert route.call_count == 1
    assert sleeps == []


def test_max_retries_zero_sends_once(api, sleeps) -> None:
    client = MatterWave(api_key="k", base_url=BASE_URL, max_retries=0)
    route = api.get("/api/me").mock(return_value=httpx.Response(502, json=UPSTREAM))
    with pytest.raises(ServerError):
        client.me()
    assert route.call_count == 1
    assert sleeps == []


def test_a_connection_error_is_retried_then_raised(api, client, sleeps) -> None:
    route = api.post("/v1/chat/completions").mock(side_effect=httpx.ConnectError("refused"))
    with pytest.raises(APIConnectionError, match="could not reach"):
        _ask(client)
    assert route.call_count == 3
    assert len(sleeps) == 2


def test_a_timeout_is_its_own_error(api, client, sleeps) -> None:
    api.post("/v1/chat/completions").mock(side_effect=httpx.ReadTimeout("slow"))
    with pytest.raises(APITimeoutError):
        _ask(client)


def test_a_streamed_429_is_retried_before_the_stream_starts(api, client, sleeps) -> None:
    frames = (
        chunk({"role": "assistant", "content": ""})
        + chunk({"content": "ok"})
        + chunk({}, "stop")
        + "data: [DONE]\n\n"
    )
    route = api.post("/v1/chat/completions").mock(
        side_effect=[
            httpx.Response(429, json=RATE_LIMITED, headers={"Retry-After": "1"}),
            httpx.Response(200, headers={"Content-Type": "text/event-stream"}, content=frames),
        ]
    )
    stream = client.chat.completions.create(
        messages=[{"role": "user", "content": "hi"}], stream=True
    )
    assert "".join(stream.text()) == "ok"
    assert route.call_count == 2
    assert sleeps == [1.0]


@pytest.mark.asyncio
async def test_the_async_client_retries_the_same_way(
    api, async_client: AsyncMatterWave, sleeps
) -> None:
    route = api.get("/api/me").mock(
        side_effect=[
            httpx.Response(429, json=RATE_LIMITED, headers={"Retry-After": "2"}),
            httpx.Response(502, json=UPSTREAM),
            httpx.Response(200, json=ME),
        ]
    )
    me = await async_client.me()
    assert me.subject == "u-1"
    assert route.call_count == 3
    assert sleeps[0] == 2.0
    assert 0.5 <= sleeps[1] <= 1.0


def test_backoff_honours_retry_after_and_caps_the_exponent() -> None:
    assert backoff_s(0, 42.0) == 42.0
    assert BACKOFF_BASE_S * 0.5 <= backoff_s(0, None) <= BACKOFF_BASE_S
    assert backoff_s(20, None) <= BACKOFF_CAP_S


def test_retry_after_as_an_http_date_is_converted_to_seconds() -> None:
    when = datetime.now(timezone.utc) + timedelta(seconds=30)
    response = httpx.Response(429, headers={"Retry-After": format_datetime(when, usegmt=True)})
    seconds = retry_after_of(response)
    assert seconds is not None
    assert 25 <= seconds <= 30


# --- writes ------------------------------------------------------------------


def test_a_5xx_on_a_post_is_raised_without_sending_it_again(api, client, sleeps) -> None:
    route = api.post("/v1/chat/completions").mock(
        return_value=httpx.Response(502, json=UPSTREAM, headers={"X-Request-Id": REQUEST_ID})
    )
    with pytest.raises(ServerError) as caught:
        _ask(client)
    assert route.call_count == 1
    assert sleeps == []
    assert caught.value.request_id == REQUEST_ID


def test_a_5xx_on_a_patch_is_not_retried(api, client, sleeps) -> None:
    route = api.patch("/api/keys/k-1").mock(return_value=httpx.Response(503, json=UPSTREAM))
    with pytest.raises(ServerError):
        client.keys.update("k-1", name="renamed")
    assert route.call_count == 1


@pytest.mark.parametrize(
    "error",
    [httpx.ConnectError("refused"), httpx.ConnectTimeout("no answer"), httpx.PoolTimeout("busy")],
    ids=["connect", "connect-timeout", "pool-timeout"],
)
def test_a_post_that_never_left_the_client_is_retried(api, client, sleeps, error) -> None:
    route = api.post("/api/conversations/c-1/report").mock(
        side_effect=[error, httpx.Response(201, json={"id": "r-1", "status": "filed"})]
    )
    client.conversations.report("c-1", "unhelpful")
    assert route.call_count == 2
    assert len(sleeps) == 1


@pytest.mark.parametrize(
    "error",
    [
        httpx.ReadTimeout("slow"),
        httpx.ReadError("reset"),
        httpx.WriteError("broken pipe"),
        httpx.RemoteProtocolError("closed mid-reply"),
    ],
    ids=["read-timeout", "read-error", "write-error", "protocol-error"],
)
def test_a_post_that_may_have_landed_is_not_sent_again(api, client, sleeps, error) -> None:
    route = api.post("/api/keys").mock(side_effect=error)
    with pytest.raises(APIConnectionError):
        client.keys.create("ci")
    assert route.call_count == 1
    assert sleeps == []


def test_a_read_after_a_dropped_reply_is_retried(api, client, sleeps) -> None:
    route = api.get("/api/me").mock(
        side_effect=[httpx.ReadError("reset"), httpx.Response(200, json=ME)]
    )
    client.me()
    assert route.call_count == 2


def test_a_put_and_a_delete_are_retried_on_a_5xx(api, client, sleeps) -> None:
    conversation = {"id": "c-1", "title": "t", "messages": []}
    pin = api.put("/api/conversations/c-1/pin").mock(
        side_effect=[httpx.Response(502, json=UPSTREAM), httpx.Response(200, json=conversation)]
    )
    revoke = api.delete("/api/keys/k-1").mock(
        side_effect=[httpx.Response(503, json=UPSTREAM), httpx.Response(204)]
    )
    client.conversations.pin("c-1")
    client.keys.revoke("k-1")
    assert pin.call_count == 2
    assert revoke.call_count == 2


def test_a_conversation_send_is_not_resent_after_a_5xx(api, client, sleeps) -> None:
    route = api.post("/api/conversations/c-1/messages").mock(
        return_value=httpx.Response(502, json={"detail": "bad gateway"})
    )
    with pytest.raises(ServerError):
        client.conversations.send("c-1", "Hi")
    assert route.call_count == 1


def test_an_upload_is_not_resent_after_a_5xx(api, client, sleeps) -> None:
    import io

    route = api.post("/api/attachments").mock(
        return_value=httpx.Response(503, json={"detail": "busy"})
    )
    with pytest.raises(ServerError):
        client.attachments.upload(io.BytesIO(b"hello"), "notes.txt")
    assert route.call_count == 1


def _rpc(answers: dict[str, list[int]]):
    """A `/mcp` stand-in: each JSON-RPC method answers with the statuses
    queued for it, in order, and the methods sent are recorded."""
    sent: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        sent.append(body["method"])
        status = answers[body["method"]].pop(0)
        if status != 200:
            return httpx.Response(status, json=UPSTREAM)
        result = {"tools": []} if body["method"] == "tools/list" else {}
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": result})

    return handler, sent


def test_a_read_only_mcp_call_retries_like_a_read(api, client, sleeps) -> None:
    handler, sent = _rpc({"initialize": [502, 200], "tools/list": [503, 200]})
    api.post("/mcp").mock(side_effect=handler)
    client.mcp.list_tools()
    assert sent == ["initialize", "initialize", "tools/list", "tools/list"]


def test_an_mcp_tool_call_is_not_resent_after_a_5xx(api, client, sleeps) -> None:
    handler, sent = _rpc({"initialize": [200], "tools/call": [502, 200]})
    api.post("/mcp").mock(side_effect=handler)
    with pytest.raises(ServerError):
        client.mcp.call_tool("send_message", {"conversation_id": "c-1", "content": "hi"})
    assert sent == ["initialize", "tools/call"]


@pytest.mark.asyncio
async def test_the_async_client_holds_writes_back_the_same_way(
    api, async_client: AsyncMatterWave, sleeps
) -> None:
    post = api.post("/v1/chat/completions").mock(return_value=httpx.Response(502, json=UPSTREAM))
    keys = api.post("/api/keys").mock(
        side_effect=[httpx.ConnectError("refused"), httpx.ReadError("reset")]
    )
    with pytest.raises(ServerError):
        await async_client.chat.completions.create(messages=[{"role": "user", "content": "hi"}])
    with pytest.raises(APIConnectionError):
        await async_client.keys.create("ci")
    assert post.call_count == 1
    assert keys.call_count == 2


# --- the longest wait ----------------------------------------------------------


def test_a_retry_after_beyond_the_maximum_raises_at_once(api, client, sleeps) -> None:
    route = api.post("/v1/chat/completions").mock(
        side_effect=[
            httpx.Response(429, json=RATE_LIMITED, headers={"Retry-After": "3600"}),
            httpx.Response(200, json=completion_body()),
        ]
    )
    with pytest.raises(RateLimitError) as caught:
        _ask(client)
    assert caught.value.retry_after == 3600.0
    assert route.call_count == 1
    assert sleeps == []


def test_a_wait_at_the_maximum_is_still_taken(api, client, sleeps) -> None:
    route = api.post("/v1/chat/completions").mock(
        side_effect=[
            httpx.Response(429, json=RATE_LIMITED, headers={"Retry-After": "60"}),
            httpx.Response(200, json=completion_body()),
        ]
    )
    _ask(client)
    assert route.call_count == 2
    assert sleeps == [60.0]


def test_max_retry_wait_moves_the_line_for_a_5xx_too(api, sleeps) -> None:
    client = MatterWave(api_key="k", base_url=BASE_URL, max_retry_wait=2.0)
    route = api.get("/api/me").mock(
        side_effect=[
            httpx.Response(503, json=UPSTREAM, headers={"Retry-After": "5"}),
            httpx.Response(200, json=ME),
        ]
    )
    with pytest.raises(ServerError) as caught:
        client.me()
    assert caught.value.retry_after == 5.0
    assert route.call_count == 1


def test_a_negative_maximum_is_refused() -> None:
    with pytest.raises(ValueError, match="max_retry_wait"):
        MatterWave(api_key="k", base_url=BASE_URL, max_retry_wait=-1)


@pytest.mark.asyncio
async def test_the_async_client_caps_the_wait_the_same_way(api, sleeps) -> None:
    client = AsyncMatterWave(api_key="k", base_url=BASE_URL)
    route = api.post("/v1/chat/completions").mock(
        return_value=httpx.Response(429, json=RATE_LIMITED, headers={"Retry-After": "61"})
    )
    with pytest.raises(RateLimitError):
        await client.chat.completions.create(messages=[{"role": "user", "content": "hi"}])
    assert route.call_count == 1
    assert sleeps == []
