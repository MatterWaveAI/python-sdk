"""The contract the app's own routes hold that the `/v1` tests do not
cover: a 429 in FastAPI's `detail` shape carries the window and the
request id like one in the envelope, `pages()` ends on a server that
still answers the pre-paging shape, and a rename the server refuses is
an error that names the field rather than a crash on a 422 body."""

from __future__ import annotations

import httpx
import pytest
from conftest import BASE_URL, REQUEST_ID
from test_conversations import CONV, conversation

from matterwave_sdk import APIError, AsyncMatterWave, MatterWave, RateLimit, RateLimitError

REFUSED = {
    "detail": "Rate limit of 3 requests per minute exceeded for this API key.",
    "retry_after_s": 42,
    "request_id": REQUEST_ID,
}
WINDOW = {
    "Retry-After": "42",
    "X-RateLimit-Limit": "3",
    "X-RateLimit-Remaining": "0",
    "X-RateLimit-Reset": "42",
}


def test_the_apps_own_429_carries_the_window_and_the_request_id(api) -> None:
    api.get("/api/conversations").mock(
        return_value=httpx.Response(429, json=REFUSED, headers=WINDOW)
    )
    client = MatterWave(api_key="k", base_url=BASE_URL, max_retries=0)
    with pytest.raises(RateLimitError) as caught:
        client.conversations.list()
    err = caught.value
    assert err.rate_limit == RateLimit(limit=3, remaining=0, reset=42)
    assert err.retry_after == 42.0
    assert err.request_id == REQUEST_ID
    assert err.message == REFUSED["detail"]
    assert err.code is None


@pytest.mark.asyncio
async def test_the_async_client_reads_the_same_window(api) -> None:
    api.patch(f"/api/conversations/{CONV}").mock(
        return_value=httpx.Response(429, json=REFUSED, headers=WINDOW)
    )
    client = AsyncMatterWave(api_key="k", base_url=BASE_URL, max_retries=0)
    with pytest.raises(RateLimitError) as caught:
        await client.conversations.update(CONV, title="Renamed")
    assert caught.value.rate_limit == RateLimit(limit=3, remaining=0, reset=42)


def test_pages_ends_on_a_server_that_answers_the_old_shape(api, client: MatterWave) -> None:
    listed = api.get("/api/conversations").mock(
        return_value=httpx.Response(200, json={"data": [conversation()], "filter": "default"})
    )
    pages = list(client.conversations.pages(limit=1))
    assert len(pages) == 1 and listed.call_count == 1
    assert pages[0].next_cursor is None and [c.id for c in pages[0]] == [CONV]


@pytest.mark.asyncio
async def test_the_async_pages_end_the_same_way(api, async_client: AsyncMatterWave) -> None:
    listed = api.get("/api/conversations").mock(
        return_value=httpx.Response(200, json={"data": [], "filter": "archived"})
    )
    pages = [page async for page in async_client.conversations.pages(filter="archived")]
    assert len(pages) == 1 and listed.call_count == 1
    assert pages[0].filter == "archived" and len(pages[0]) == 0


def test_a_rename_the_server_refuses_names_the_field(api, client: MatterWave) -> None:
    api.patch(f"/api/conversations/{CONV}").mock(
        return_value=httpx.Response(
            422,
            json={
                "detail": [
                    {
                        "loc": ["body", "title"],
                        "msg": "Value error, title must be at most 200 characters",
                        "type": "value_error",
                    }
                ],
                "request_id": REQUEST_ID,
            },
        )
    )
    with pytest.raises(APIError) as caught:
        client.conversations.update(CONV, title="x" * 201)
    assert caught.value.status_code == 422
    assert "200 characters" in caught.value.message
    assert caught.value.request_id == REQUEST_ID
