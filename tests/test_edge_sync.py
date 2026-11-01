"""The edge pairing and sync resource (`client.sync`, with `pairings`)
against the fake API: the admin half that mints codes and unpairs boxes,
and the machine half a paired box pushes with. Each test answers with the
body the route in matterwave-app sends and checks the request the client
made.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import httpx
import pytest
from conftest import API_KEY, REQUEST_ID

from matterwave_sdk import (
    APP_OPERATIONS,
    AsyncMatterWave,
    MatterWave,
    MatterWaveError,
    NotFoundError,
    PairedKey,
    SyncOutcome,
)

KEY_ID = "key-edge-1"
H = {"X-Request-Id": REQUEST_ID}

CONVERSATION = {
    "id": "c0ffee00-0000-4000-8000-000000000001",
    "title": "Mirror notes",
    "created_at": "2026-10-31T09:00:00+00:00",
    "updated_at": "2026-10-31T09:30:00+00:00",
    "messages": [
        {
            "id": "aabbccdd-0000-4000-8000-000000000001",
            "conversation_id": "c0ffee00-0000-4000-8000-000000000001",
            "role": "user",
            "parts": [{"type": "text", "text": "hello"}],
            "created_at": "2026-10-31T09:00:01+00:00",
        }
    ],
}


def sent(route) -> httpx.Request:
    return route.calls.last.request


def body_of(route) -> dict:
    return json.loads(sent(route).content)


def _family_operations() -> dict[str, tuple[str, str]]:
    return {k: v for k, v in APP_OPERATIONS.items() if k.split(".")[0] == "sync"}


def test_every_sync_operation_names_a_method_on_both_clients() -> None:
    for client in (
        MatterWave(api_key="k", base_url="https://matterwave.test"),
        AsyncMatterWave(api_key="k", base_url="https://matterwave.test"),
    ):
        for dotted in _family_operations():
            target: object = client
            for part in dotted.split("."):
                assert hasattr(target, part), f"{type(client).__name__} has no {dotted}"
                target = getattr(target, part)
            assert callable(target)


def _api_routes() -> set[tuple[str, str]]:
    found: set[tuple[str, str]] = set()
    for prefix_width in (2, 3, 4):
        base = Path(__file__).resolve().parents[prefix_width]
        for candidate in (
            base / "matterwave-app" / "api" / "matterwave",
            base / "api" / "matterwave",
        ):
            if candidate.is_dir():
                for source in candidate.glob("*.py"):
                    text = source.read_text(encoding="utf-8")
                    prefixes = dict(
                        re.findall(r'(\w+)\s*=\s*APIRouter\([^)]*prefix="([^"]*)"', text)
                    )
                    for router, method, path in re.findall(
                        r'@(\w+)\.(get|post|put|patch|delete)\(\s*"([^"]*)"', text
                    ):
                        found.add((method.upper(), prefixes.get(router, "") + path))
    return found


def test_every_sync_operation_is_a_route_the_api_declares() -> None:
    routes = _api_routes()
    if not routes:
        pytest.skip("matterwave-app/api/matterwave is not beside the SDK in this checkout")
    for key, (method, path) in _family_operations().items():
        assert (method, path) in routes, f"{key}: {method} {path} is not an API route"


# --- the admin half -------------------------------------------------------------------


def pairing_body(**extra) -> dict:
    return {
        "code": "ABCD-EFGH-JKLM",
        "name": "Workshop box",
        "expires_in": 600,
        "expires_at": "2026-10-31T09:10:00+00:00",
        **extra,
    }


def device_body(**extra) -> dict:
    return {
        "id": KEY_ID,
        "prefix": "mw_ed12",
        "name": "Edge: Workshop box",
        "box_name": "Workshop box",
        "kind": "device",
        "scopes": ["conversations:import"],
        "created_at": "2026-10-31T09:00:00+00:00",
        "expires_at": None,
        "revoked_at": None,
        "last_used_at": "2026-10-31T09:29:00+00:00",
        **extra,
    }


def test_mint_answers_a_code_that_lives_ten_minutes(api, client: MatterWave) -> None:
    route = api.post("/api/sync/pairings").respond(201, json=pairing_body(), headers=H)
    code = client.sync.pairings.mint("  Workshop box ")
    # Trimmed, control characters gone, interior spacing kept -- the route's
    # own cleaning, mirrored here.
    assert body_of(route) == {"name": "Workshop box"}
    assert code.code == "ABCD-EFGH-JKLM"
    assert code.expires_in == 600
    assert route.called
    with pytest.raises(ValueError, match="name"):
        client.sync.pairings.mint("   ")


def test_devices_list_carries_the_box_name_and_revoked_rows(
    api, client: MatterWave
) -> None:
    route = api.get("/api/sync/devices").respond(
        200,
        json={
            "data": [
                device_body(),
                device_body(id="key-edge-2", box_name="Old box",
                            revoked_at="2026-10-30T09:00:00+00:00"),
            ]
        },
        headers=H,
    )
    devices = client.sync.pairings.devices()
    assert sent(route).headers["authorization"] == f"Bearer {API_KEY}"
    assert [d.box_name for d in devices] == ["Workshop box", "Old box"]
    assert devices.data[0].scopes == ["conversations:import"]
    assert devices.data[1].revoked_at is not None  # the record of an unpairing


def test_unpair_refuses_what_is_not_an_edge_key(api, client: MatterWave) -> None:
    route = api.delete(f"/api/sync/devices/{KEY_ID}").respond(204)
    assert client.sync.pairings.unpair(KEY_ID) is None
    assert route.called
    api.delete(f"/api/sync/devices/{KEY_ID}").respond(
        404, json={"detail": "No such edge box."}, headers=H
    )
    with pytest.raises(NotFoundError):
        client.sync.pairings.unpair(KEY_ID)
    with pytest.raises(ValueError, match="key_id"):
        client.sync.pairings.unpair("../../keys")


# --- the box half ------------------------------------------------------------------------


def granted_body(**extra) -> dict:
    return {
        "key": "mw_ed12_secret",
        "key_id": KEY_ID,
        "name": "Workshop box",
        "scopes": ["conversations:import"],
        **extra,
    }


def test_pair_is_anonymous_and_shows_the_key_once(api, client: MatterWave) -> None:
    route = api.post("/api/sync/pair").respond(200, json=granted_body(), headers=H)
    granted = client.sync.pair("abcd-efgh-jklm", device_id="dev-123")
    request = sent(route)
    assert "authorization" not in request.headers
    assert body_of(route) == {"code": "abcd-efgh-jklm", "device_id": "dev-123"}
    assert isinstance(granted, PairedKey)
    assert granted.key == "mw_ed12_secret"
    assert "mw_ed12_secret" not in repr(granted)
    with pytest.raises(ValueError, match="code"):
        client.sync.pair("")
    # Unknown, expired and already used are one answer, so a guess learns nothing.
    api.post("/api/sync/pair").respond(
        404, json={"detail": "That pairing code is not valid, or it has expired."}, headers=H
    )
    with pytest.raises(NotFoundError):
        client.sync.pair("zzzz-zzzz-zzzz")


def test_status_on_a_paired_box_and_on_a_platform_pod(api, client: MatterWave) -> None:
    route = api.get("/api/sync/status").respond(
        200,
        json={
            "box": True,
            "paired": True,
            "device_id": "dev-123",
            "platform_url": "https://app.matterwave.test",
            "name": "Workshop box",
            "paired_at": "2026-10-31T09:00:00+00:00",
            "last_synced_at": "2026-10-31T09:30:00+00:00",
            "last_attempt_at": "2026-10-31T09:30:00+00:00",
            "offline": False,
            "pending": 3,
            "pending_messages": 3,
            "pending_capped": False,
            "stopped": None,
            "last_error": None,
            "skipped": 0,
            "skipped_recent": 0,
            "next_attempt_at": None,
        },
        headers=H,
    )
    status = client.sync.status()
    assert status.box and status.paired
    assert status.device_id == "dev-123"
    assert status.pending == 3 and status.pending_capped is False
    assert status.last_error is None
    api.get("/api/sync/status").respond(200, json={"box": False, "paired": False}, headers=H)
    pod = client.sync.status()
    assert pod.box is False and pod.device_id is None
    assert route.called


def test_push_upserts_by_id_and_reports_rejections(api, client: MatterWave) -> None:
    route = api.post("/api/sync/conversations").respond(
        200,
        json={
            "conversation_id": CONVERSATION["id"],
            "accepted": 1,
            "updated": 0,
            "already_present": 0,
            "rejected": [],
        },
        headers=H,
    )
    outcome = client.sync.push(CONVERSATION, device_id="dev-123")
    assert isinstance(outcome, SyncOutcome)
    request = sent(route)
    assert request.headers["authorization"] == f"Bearer {API_KEY}"
    sent_body = body_of(route)
    assert sent_body["format_version"] == 1
    assert sent_body["source"] == "edge"
    assert sent_body["device_id"] == "dev-123"
    assert sent_body["conversation"] == CONVERSATION
    assert outcome.accepted == 1 and outcome.rejected == []
    # 200 with the counts is the answer even when some messages were refused.
    api.post("/api/sync/conversations").respond(
        200,
        json={
            "conversation_id": CONVERSATION["id"],
            "accepted": 0,
            "updated": 1,
            "already_present": 1,
            "rejected": [{"id": "aabbccdd-0000-4000-8000-000000000001", "status": 409}],
        },
        headers=H,
    )
    mixed = client.sync.push(CONVERSATION)
    assert mixed.updated == 1 and mixed.already_present == 1
    assert mixed.rejected[0]["status"] == 409


def test_push_refuses_a_wrong_version_or_an_overfull_conversation(
    api, client: MatterWave
) -> None:
    route = api.post("/api/sync/conversations").respond(
        200,
        json={"conversation_id": "x", "accepted": 0, "updated": 0, "already_present": 0,
              "rejected": []},
        headers=H,
    )
    with pytest.raises(ValueError, match="format_version"):
        client.sync.push(CONVERSATION, format_version=2)
    with pytest.raises(ValueError, match="messages"):
        client.sync.push(
            {**CONVERSATION, "messages": [{"id": str(i)} for i in range(501)]}
        )
    with pytest.raises(ValueError, match="conversation"):
        client.sync.push({})
    assert route.call_count == 0
    # The conversation id not being the caller's to write is the 409/410 pair.
    api.post("/api/sync/conversations").respond(
        409, json={"detail": "A conversation with this id exists under another account."},
        headers=H,
    )
    with pytest.raises(MatterWaveError, match="another account"):
        client.sync.push(CONVERSATION)
    api.post("/api/sync/conversations").respond(
        410, json={"detail": "This conversation was deleted on the platform."}, headers=H
    )
    with pytest.raises(MatterWaveError, match="deleted"):
        client.sync.push(CONVERSATION)


# --- async ----------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_async_sync_makes_the_same_requests(api, async_client: AsyncMatterWave) -> None:
    api.post("/api/sync/pairings").respond(201, json=pairing_body(), headers=H)
    api.get("/api/sync/devices").respond(200, json={"data": [device_body()]}, headers=H)
    api.delete(f"/api/sync/devices/{KEY_ID}").respond(204)
    api.post("/api/sync/pair").respond(200, json=granted_body(), headers=H)
    api.get("/api/sync/status").respond(
        200, json={"box": True, "paired": True, "device_id": "dev-123"}, headers=H
    )
    api.post("/api/sync/conversations").respond(
        200,
        json={
            "conversation_id": CONVERSATION["id"],
            "accepted": 1,
            "updated": 0,
            "already_present": 0,
            "rejected": [],
        },
        headers=H,
    )
    async with async_client as c:
        assert (await c.sync.pairings.mint("Workshop box")).code == "ABCD-EFGH-JKLM"
        devices = await c.sync.pairings.devices()
        assert devices.data[0].box_name == "Workshop box"
        assert await c.sync.pairings.unpair(KEY_ID) is None
        granted = await c.sync.pair("abcd-efgh-jklm")
        assert granted.key == "mw_ed12_secret"
        assert (await c.sync.status()).paired is True
        outcome = await c.sync.push(CONVERSATION, device_id="dev-123")
        assert outcome.conversation_id == CONVERSATION["id"]
        assert outcome.accepted == 1
