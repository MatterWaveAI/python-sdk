"""The account and data clients against the fake API: `account`,
`preferences`, `sessions`, `exports`, `imports`, `speech`, the attachment
scan and view, `billing.status`, `plans` and `policy`. Each test answers
with the body the platform's route sends and checks the request the client
made and what it parsed."""

from __future__ import annotations

import json

import httpx
import pytest
from conftest import API_KEY, BASE_URL, REQUEST_ID

from matterwave_sdk import (
    APITimeoutError,
    AsyncMatterWave,
    MatterWave,
    MatterWaveError,
    NotFoundError,
)

H = {"X-Request-Id": REQUEST_ID}
PREFERENCES = {
    "email": {"product_updates": False, "usage_alerts": True, "billing_receipts": True},
    "locale": "en-GB",
    "notifications": {"budget_warn": True},
}
EXPORT = {
    "id": "exp-1",
    "status": "pending",
    "size_bytes": None,
    "created_at": "2026-10-31T09:00:00+00:00",
    "completed_at": None,
    "expires_at": None,
}
READY = {
    **EXPORT,
    "status": "ready",
    "size_bytes": 2048,
    "download_url": "/api/me/export/exp-1/download?expires=1793000000&sig=abc123",
}
IMPORT = {
    "id": "imp-1",
    "status": "pending",
    "source": "chatgpt",
    "filename": "conversations.json",
    "size_bytes": 1234,
    "conversations_total": 0,
    "conversations_done": 0,
    "conversations_created": 0,
    "conversations_existing": 0,
    "conversations_skipped": 0,
    "messages_created": 0,
    "attachments_skipped": 0,
    "branches_dropped": 0,
    "error": None,
    "created_at": "2026-10-31T09:00:00+00:00",
    "started_at": None,
    "completed_at": None,
}


def body_of(route) -> dict:
    return json.loads(route.calls.last.request.content)


# --- the account ---------------------------------------------------------------


def test_account_update_accept_terms_and_enforcement(api, client: MatterWave) -> None:
    rename = api.patch("/api/me").respond(200, json={"name": "Ada Lovelace"}, headers=H)
    terms = api.post("/api/me/accept-terms").respond(
        200,
        json={
            "terms_version": "2026-10",
            "terms_accepted_at": "2026-10-31T09:00:00+00:00",
            "terms_current": True,
        },
    )
    api.get("/api/me/enforcement").respond(
        200,
        json={
            "state": "rate_limited",
            "reason": "spam",
            "since": "2026-10-30T00:00:00+00:00",
            "expires_at": None,
            "scope": "user",
            "appeal_path": "/policy#appeal",
            "limit": {"messages": 10, "window_s": 3600},
        },
    )
    named = client.account.update(full_name="  Ada   Lovelace ")
    assert body_of(rename) == {"full_name": "  Ada   Lovelace "}
    assert (named.name, named.request_id) == ("Ada Lovelace", REQUEST_ID)
    accepted = client.account.accept_terms("2026-10")
    assert body_of(terms) == {"version": "2026-10"}
    assert accepted.terms_current is True
    held = client.account.enforcement()
    assert held.state == "rate_limited" and held.limit is not None and held.limit.window_s == 3600
    with pytest.raises(ValueError):
        client.account.update(full_name="   ")


def test_enforcement_none_is_a_bare_state(api, client: MatterWave) -> None:
    api.get("/api/me/enforcement").respond(200, json={"state": "none"})
    held = client.account.enforcement()
    assert (held.state, held.reason, held.limit) == ("none", None, None)


def test_preferences_update_sends_the_whole_object_with_the_changes(
    api, client: MatterWave
) -> None:
    api.get("/api/me/preferences").respond(200, json=PREFERENCES)
    put = api.put("/api/me/preferences").respond(200, json=PREFERENCES)
    client.preferences.update(product_updates=True)
    assert body_of(put) == {
        "locale": "en-GB",
        "email": {"product_updates": True, "usage_alerts": True, "billing_receipts": True},
        "notifications": {"budget_warn": True},
    }
    client.preferences.update(locale=None, budget_warn=False)
    assert body_of(put)["locale"] is None
    assert body_of(put)["notifications"] == {"budget_warn": False}
    assert body_of(put)["email"]["product_updates"] is False


def test_sessions_list_hides_the_ids_and_revokes(api, client: MatterWave) -> None:
    api.get("/api/me/sessions").respond(
        200,
        json={
            "current_id": None,
            "sessions": [
                {
                    "id": "secret-session-value",
                    "current": False,
                    "created_at": "2026-10-01T00:00:00+00:00",
                    "last_seen_at": "2026-10-31T00:00:00+00:00",
                    "user_agent": "Firefox",
                    "ip": "203.0.113.9",
                }
            ],
        },
    )
    revoke = api.delete("/api/me/sessions/s-1").respond(
        200, json={"status": "revoked", "current": False}
    )
    api.post("/api/me/sessions/revoke-others").respond(200, json={"revoked": 3})
    sessions = client.sessions.list()
    assert [s.user_agent for s in sessions] == ["Firefox"]
    assert "secret-session-value" not in repr(sessions)
    assert client.sessions.revoke("s-1").status == "revoked"
    assert revoke.called
    assert client.sessions.revoke_others().revoked == 3


# --- exports -------------------------------------------------------------------


def test_create_and_download_waits_for_the_export_then_fetches_it(
    api, client: MatterWave, sleeps
) -> None:
    api.post("/api/me/export").respond(202, json=EXPORT)
    api.get("/api/me/export/exp-1").mock(
        side_effect=[httpx.Response(200, json=EXPORT), httpx.Response(200, json=READY)]
    )
    download = api.get("/api/me/export/exp-1/download").respond(
        200, content=b'{"format_version": 2}', headers={"Content-Type": "application/json"}
    )
    document = client.exports.create_and_download(poll_interval=2)
    assert json.loads(document) == {"format_version": 2}
    assert dict(download.calls.last.request.url.params) == {
        "expires": "1793000000",
        "sig": "abc123",
    }
    assert download.calls.last.request.headers["authorization"] == f"Bearer {API_KEY}"
    assert sleeps == [2]


def test_a_ready_export_keeps_its_link_out_of_repr(api, client: MatterWave) -> None:
    api.get("/api/me/export/exp-1").respond(200, json=READY)
    ready = client.exports.get("exp-1")
    assert ready.download_url is not None and "abc123" not in repr(ready)


def test_wait_raises_when_the_export_failed_or_time_runs_out(
    api, client: MatterWave, sleeps
) -> None:
    api.get("/api/me/export/exp-1").respond(200, json={**EXPORT, "status": "failed"})
    with pytest.raises(MatterWaveError, match="failed"):
        client.exports.wait("exp-1")
    api.get("/api/me/export/exp-2").respond(200, json={**EXPORT, "id": "exp-2"})
    with pytest.raises(APITimeoutError):
        client.exports.wait("exp-2", timeout=0)


def test_download_refuses_an_export_that_is_not_ready(client: MatterWave) -> None:
    from matterwave_sdk import AccountExport

    with pytest.raises(ValueError, match="not ready|no download_url"):
        client.exports.download(AccountExport.from_dict(EXPORT))
    with pytest.raises(ValueError, match="not an export download URL"):
        client.exports.download("/api/attachments/a/content")


def test_the_inline_export_is_the_document(api, client: MatterWave) -> None:
    api.get("/api/me/export").respond(200, json={"format_version": 2, "conversations": []})
    assert client.exports.document()["format_version"] == 2


# --- imports -------------------------------------------------------------------


def test_imports_upload_list_get_and_wait(api, client: MatterWave, sleeps) -> None:
    create = api.post("/api/me/imports").respond(202, json=IMPORT)
    api.get("/api/me/imports").respond(200, json={"data": [IMPORT]})
    api.get("/api/me/imports/imp-1").mock(
        side_effect=[
            httpx.Response(200, json={**IMPORT, "status": "running"}),
            httpx.Response(200, json={**IMPORT, "status": "done", "conversations_created": 12}),
        ]
    )
    job = client.imports.create(b"[]", "conversations.json")
    sent = create.calls.last.request
    assert b'name="file"; filename="conversations.json"' in sent.content
    assert b"Content-Type: application/json" in sent.content
    assert job.status == "pending"
    assert [j.id for j in client.imports.list()] == ["imp-1"]
    assert client.imports.wait("imp-1").conversations_created == 12


def test_a_failed_import_raises_with_its_reason(api, client: MatterWave) -> None:
    api.get("/api/me/imports/imp-1").respond(
        200, json={**IMPORT, "status": "failed", "error": "not an export"}
    )
    with pytest.raises(MatterWaveError, match="not an export"):
        client.imports.wait("imp-1")


# --- speech and attachments ----------------------------------------------------


def test_transcribe_sends_the_clip_its_type_and_the_language(api, client: MatterWave) -> None:
    route = api.post("/api/speech/transcribe").respond(200, json={"text": "hello there"})
    said = client.speech.transcribe(b"OggS...", "note.ogg", language="en")
    assert said.text == "hello there"
    sent = route.calls.last.request.content
    assert b'filename="note.ogg"' in sent and b"Content-Type: audio/ogg" in sent
    assert b'name="language"' in sent and b"\r\n\r\nen\r\n" in sent
    client.speech.transcribe(b"...", "clip.bin", content_type="audio/webm")
    assert b"Content-Type: audio/webm" in route.calls.last.request.content
    with pytest.raises(ValueError, match="content_type"):
        client.speech.transcribe(b"...", "clip")
    with pytest.raises(ValueError, match="empty"):
        client.speech.transcribe(b"", "clip.ogg")


def test_a_deployment_without_a_transcriber_is_a_404(api, client: MatterWave) -> None:
    api.post("/api/speech/transcribe").respond(
        404, json={"detail": "Transcription is not configured on this deployment."}
    )
    with pytest.raises(NotFoundError):
        client.speech.transcribe(b"...", "a.wav")


def test_attachment_scan_and_view_follows_the_signed_link(api, client: MatterWave) -> None:
    api.get("/api/attachments/att-1/scan").respond(
        200, json={"id": "att-1", "scan_status": "clean"}
    )
    api.get("/api/attachments/att-1/view").respond(
        302, headers={"Location": "/api/attachments/att-1/content?expires=1&sig=xyz"}
    )
    content = api.get("/api/attachments/att-1/content").respond(200, content=b"%PDF-1.7")
    assert client.attachments.scan("att-1").scan_status == "clean"
    assert client.attachments.view("att-1") == b"%PDF-1.7"
    assert dict(content.calls.last.request.url.params) == {"expires": "1", "sig": "xyz"}
    assert content.calls.last.request.headers["authorization"] == f"Bearer {API_KEY}"


# --- billing, plans and policy -------------------------------------------------


def test_billing_status_reads_the_entitlements_and_the_dunning(api, client: MatterWave) -> None:
    api.get("/api/billing/status").respond(
        200,
        json={
            "org_id": "org-1",
            "plan": "team",
            "plan_name": "Team",
            "status": "active",
            "seats": 5,
            "billing_enabled": True,
            "collection_method": "send_invoice",
            "days_until_due": 30,
            "dunning": {
                "stage": "warn",
                "source": "invoice",
                "first_overdue_at": "2026-10-20T00:00:00+00:00",
                "days_overdue": 11,
                "outstanding_micros": 49_000_000,
                "schedule": {
                    "warn_at": "a",
                    "warn_final_at": "b",
                    "limited_at": "c",
                    "suspended_at": None,
                    "days_limit_at": "d",
                },
                "next_step": {"stage": "warn_final", "at": "b"},
                "overage_stopped": False,
                "limited": {
                    "stops_overage": True,
                    "blocks_new_keys": True,
                    "blocks_upgrades": True,
                },
                "auto_suspend": False,
                "pay_url": "https://pay.example.test/inv",
            },
        },
    )
    status = client.billing.status()
    assert status.entitlements.plan == "team" and status.entitlements.seats == 5
    assert status.billing_enabled and status.days_until_due == 30
    assert status.dunning is not None and status.dunning.next_step is not None
    assert status.dunning.next_step.stage == "warn_final"
    assert status.dunning.limited is not None and status.dunning.limited.blocks_new_keys


def test_plans_and_policy_are_public_and_send_no_credential(api, client: MatterWave) -> None:
    plans = api.get("/api/plans").respond(
        200,
        json={
            "data": [
                {
                    "code": "free",
                    "name": "Free",
                    "price_cents_monthly": 0,
                    "price_cents_annual": 0,
                    "included_tokens": 100000,
                    "included_tokens_per_seat": None,
                    "per_seat": False,
                    "overage_enabled": False,
                    "default_overage_cap_micros": 0,
                    "trial_days": 0,
                    "models": ["grt-nano"],
                    "limits": {"max_seats": 1},
                }
            ],
            "models": [
                {
                    "id": "grt-nano",
                    "label": "GRT Nano",
                    "multiplier": 1,
                    "input_usd_per_m": 0.1,
                    "output_usd_per_m": 0.4,
                    "partner": False,
                }
            ],
            "default_model": "grt-nano",
        },
    )
    stats = api.get("/api/policy/stats").respond(
        200,
        json={
            "reports": {"by_kind": {"unsafe": 2, "unhelpful": 5}},
            "enforcement": {"active": {"warned": 1, "rate_limited": 0, "suspended": 0}},
        },
    )
    anonymous = MatterWave(base_url=BASE_URL, api_key=None)
    catalog = anonymous.plans.list()
    assert [p.code for p in catalog] == ["free"] and catalog.models[0].input_usd_per_m == 0.1
    assert catalog.data[0].limits == {"max_seats": 1}
    assert anonymous.policy.stats().reports["by_kind"]["unhelpful"] == 5
    client.plans.list()
    assert "authorization" not in plans.calls.last.request.headers
    assert "authorization" not in stats.calls.last.request.headers


@pytest.mark.asyncio
async def test_the_async_clients_match(api, async_client: AsyncMatterWave, sleeps) -> None:
    api.get("/api/me/preferences").respond(200, json=PREFERENCES)
    put = api.put("/api/me/preferences").respond(200, json=PREFERENCES)
    api.post("/api/me/export").respond(202, json=EXPORT)
    api.get("/api/me/export/exp-1").respond(200, json=READY)
    api.get("/api/me/export/exp-1/download").respond(200, content=b"{}")
    api.post("/api/speech/transcribe").respond(200, json={"text": "hi"})
    api.get("/api/me/sessions").respond(200, json={"current_id": None, "sessions": []})
    api.get("/api/billing/status").respond(200, json={"plan": "free", "billing_enabled": False})
    await async_client.preferences.update(usage_alerts=False)
    assert body_of(put)["email"]["usage_alerts"] is False
    assert await async_client.exports.create_and_download() == b"{}"
    assert (await async_client.speech.transcribe(b"...", "a.mp3")).text == "hi"
    assert len(await async_client.sessions.list()) == 0
    status = await async_client.billing.status()
    assert status.entitlements.plan == "free" and status.dunning is None
