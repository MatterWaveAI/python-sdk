"""Raw response access: `client.last_response` keeps the status and headers
the parsed models leave behind, on a refusal as well as on a success."""

from __future__ import annotations

import httpx
import pytest
from conftest import REQUEST_ID, completion_body, error_body

from matterwave_sdk import AsyncMatterWave, MatterWave, RateLimitError

RATE_LIMITED = error_body("Rate limit exceeded.", "rate_limit_error", "rate_limit_exceeded")


def test_a_429s_headers_and_window_survive_the_parsed_error(
    api, client: MatterWave, sleeps
) -> None:
    api.post("/v1/chat/completions").mock(
        return_value=httpx.Response(
            429,
            json=RATE_LIMITED,
            headers={
                "X-Request-Id": REQUEST_ID,
                "X-RateLimit-Limit": "60",
                "X-RateLimit-Remaining": "0",
                "X-RateLimit-Reset": "12",
                "Retry-After": "30",
            },
        )
    )
    with pytest.raises(RateLimitError):
        client.chat.completions.create(messages=[{"role": "user", "content": "hi"}])
    last = client.last_response
    assert last is not None
    assert last.status_code == 429
    assert last.request_id == REQUEST_ID
    assert last.rate_limit is not None
    assert (last.rate_limit.limit, last.rate_limit.remaining, last.rate_limit.reset) == (60, 0, 12)
    assert last.retry_after == 30.0
    assert last.header("x-ratelimit-remaining") == "0"
    assert last.header("X-RateLimit-Reset") == "12"
    assert sleeps == [30.0, 30.0]


def test_a_successs_request_id_is_reachable(api, client: MatterWave) -> None:
    api.get("/api/me").mock(
        return_value=httpx.Response(
            200,
            json={"subject": "u-1", "kind": "key", "roles": [], "org_id": "o-1"},
            headers={"X-Request-Id": REQUEST_ID},
        )
    )
    client.me()
    assert client.last_response is not None
    assert client.last_response.status_code == 200
    assert client.last_response.request_id == REQUEST_ID


def test_the_last_response_starts_as_none(client: MatterWave) -> None:
    assert client.last_response is None


def test_a_retried_call_leaves_the_final_response(api, sleeps) -> None:
    api.post("/v1/chat/completions").mock(
        side_effect=[
            httpx.Response(
                429,
                json={"error": {"message": "slow down"}},
                headers={"X-Request-Id": "req-first"},
            ),
            httpx.Response(200, json=completion_body(), headers={"X-Request-Id": "req-final"}),
        ]
    )
    client = MatterWave(api_key="mw_test_key", base_url="https://matterwave.test")
    client.chat.completions.create(messages=[{"role": "user", "content": "hi"}])
    assert client.last_response is not None
    assert client.last_response.status_code == 200
    assert client.last_response.request_id == "req-final"


@pytest.mark.asyncio
async def test_the_async_client_keeps_the_same_record(
    api, async_client: AsyncMatterWave, sleeps
) -> None:
    api.post("/v1/chat/completions").mock(
        return_value=httpx.Response(
            429,
            json=RATE_LIMITED,
            headers={
                "X-Request-Id": REQUEST_ID,
                "X-RateLimit-Limit": "60",
                "X-RateLimit-Remaining": "0",
                "X-RateLimit-Reset": "12",
            },
        )
    )
    with pytest.raises(RateLimitError):
        await async_client.chat.completions.create(messages=[{"role": "user", "content": "hi"}])
    last = async_client.last_response
    assert last is not None
    assert last.status_code == 429
    assert last.request_id == REQUEST_ID
    assert last.rate_limit is not None and last.rate_limit.remaining == 0
