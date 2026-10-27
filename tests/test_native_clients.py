"""The native API clients against the fake API: keys, usage, webhooks,
attachments, library and shares. Each test answers with the body the
route in api/matterwave sends and checks the request the client made
(method, path, query, body) and what it parsed out of the answer.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest
from conftest import API_KEY, REQUEST_ID

from matterwave_sdk import (
    APP_OPERATIONS,
    APIError,
    AsyncMatterWave,
    MatterWave,
    NotFoundError,
    PermissionDeniedError,
)

ROUTES = Path(__file__).resolve().parents[3] / "api" / "matterwave"
NATIVE = ("keys", "usage", "webhooks", "attachments", "library", "shares", "budget")

KEY_ID = "key-0001"
ENDPOINT_ID = "whe-0001"
DELIVERY_ID = "whd-0001"
ATTACHMENT_ID = "att-0001"
CONV = "c0ffee00-0000-4000-8000-000000000001"
H = {"X-Request-Id": REQUEST_ID}


def sent(route) -> httpx.Request:
    return route.calls.last.request


def body_of(route) -> dict:
    return json.loads(sent(route).content)


# --- the route table ----------------------------------------------------------


def _native_operations() -> dict[str, tuple[str, str]]:
    return {k: v for k, v in APP_OPERATIONS.items() if k.split(".")[0] in NATIVE}


def test_every_native_operation_names_a_method_on_both_clients() -> None:
    for client in (
        MatterWave(api_key="k", base_url="https://matterwave.test"),
        AsyncMatterWave(api_key="k", base_url="https://matterwave.test"),
    ):
        for dotted in _native_operations():
            target: object = client
            for part in dotted.split("."):
                assert hasattr(target, part), f"{type(client).__name__} has no {dotted}"
                target = getattr(target, part)
            assert callable(target)


def _api_routes() -> set[tuple[str, str]]:
    """Every (method, path) the app's routers declare, read from the source
    so the check needs none of the app's dependencies."""
    found: set[tuple[str, str]] = set()
    for source in ROUTES.glob("routes_*.py"):
        text = source.read_text(encoding="utf-8")
        prefixes = dict(re.findall(r'(\w+)\s*=\s*APIRouter\([^)]*prefix="([^"]*)"', text))
        for router, method, path in re.findall(
            r'@(\w+)\.(get|post|put|patch|delete)\(\s*"([^"]*)"', text
        ):
            found.add((method.upper(), prefixes.get(router, "") + path))
    return found


def test_every_native_operation_is_a_route_the_api_declares() -> None:
    if not ROUTES.is_dir():
        pytest.skip("api/matterwave is not beside the SDK in this checkout")
    routes = _api_routes()
    for key, (method, path) in _native_operations().items():
        assert (method, path) in routes, f"{key}: {method} {path} is not an API route"


# --- keys ---------------------------------------------------------------------


def key_body(**extra) -> dict:
    return {
        "id": KEY_ID,
        "prefix": "mw_ab12",
        "name": "deploy",
        "kind": "api",
        "scopes": ["chat:read", "chat:write"],
        "created_at": "2026-10-18T09:00:00+00:00",
        "expires_at": None,
        "revoked_at": None,
        "last_used_at": None,
        "rotated_at": None,
        "previous_prefix": None,
        "previous_valid_until": None,
        "allowed_cidrs": None,
        "rpm_limit": None,
        **extra,
    }


def test_keys_list_parses_rows_and_never_a_secret(api, client: MatterWave) -> None:
    route = api.get("/api/keys").respond(200, json={"data": [key_body()]}, headers=H)
    keys = client.keys.list()
    assert sent(route).headers["authorization"] == f"Bearer {API_KEY}"
    assert len(keys) == 1
    assert keys.data[0].prefix == "mw_ab12"
    assert keys.data[0].key is None
    assert keys.request_id == REQUEST_ID


def test_keys_create_sends_only_what_was_passed_and_returns_the_secret_once(
    api, client: MatterWave
) -> None:
    route = api.post("/api/keys").respond(
        201, json=key_body(key="mw_ab12_secret", rpm_limit=30), headers=H
    )
    key = client.keys.create(
        "deploy",
        scopes=["chat:read"],
        expires_at=datetime(2027, 1, 1, tzinfo=timezone.utc),
        rpm_limit=30,
    )
    assert body_of(route) == {
        "name": "deploy",
        "scopes": ["chat:read"],
        "expires_at": "2027-01-01T00:00:00+00:00",
        "rpm_limit": 30,
    }
    assert key.key == "mw_ab12_secret"
    assert "mw_ab12_secret" not in repr(key)


def test_keys_create_refuses_a_naive_expiry_and_both_expiries() -> None:
    client = MatterWave(api_key="k", base_url="https://matterwave.test")
    with pytest.raises(ValueError, match="timezone"):
        client.keys.create("deploy", expires_at=datetime(2027, 1, 1))  # noqa: DTZ001
    with pytest.raises(ValueError, match="not both"):
        client.keys.create("deploy", expires_in_days=30, expires_at="2027-01-01T00:00:00Z")


def test_keys_update_sends_null_to_clear_and_leaves_out_what_was_not_passed(
    api, client: MatterWave
) -> None:
    route = api.patch(f"/api/keys/{KEY_ID}").respond(
        200, json=key_body(name="renamed", allowed_cidrs=["203.0.113.0/24"]), headers=H
    )
    key = client.keys.update(KEY_ID, name="renamed", allowed_cidrs=["203.0.113.0/24"], expires_at=None)
    assert body_of(route) == {
        "name": "renamed",
        "allowed_cidrs": ["203.0.113.0/24"],
        "expires_at": None,
    }
    assert key.allowed_cidrs == ["203.0.113.0/24"]


def test_keys_update_with_nothing_to_change_is_refused_before_the_request() -> None:
    client = MatterWave(api_key="k", base_url="https://matterwave.test")
    with pytest.raises(ValueError, match="update needs"):
        client.keys.update(KEY_ID)


def test_keys_rotate_returns_the_new_secret_and_the_grace_window(api, client: MatterWave) -> None:
    route = api.post(f"/api/keys/{KEY_ID}/rotate").respond(
        200,
        json=key_body(
            prefix="mw_cd34",
            key="mw_cd34_secret",
            previous_prefix="mw_ab12",
            previous_valid_until="2026-10-19T09:00:00+00:00",
        ),
        headers=H,
    )
    key = client.keys.rotate(KEY_ID)
    assert sent(route).content == b""
    assert (key.key, key.previous_prefix) == ("mw_cd34_secret", "mw_ab12")
    assert key.previous_valid_until == "2026-10-19T09:00:00+00:00"


def test_keys_revoke_is_a_delete_answered_204(api, client: MatterWave) -> None:
    route = api.delete(f"/api/keys/{KEY_ID}").respond(204)
    assert client.keys.revoke(KEY_ID) is None
    assert route.called


def test_keys_revoke_of_a_missing_key_is_not_found(api, client: MatterWave) -> None:
    api.delete(f"/api/keys/{KEY_ID}").respond(404, json={"detail": "No such API key"}, headers=H)
    with pytest.raises(NotFoundError, match="No such API key") as caught:
        client.keys.revoke(KEY_ID)
    assert caught.value.request_id == REQUEST_ID


def test_keys_refuse_an_id_that_would_change_the_path() -> None:
    client = MatterWave(api_key="k", base_url="https://matterwave.test")
    with pytest.raises(ValueError, match="key_id"):
        client.keys.rotate("../usage")


# --- usage --------------------------------------------------------------------


BALANCE = {
    "unit": "tokens",
    "used": 850,
    "included": 1000,
    "remaining": 150,
    "percent": 85.0,
    "warn": True,
    "in_overage": False,
    "exhausted": False,
    "period_start": "2026-10-01",
    "resets_at": "2026-11-01T00:00:00+00:00",
    "plan": {"code": "team", "name": "Team"},
    "seats": None,
    "overage": {"enabled": False, "spent_micros": 0, "cap_micros": 0},
    "models": [
        {
            "model": "grt-nano",
            "label": "Nano",
            "multiplier": 1.5,
            "prompt_tokens": 400,
            "completion_tokens": 167,
            "tokens": 567,
            "weighted_tokens": 850,
            "cost_micros": 1200,
            "overage_micros": 0,
            "requests": 12,
            "compaction_weighted_tokens": 30,
            "compaction_requests": 1,
        }
    ],
    "compaction": {"weighted_tokens": 30, "requests": 1},
    "history": [
        {
            "day": "2026-10-18",
            "weighted_tokens": 850,
            "tokens": 567,
            "cost_micros": 1200,
            "overage_micros": 0,
            "requests": 12,
            "compaction_weighted_tokens": 30,
            "compaction_requests": 1,
        }
    ],
}

ORG_USAGE = {
    "period": "2026-10",
    "period_start": "2026-10-01",
    "period_end": "2026-11-01",
    "unit": "tokens",
    "scope": "org",
    "data": [
        {
            "subject": "user-1",
            "email": "a@example.test",
            "name": "A",
            "weighted_tokens": 850,
            "cost_micros": 1200,
            "turns": 11,
            "tokens": 567,
            "last_active": "2026-10-18T09:00:00+00:00",
        },
        {
            "subject": "unattributed",
            "email": None,
            "name": None,
            "weighted_tokens": 0,
            "cost_micros": 0,
            "turns": 0,
            "tokens": 0,
            "last_active": None,
        },
    ],
    "totals": {"weighted_tokens": 850, "cost_micros": 1200, "turns": 11, "tokens": 567},
}

CSV = (
    "subject,email,name,weighted_tokens,tokens,cost_usd,turns,last_active\r\n"
    "user-1,a@example.test,A,850,567,0.001200,11,2026-10-18T09:00:00+00:00\r\n"
)


def test_usage_get_is_the_balance_with_models_and_history(api, client: MatterWave) -> None:
    api.get("/api/me/usage").respond(200, json=BALANCE, headers=H)
    balance = client.usage.get()
    assert (balance.used, balance.included, balance.percent) == (850, 1000, 85.0)
    assert balance.warn and not balance.exhausted
    assert balance.plan == {"code": "team", "name": "Team"}
    assert balance.seats is None
    assert balance.models[0].multiplier == 1.5
    assert balance.history[0].day == "2026-10-18"
    assert balance.request_id == REQUEST_ID


def test_usage_org_passes_the_period_and_parses_members(api, client: MatterWave) -> None:
    route = api.get("/api/org/usage").respond(200, json=ORG_USAGE, headers=H)
    report = client.usage.org(period="2026-10")
    assert sent(route).url.params["period"] == "2026-10"
    assert report.scope == "org"
    assert [m.subject for m in report] == ["user-1", "unattributed"]
    assert report.data[1].email is None
    assert report.totals.cost_micros == 1200


def test_usage_org_without_a_period_sends_none(api, client: MatterWave) -> None:
    route = api.get("/api/org/usage").respond(200, json=ORG_USAGE, headers=H)
    client.usage.org()
    assert "period" not in sent(route).url.params


def test_usage_export_csv_returns_the_text_the_server_wrote(api, client: MatterWave) -> None:
    route = api.get("/api/org/usage.csv").respond(
        200,
        text=CSV,
        headers={
            "Content-Type": "text/csv; charset=utf-8",
            "Content-Disposition": 'attachment; filename="usage-2026-10.csv"',
            **H,
        },
    )
    assert client.usage.export_csv(period="2026-10") == CSV
    assert sent(route).headers["accept"] == "text/csv"
    assert sent(route).url.params["period"] == "2026-10"


def test_usage_a_malformed_period_is_the_servers_422(api, client: MatterWave) -> None:
    api.get("/api/org/usage.csv").respond(
        422, json={"detail": "period must look like 2026-10"}, headers=H
    )
    with pytest.raises(APIError, match="period must look like 2026-10"):
        client.usage.export_csv(period="October")


# --- webhooks -----------------------------------------------------------------


def endpoint_body(**extra) -> dict:
    return {
        "id": ENDPOINT_ID,
        "url": "https://hooks.example.test/mw",
        "description": "",
        "events": ["message.completed"],
        "enabled": True,
        "failure_count": 0,
        "created_at": "2026-10-18T09:00:00+00:00",
        "updated_at": "2026-10-18T09:00:00+00:00",
        "disabled_at": None,
        **extra,
    }


def delivery_body(**extra) -> dict:
    return {
        "id": DELIVERY_ID,
        "endpoint_id": ENDPOINT_ID,
        "event": "ping",
        "event_id": "evt-1",
        "payload": {"type": "ping"},
        "attempt": 1,
        "status": "delivered",
        "response_code": 200,
        "response_ms": 41,
        "response_excerpt": "ok",
        "next_attempt_at": None,
        "delivered_at": "2026-10-18T09:00:01+00:00",
        "last_error": None,
        "created_at": "2026-10-18T09:00:00+00:00",
        **extra,
    }


def test_webhooks_list_carries_the_event_vocabulary(api, client: MatterWave) -> None:
    api.get("/api/org/webhooks").respond(
        200,
        json={"data": [endpoint_body()], "events": ["message.completed", "member.joined"]},
        headers=H,
    )
    listed = client.webhooks.list()
    assert listed.events == ["message.completed", "member.joined"]
    assert listed.data[0].secret is None


def test_webhooks_create_returns_the_secret_once(api, client: MatterWave) -> None:
    route = api.post("/api/org/webhooks").respond(
        201,
        json=endpoint_body(
            secret="whsec_x",
            shown_once=True,
            verification={"python": "...", "node": "..."},
        ),
        headers=H,
    )
    endpoint = client.webhooks.create(
        "https://hooks.example.test/mw", ["message.completed"], description="prod"
    )
    assert body_of(route) == {
        "url": "https://hooks.example.test/mw",
        "events": ["message.completed"],
        "description": "prod",
    }
    assert endpoint.secret == "whsec_x"
    assert endpoint.verification == {"python": "...", "node": "..."}
    assert "whsec_x" not in repr(endpoint)


def test_webhooks_get_update_delete(api, client: MatterWave) -> None:
    api.get(f"/api/org/webhooks/{ENDPOINT_ID}").respond(200, json=endpoint_body(), headers=H)
    patch = api.patch(f"/api/org/webhooks/{ENDPOINT_ID}").respond(
        200, json=endpoint_body(enabled=False), headers=H
    )
    delete = api.delete(f"/api/org/webhooks/{ENDPOINT_ID}").respond(204)
    assert client.webhooks.get(ENDPOINT_ID).url == "https://hooks.example.test/mw"
    assert client.webhooks.update(ENDPOINT_ID, enabled=False).enabled is False
    assert body_of(patch) == {"enabled": False}
    assert client.webhooks.delete(ENDPOINT_ID) is None
    assert delete.called


def test_webhooks_rotate_secret_and_test(api, client: MatterWave) -> None:
    api.post(f"/api/org/webhooks/{ENDPOINT_ID}/rotate").respond(
        200, json=endpoint_body(secret="whsec_y", shown_once=True, verification={}), headers=H
    )
    api.post(f"/api/org/webhooks/{ENDPOINT_ID}/test").respond(200, json=delivery_body(), headers=H)
    assert client.webhooks.rotate_secret(ENDPOINT_ID).secret == "whsec_y"
    delivery = client.webhooks.test(ENDPOINT_ID)
    assert (delivery.event, delivery.status, delivery.response_code) == ("ping", "delivered", 200)


def test_webhooks_deliveries_filter_and_redeliver(api, client: MatterWave) -> None:
    route = api.get(f"/api/org/webhooks/{ENDPOINT_ID}/deliveries").respond(
        200, json={"data": [delivery_body(status="failed")], "status": "failed"}, headers=H
    )
    again = api.post(f"/api/org/webhooks/{ENDPOINT_ID}/deliveries/{DELIVERY_ID}/redeliver").respond(
        202, json=delivery_body(id="whd-0002", status="pending"), headers=H
    )
    log = client.webhooks.deliveries(ENDPOINT_ID, status="failed")
    assert sent(route).url.params["status"] == "failed"
    assert (log.status, log.data[0].status) == ("failed", "failed")
    redelivered = client.webhooks.redeliver(ENDPOINT_ID, DELIVERY_ID)
    assert (redelivered.id, redelivered.status) == ("whd-0002", "pending")
    assert again.called


def test_webhooks_an_unknown_delivery_status_is_refused_before_the_request() -> None:
    client = MatterWave(api_key="k", base_url="https://matterwave.test")
    with pytest.raises(ValueError, match="status must be one of"):
        client.webhooks.deliveries(ENDPOINT_ID, status="lost")


def test_webhooks_need_an_admin(api, client: MatterWave) -> None:
    api.get("/api/org/webhooks").respond(
        403, json={"detail": "Only an owner or admin can do that."}, headers=H
    )
    with pytest.raises(PermissionDeniedError):
        client.webhooks.list()


# --- attachments --------------------------------------------------------------


def attachment_body(**extra) -> dict:
    return {
        "id": ATTACHMENT_ID,
        "filename": "notes.txt",
        "content_type": "text/plain",
        "size_bytes": 5,
        "scan_status": "clean",
        "created_at": "2026-10-18T09:00:00+00:00",
        "message_id": None,
        "extract_truncated": False,
        "download_url": f"/api/attachments/{ATTACHMENT_ID}/content?expires=1790622173&sig=abc_-",
        **extra,
    }


def test_attachments_upload_is_multipart_with_the_conversation_in_the_query(
    api, client: MatterWave
) -> None:
    route = api.post("/api/attachments").respond(201, json=attachment_body(), headers=H)
    attachment = client.attachments.upload(b"hello", "notes.txt", conversation_id=CONV)
    request = sent(route)
    assert request.headers["content-type"].startswith("multipart/form-data; boundary=")
    assert b'name="file"; filename="notes.txt"' in request.content
    assert b"hello" in request.content
    assert request.url.params["conversation_id"] == CONV
    assert attachment.scan_status == "clean"
    assert attachment.download_url is not None


def test_attachments_upload_retries_with_the_same_bytes(api, client: MatterWave, sleeps) -> None:
    import io

    route = api.post("/api/attachments")
    route.side_effect = [
        httpx.Response(503, json={"detail": "busy"}),
        httpx.Response(201, json=attachment_body(), headers=H),
    ]
    client.attachments.upload(io.BytesIO(b"hello"), "notes.txt")
    assert route.call_count == 2
    assert b"hello" in route.calls[1].request.content


def test_attachments_get_delete_limits(api, client: MatterWave) -> None:
    api.get(f"/api/attachments/{ATTACHMENT_ID}").respond(200, json=attachment_body(), headers=H)
    delete = api.delete(f"/api/attachments/{ATTACHMENT_ID}").respond(
        200, json={"status": "deleted", "blob_removed_by": "sweeper"}, headers=H
    )
    api.get("/api/attachments/limits/me").respond(
        200,
        json={
            "max_upload_bytes": 10,
            "quota_bytes": 100,
            "quota_used_bytes": 40,
            "quota_remaining_bytes": 60,
        },
        headers=H,
    )
    assert client.attachments.get(ATTACHMENT_ID).filename == "notes.txt"
    assert client.attachments.delete(ATTACHMENT_ID) is None
    assert delete.called
    assert client.attachments.limits().quota_remaining_bytes == 60


def test_attachments_download_follows_the_signed_url_with_the_key(api, client: MatterWave) -> None:
    route = api.get(f"/api/attachments/{ATTACHMENT_ID}/content").respond(
        200, content=b"hello", headers={"Content-Type": "text/plain"}
    )
    data = client.attachments.download(
        f"/api/attachments/{ATTACHMENT_ID}/content?expires=1790622173&sig=abc_-"
    )
    assert data == b"hello"
    request = sent(route)
    assert request.url.params["expires"] == "1790622173"
    assert request.url.params["sig"] == "abc_-"
    assert request.headers["authorization"] == f"Bearer {API_KEY}"


def test_attachments_download_refuses_a_url_that_is_not_a_download() -> None:
    client = MatterWave(api_key="k", base_url="https://matterwave.test")
    with pytest.raises(ValueError, match="not an attachment download URL"):
        client.attachments.download("/api/keys")


# --- library ------------------------------------------------------------------


FOLDER = {
    "id": "fld-1",
    "name": "Work",
    "position": 0,
    "created_at": "2026-10-18T09:00:00+00:00",
    "updated_at": "2026-10-18T09:00:00+00:00",
}
TAG = {"id": "tag-1", "name": "urgent", "created_at": "2026-10-18T09:00:00+00:00"}
CONVERSATION = {
    "id": CONV,
    "title": "New chat",
    "created_at": "2026-10-18T09:00:00+00:00",
    "updated_at": "2026-10-18T09:00:00+00:00",
    "folder_id": "fld-1",
}


def test_library_get(api, client: MatterWave) -> None:
    api.get("/api/library").respond(
        200,
        json={"folders": [FOLDER], "tags": [TAG], "assignments": {CONV: ["tag-1"]}},
        headers=H,
    )
    library = client.library.get()
    assert library.folders[0].name == "Work"
    assert library.tags[0].name == "urgent"
    assert library.assignments == {CONV: ["tag-1"]}


def test_library_search_pages_by_cursor(api, client: MatterWave) -> None:
    route = api.get("/api/search").respond(
        200,
        json={
            "data": [{**CONVERSATION, "snippet": "<b>hi</b>", "message_id": "m-1"}],
            "query": "hi",
            "next_cursor": "c2",
        },
        headers=H,
    )
    page = client.library.search("hi", cursor="c1", limit=5)
    assert dict(sent(route).url.params) == {"q": "hi", "cursor": "c1", "limit": "5"}
    assert page.next_cursor == "c2"
    assert page.data[0].message_id == "m-1"


def test_library_folders(api, client: MatterWave) -> None:
    create = api.post("/api/library/folders").respond(201, json=FOLDER, headers=H)
    update = api.patch("/api/library/folders/fld-1").respond(
        200, json={**FOLDER, "position": 2}, headers=H
    )
    delete = api.delete("/api/library/folders/fld-1").respond(204)
    assert client.library.create_folder("Work").id == "fld-1"
    assert body_of(create) == {"name": "Work"}
    assert client.library.update_folder("fld-1", position=2).position == 2
    assert body_of(update) == {"position": 2}
    assert client.library.delete_folder("fld-1") is None
    assert delete.called


def test_library_move_sends_null_for_the_main_list(api, client: MatterWave) -> None:
    route = api.put(f"/api/library/conversations/{CONV}/folder").respond(
        200, json={**CONVERSATION, "folder_id": None}, headers=H
    )
    moved = client.library.move(CONV, None)
    assert body_of(route) == {"folder_id": None}
    assert moved.folder_id is None


def test_library_tags(api, client: MatterWave) -> None:
    create = api.post("/api/library/tags").respond(201, json=TAG, headers=H)
    delete = api.delete("/api/library/tags/tag-1").respond(204)
    put = api.put(f"/api/library/conversations/{CONV}/tags").respond(
        200, json={"conversation_id": CONV, "tag_ids": ["tag-1"]}, headers=H
    )
    assert client.library.create_tag("urgent").id == "tag-1"
    assert body_of(create) == {"name": "urgent"}
    tags = client.library.set_tags(CONV, ["tag-1"])
    assert body_of(put) == {"tag_ids": ["tag-1"]}
    assert tags.tag_ids == ["tag-1"]
    assert client.library.delete_tag("tag-1") is None
    assert delete.called


# --- shares -------------------------------------------------------------------


SHARE = {
    "id": "shr-1",
    "conversation_id": CONV,
    "title": "New chat",
    "message_count": 2,
    "created_at": "2026-10-18T09:00:00+00:00",
    "expires_at": None,
    "revoked_at": None,
    "view_count": 0,
    "status": "active",
}


def test_shares_create_returns_the_token_once(api, client: MatterWave) -> None:
    route = api.post(f"/api/conversations/{CONV}/share").respond(
        201, json={"share": SHARE, "token": "tok_x", "path": "/share/tok_x"}, headers=H
    )
    created = client.shares.create(CONV, expires_in_s=3600)
    assert body_of(route) == {"expires_in_s": 3600}
    assert (created.token, created.path) == ("tok_x", "/share/tok_x")
    assert created.share.status == "active"
    assert "tok_x" not in repr(created)


def test_shares_create_without_an_expiry_sends_null(api, client: MatterWave) -> None:
    route = api.post(f"/api/conversations/{CONV}/share").respond(
        201, json={"share": SHARE, "token": "t", "path": "/share/t"}, headers=H
    )
    client.shares.create(CONV)
    assert body_of(route) == {"expires_in_s": None}


def test_shares_list_list_mine_revoke(api, client: MatterWave) -> None:
    api.get(f"/api/conversations/{CONV}/shares").respond(200, json={"data": [SHARE]}, headers=H)
    api.get("/api/me/shares").respond(
        200, json={"data": [SHARE, {**SHARE, "id": "shr-2", "status": "revoked"}]}, headers=H
    )
    revoke = api.delete("/api/shares/shr-1").respond(200, json={"status": "revoked"}, headers=H)
    assert [s.id for s in client.shares.list(CONV)] == ["shr-1"]
    assert [s.status for s in client.shares.list_mine()] == ["active", "revoked"]
    assert client.shares.revoke("shr-1") is None
    assert revoke.called


def test_shares_view_is_public_and_sends_no_key(api) -> None:
    route = api.get("/api/share/tok_x").respond(
        200,
        json={
            "title": "New chat",
            "shared_at": "2026-10-18T09:00:00+00:00",
            "messages": [{"role": "user", "content": "hi"}],
            "org_name": None,
        },
        headers=H,
    )
    keyless = MatterWave(api_key=None, base_url="https://matterwave.test")
    shared = keyless.shares.view("tok_x")
    assert "authorization" not in sent(route).headers
    assert shared.messages == [{"role": "user", "content": "hi"}]
    assert shared.org_name is None


# --- budget --------------------------------------------------------------------


def budget_body(**extra) -> dict:
    return {
        "period_start": "2026-10-01",
        "included_tokens": 2_000_000,
        "tokens_used": 1_250_000,
        "remaining_tokens": 750_000,
        "overage_enabled": True,
        "overage_cap_micros": 5_000_000,
        "overage_micros": 0,
        "member_default_cap_micros": None,
        "member_cap": None,
        "member_spent": 0,
        "member_exhausted": False,
        **extra,
    }


def member_budget_body(**extra) -> dict:
    return {
        "subject": "u-1",
        "period_start": "2026-10-01",
        "cap_micros": None,
        "own": False,
        "spent_micros": 42_000,
        "exhausted": False,
        **extra,
    }


def test_budget_get_reads_the_balance_and_the_callers_own_cap(
    api, client: MatterWave
) -> None:
    route = api.get("/api/budget").mock(
        return_value=httpx.Response(200, json=budget_body(), headers=H)
    )
    budget = client.budget.get()
    assert budget.request_id == REQUEST_ID
    assert budget.remaining_tokens == 750_000 and budget.included_tokens == 2_000_000
    assert budget.overage_enabled and budget.overage_cap_micros == 5_000_000
    assert budget.member_cap is None and budget.member_exhausted is False
    assert sent(route).method == "GET"


def test_set_overage_cap_puts_the_number(api, client: MatterWave) -> None:
    route = api.put("/api/budget").mock(
        return_value=httpx.Response(
            200,
            json=budget_body(
                overage_cap_micros=10_000_000, member_default_cap_micros=None
            ),
            headers=H,
        )
    )
    budget = client.budget.set_overage_cap(10_000_000)
    assert budget.overage_cap_micros == 10_000_000
    assert body_of(route) == {"overage_cap_micros": 10_000_000}
    assert route.call_count == 1
    # A negative cap is refused here rather than spent on a 422.
    with pytest.raises(ValueError):
        client.budget.set_overage_cap(-1)
    assert route.call_count == 1


def test_budget_members_reads_rows_and_sets_the_default_cap(
    api, client: MatterWave
) -> None:
    listing = {
        "period_start": "2026-10-01",
        "member_default_cap_micros": None,
        "data": [
            member_budget_body(email="a@example.test", name="Ada", role="owner", state="active"),
            member_budget_body(
                subject="u-2",
                cap_micros=1_000_000,
                own=True,
                exhausted=True,
                email="b@example.test",
                name="Bob",
                role="member",
                state="active",
            ),
        ],
    }
    listed = api.get("/api/budget/members").mock(
        return_value=httpx.Response(200, json=listing, headers=H)
    )
    put = api.put("/api/budget/members").mock(
        return_value=httpx.Response(
            200, json={**listing, "member_default_cap_micros": 2_000_000}, headers=H
        )
    )
    members = client.budget.members()
    assert [m.subject for m in members] == ["u-1", "u-2"]
    assert members.data[1].own and members.data[1].exhausted
    assert members.data[1].cap_micros == 1_000_000
    assert listed.calls.last.request.method == "GET"
    updated = client.budget.set_member_default_cap(2_000_000)
    assert updated.member_default_cap_micros == 2_000_000
    assert body_of(put) == {"member_default_cap_micros": 2_000_000}
    # None lifts the default, and is sent rather than dropped.
    client.budget.set_member_default_cap(None)
    assert body_of(put) == {"member_default_cap_micros": None}


def test_set_member_cap_addresses_the_subject_and_accepts_null(
    api, client: MatterWave
) -> None:
    route = api.put("/api/budget/members/u-2").mock(
        return_value=httpx.Response(
            200,
            json=member_budget_body(cap_micros=1_000_000, own=True),
            headers=H,
        )
    )
    capped = client.budget.set_member_cap("u-2", 1_000_000)
    assert capped.own and capped.cap_micros == 1_000_000
    assert sent(route).url.path == "/api/budget/members/u-2"
    assert body_of(route) == {"cap_micros": 1_000_000}
    client.budget.set_member_cap("u-2", None)
    assert body_of(route) == {"cap_micros": None}
    with pytest.raises(ValueError):
        client.budget.set_member_cap("../keys", 1)


# --- async ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_async_clients_make_the_same_requests(api, async_client: AsyncMatterWave) -> None:
    api.post(f"/api/keys/{KEY_ID}/rotate").respond(200, json=key_body(key="s"), headers=H)
    api.delete(f"/api/keys/{KEY_ID}").respond(204)
    api.get("/api/org/usage.csv").respond(200, text=CSV, headers=H)
    api.get("/api/org/webhooks").respond(200, json={"data": [], "events": []}, headers=H)
    api.post("/api/attachments").respond(201, json=attachment_body(), headers=H)
    api.get(f"/api/attachments/{ATTACHMENT_ID}/content").respond(200, content=b"hello")
    api.get("/api/library").respond(200, json={"folders": [], "tags": [], "assignments": {}})
    api.get("/api/me/shares").respond(200, json={"data": [SHARE]}, headers=H)
    api.get("/api/budget").respond(200, json=budget_body(), headers=H)
    async with async_client as client:
        assert (await client.keys.rotate(KEY_ID)).key == "s"
        assert await client.keys.revoke(KEY_ID) is None
        assert await client.usage.export_csv() == CSV
        assert len(await client.webhooks.list()) == 0
        attachment = await client.attachments.upload(b"hello", "notes.txt")
        assert await client.attachments.download(attachment) == b"hello"
        assert (await client.library.get()).folders == []
        assert len(await client.shares.list_mine()) == 1
        assert (await client.budget.get()).remaining_tokens == 750_000
