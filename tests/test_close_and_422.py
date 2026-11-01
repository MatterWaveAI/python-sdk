"""The 422 refusal as its own class, and `close()` refusing anything
further: a request sent after close raises, and an in-flight conversation
turn whose body dies with the closed transport is refused at its resume
rather than retried against a dead client."""

from __future__ import annotations

import httpx
import pytest
from conftest import completion_body

from matterwave_sdk import (
    AsyncMatterWave,
    MatterWave,
    MatterWaveError,
    UnprocessableEntityError,
)


def _fastapi_422() -> httpx.Response:
    return httpx.Response(
        422,
        json={
            "detail": [
                {"loc": ["body", "url"], "msg": "The URL does not resolve.", "type": "value_error"}
            ]
        },
        headers={"X-Request-Id": "req-422"},
    )


def test_a_422_is_its_own_error_with_the_fields_joined(api, client: MatterWave) -> None:
    api.post("/api/org/webhooks").mock(return_value=_fastapi_422())
    with pytest.raises(UnprocessableEntityError) as excinfo:
        client.webhooks.create(url="https://nowhere.test", events=["ping"])
    error = excinfo.value
    assert error.status_code == 422
    assert error.request_id == "req-422"
    assert "The URL does not resolve." in str(error)
    assert isinstance(error.body, dict)


def test_a_422_on_the_v1_surface_is_the_same_class(api, client: MatterWave) -> None:
    api.post("/v1/chat/completions").mock(return_value=_fastapi_422())
    with pytest.raises(UnprocessableEntityError):
        client.chat.completions.create(messages=[{"role": "user", "content": "hi"}])


@pytest.mark.asyncio
async def test_the_async_422_is_the_same_class(api, async_client: AsyncMatterWave) -> None:
    api.post("/v1/chat/completions").mock(return_value=_fastapi_422())
    with pytest.raises(UnprocessableEntityError):
        await async_client.chat.completions.create(messages=[{"role": "user", "content": "hi"}])


def test_a_call_after_close_is_refused() -> None:
    client = MatterWave(api_key="mw_test_key", base_url="https://matterwave.test")
    client.close()
    with pytest.raises(MatterWaveError, match="closed client"):
        client.me()


def test_close_is_idempotent() -> None:
    client = MatterWave(api_key="mw_test_key", base_url="https://matterwave.test")
    client.close()
    client.close()


def test_an_inflight_turn_is_refused_at_its_resume_after_close(api, sleeps) -> None:
    client = MatterWave(api_key="mw_test_key", base_url="https://matterwave.test")

    def body():
        # One frame with an id, so the stream has a resume cursor, then the
        # transport dies -- which is what closing the client does to a real
        # connection.
        yield b'event: delta\nid: 1\ndata: {"type": "delta", "content": "He"}\n\n'
        if client._http.is_closed:
            raise httpx.ReadError("the transport was closed under the stream")
        raise AssertionError("the test forgot to close the client")

    api.post("/api/conversations/c-1/messages").mock(
        return_value=httpx.Response(200, content=body())
    )
    stream = client.conversations.send("c-1", "Hi")
    frames = iter(stream)
    assert next(frames).content == "He"
    client.close()
    with pytest.raises(MatterWaveError, match="closed client"):
        next(frames)


@pytest.mark.asyncio
async def test_the_async_inflight_turn_is_refused_the_same_way(api, sleeps) -> None:
    client = AsyncMatterWave(api_key="mw_test_key", base_url="https://matterwave.test")

    async def body():
        yield b'event: delta\nid: 1\ndata: {"type": "delta", "content": "He"}\n\n'
        if client._http.is_closed:
            raise httpx.ReadError("the transport was closed under the stream")
        raise AssertionError("the test forgot to close the client")

    api.post("/api/conversations/c-1/messages").mock(
        return_value=httpx.Response(200, content=body())
    )
    stream = await client.conversations.send("c-1", "Hi")
    frames = stream.__aiter__()
    first = await frames.__anext__()
    assert first.content == "He"
    await client.close()
    with pytest.raises(MatterWaveError, match="closed client"):
        await frames.__anext__()


def test_a_borrowed_transport_is_left_alone_by_close() -> None:
    http_client = httpx.Client()
    client = MatterWave(
        api_key="mw_test_key", base_url="https://matterwave.test", http_client=http_client
    )
    client.close()
    assert not http_client.is_closed
    assert client._closed
    http_client.close()


def test_an_ordinary_success_still_works_before_any_close(api) -> None:
    api.post("/v1/chat/completions").mock(return_value=httpx.Response(200, json=completion_body()))
    client = MatterWave(api_key="mw_test_key", base_url="https://matterwave.test")
    assert client.chat.completions.create(messages=[{"role": "user", "content": "hi"}]).content
    client.close()
