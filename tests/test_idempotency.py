"""Every write carries an `Idempotency-Key`: one per call, the same on each
retry of it, never on a read; `with_options(idempotency_key=...)` supplies
the caller's own, and a header the caller set by hand is left alone."""

from __future__ import annotations

import io
import uuid

import httpx
import pytest
from conftest import BASE_URL, completion_body, error_body

from matterwave_sdk import AsyncMatterWave, MatterWave

RATE_LIMITED = error_body("Rate limit exceeded.", "rate_limit_error", "rate_limit_exceeded")
ME = {"subject": "u-1", "kind": "key", "roles": [], "org_id": "o-1"}
KEY = {
    "id": "k-1",
    "name": "ci",
    "prefix": "mw_ab",
    "scopes": [],
    "created_at": "2026-10-31T00:00:00Z",
}


def key_of(call) -> str | None:
    return call.request.headers.get("idempotency-key")


def _ask(client: MatterWave) -> None:
    client.chat.completions.create(messages=[{"role": "user", "content": "hi"}])


def test_a_post_carries_a_fresh_uuid(api, client: MatterWave) -> None:
    route = api.post("/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=completion_body())
    )
    _ask(client)
    _ask(client)
    first, second = (key_of(call) for call in route.calls)
    assert first is not None and second is not None
    assert uuid.UUID(first).version == 4
    assert first != second


def test_every_retry_of_a_write_reuses_its_key(api, client: MatterWave, sleeps) -> None:
    route = api.post("/v1/chat/completions").mock(
        side_effect=[
            httpx.Response(429, json=RATE_LIMITED),
            httpx.ConnectError("refused"),
            httpx.Response(200, json=completion_body()),
        ]
    )
    _ask(client)
    keys = {key_of(call) for call in route.calls}
    assert route.call_count == 3
    assert len(keys) == 1 and None not in keys


def test_a_read_carries_none(api, client: MatterWave) -> None:
    route = api.get("/api/me").mock(return_value=httpx.Response(200, json=ME))
    client.me()
    assert key_of(route.calls.last) is None


@pytest.mark.parametrize("method", ["put", "patch", "delete"])
def test_put_patch_and_delete_are_writes_too(api, client: MatterWave, method: str) -> None:
    conversation = {"id": "c-1", "title": "t", "messages": []}
    routes = {
        "put": (api.put("/api/conversations/c-1/pin"), lambda: client.conversations.pin("c-1")),
        "patch": (
            api.patch("/api/conversations/c-1"),
            lambda: client.conversations.update("c-1", title="t"),
        ),
        "delete": (
            api.delete("/api/conversations/c-1"),
            lambda: client.conversations.delete("c-1"),
        ),
    }
    route, call = routes[method]
    route.mock(return_value=httpx.Response(200, json=conversation | {"status": "deleted"}))
    call()
    assert key_of(route.calls.last) is not None


def test_a_streamed_turn_and_an_upload_carry_one(api, client: MatterWave) -> None:
    send = api.post("/api/conversations/c-1/messages").mock(
        return_value=httpx.Response(
            200,
            content=b'event: done\ndata: {"type": "done", "message": {"id": "m1"}}\n\n',
            headers={"Content-Type": "text/event-stream"},
        )
    )
    upload = api.post("/api/attachments").mock(
        return_value=httpx.Response(
            201,
            json={
                "id": "a-1",
                "filename": "notes.txt",
                "content_type": "text/plain",
                "size_bytes": 5,
                "scan_status": "clean",
                "created_at": "2026-10-31T00:00:00Z",
            },
        )
    )
    for _ in client.conversations.send("c-1", "Hi"):
        pass
    client.attachments.upload(io.BytesIO(b"hello"), "notes.txt")
    assert key_of(send.calls.last) is not None
    assert key_of(upload.calls.last) is not None


def test_with_options_supplies_the_callers_key_on_every_attempt(
    api, client: MatterWave, sleeps
) -> None:
    route = api.post("/api/keys").mock(
        side_effect=[httpx.Response(429, json=RATE_LIMITED), httpx.Response(201, json=KEY)]
    )
    client.with_options(idempotency_key="job-42").keys.create("ci")
    assert [key_of(call) for call in route.calls] == ["job-42", "job-42"]


def test_an_explicit_idempotency_key_lifts_the_write_retry_restriction(
    api, client: MatterWave, sleeps
) -> None:
    route = api.post("/api/keys").mock(
        side_effect=[
            httpx.Response(502, json=error_body("bad gateway", "server_error", "upstream_error")),
            httpx.ReadError("reset mid-reply"),
            httpx.Response(201, json=KEY),
        ]
    )
    created = client.with_options(idempotency_key="job-99").keys.create("ci")
    assert created.id == "k-1"
    assert [key_of(call) for call in route.calls] == ["job-99", "job-99", "job-99"]
    assert len(sleeps) == 2


def test_the_callers_key_is_for_writes_only(api, client: MatterWave) -> None:
    route = api.get("/api/me").mock(return_value=httpx.Response(200, json=ME))
    client.with_options(idempotency_key="job-42").me()
    assert key_of(route.calls.last) is None


def test_a_header_set_by_hand_is_left_alone(api) -> None:
    client = MatterWave(
        api_key="k", base_url=BASE_URL, default_headers={"idempotency-key": "from-defaults"}
    )
    route = api.post("/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=completion_body())
    )
    _ask(client)
    assert route.calls.last.request.headers.get_list("idempotency-key") == ["from-defaults"]


def test_an_empty_key_is_refused(client: MatterWave) -> None:
    with pytest.raises(ValueError, match="idempotency_key"):
        client.with_options(idempotency_key=" ")


@pytest.mark.asyncio
async def test_the_async_client_keys_writes_the_same_way(
    api, async_client: AsyncMatterWave, sleeps
) -> None:
    route = api.post("/v1/chat/completions").mock(
        side_effect=[
            httpx.Response(429, json=RATE_LIMITED),
            httpx.Response(200, json=completion_body()),
            httpx.Response(200, json=completion_body()),
        ]
    )
    await async_client.chat.completions.create(messages=[{"role": "user", "content": "hi"}])
    first, second = (key_of(call) for call in route.calls)
    assert first is not None and first == second
    view = async_client.with_options(idempotency_key="job-7")
    await view.chat.completions.create(messages=[{"role": "user", "content": "hi"}])
    assert key_of(route.calls.last) == "job-7"
