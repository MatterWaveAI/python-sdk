"""429 and 5xx are retried with backoff; Retry-After wins; the budget 429
and every 4xx are not retried; the retry count is a ceiling."""

from __future__ import annotations

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
    route = api.post("/v1/chat/completions").mock(
        side_effect=[
            httpx.Response(502, json=UPSTREAM),
            httpx.Response(503, text="bad gateway"),
            httpx.Response(200, json=completion_body()),
        ]
    )
    _ask(client)
    assert route.call_count == 3
    # Jittered: between half and all of 0.5s, then of 1s.
    assert 0.25 <= sleeps[0] <= 0.5
    assert 0.5 <= sleeps[1] <= 1.0


def test_retries_stop_at_max_retries_and_raise_the_last_answer(api, client, sleeps) -> None:
    route = api.post("/v1/chat/completions").mock(
        return_value=httpx.Response(502, json=UPSTREAM, headers={"X-Request-Id": REQUEST_ID})
    )
    with pytest.raises(ServerError) as caught:
        _ask(client)
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
    route = api.post("/v1/chat/completions").mock(return_value=httpx.Response(502, json=UPSTREAM))
    with pytest.raises(ServerError):
        _ask(client)
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
    route = api.post("/v1/chat/completions").mock(
        side_effect=[
            httpx.Response(429, json=RATE_LIMITED, headers={"Retry-After": "2"}),
            httpx.Response(502, json=UPSTREAM),
            httpx.Response(200, json=completion_body()),
        ]
    )
    completion = await async_client.chat.completions.create(
        messages=[{"role": "user", "content": "hi"}]
    )
    assert completion.content == "Hello there, friend of mine."
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
