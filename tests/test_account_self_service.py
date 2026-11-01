"""The account self-service routes (`client.account`, `client.sessions`,
`client.preferences`) against the fake API: the writes the Account and
Preferences panes make. Each test answers with the body the route in
api/matterwave sends and checks the request the client made (method, path,
body) and what it parsed out of the answer.
"""

from __future__ import annotations

import re
from pathlib import Path

import httpx
import pytest
from conftest import API_KEY, REQUEST_ID

from matterwave_sdk import (
    APP_OPERATIONS,
    AsyncMatterWave,
    MatterWave,
    NotFoundError,
    PermissionDeniedError,
    UnprocessableEntityError,
)

SESSION_ID = "sess-0001"
H = {"X-Request-Id": REQUEST_ID}


def sent(route) -> httpx.Request:
    return route.calls.last.request


def body_of(route) -> dict:
    import json

    return json.loads(sent(route).content)


def _family_operations() -> dict[str, tuple[str, str]]:
    return {k: v for k, v in APP_OPERATIONS.items() if k.split(".")[0] == "account"}


def test_every_account_operation_names_a_method_on_both_clients() -> None:
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
    """Every (method, path) the app's routers declare, read from the source
    so the check needs none of the app's dependencies."""
    found: set[tuple[str, str]] = set()
    for prefix_width in (2, 3, 4):
        base = Path(__file__).resolve().parents[prefix_width]
        routes = base / "matterwave-app" / "api" / "matterwave"
        if not routes.is_dir():
            routes = base / "api" / "matterwave"
        if not routes.is_dir():
            continue
        for source in routes.glob("*.py"):
            text = source.read_text(encoding="utf-8")
            prefixes = dict(re.findall(r'(\w+)\s*=\s*APIRouter\([^)]*prefix="([^"]*)"', text))
            for router, method, path in re.findall(
                r'@(\w+)\.(get|post|put|patch|delete)\(\s*"([^"]*)"', text
            ):
                found.add((method.upper(), prefixes.get(router, "") + path))
    return found


def test_every_account_operation_is_a_route_the_api_declares() -> None:
    routes = _api_routes()
    if not routes:
        pytest.skip("matterwave-app/api/matterwave is not beside the SDK in this checkout")
    for key, (method, path) in _family_operations().items():
        assert (method, path) in routes, f"{key}: {method} {path} is not an API route"


# --- terms, onboarding ---------------------------------------------------------


def test_accept_terms_sends_the_version_and_parses_the_receipt(api, client: MatterWave) -> None:
    route = api.post("/api/me/accept-terms").respond(
        200,
        json={
            "terms_version": "2026-10",
            "terms_accepted_at": "2026-10-31T12:00:00+00:00",
            "terms_current": True,
        },
        headers=H,
    )
    accepted = client.account.accept_terms("2026-10")
    assert body_of(route) == {"version": "2026-10"}
    assert accepted.terms_version == "2026-10" and accepted.terms_current
    # A stale constant is the server's 422, and the SDK raises it named.
    api.post("/api/me/accept-terms").respond(
        422, json={"detail": "The current terms are version 2026-10."}, headers=H
    )
    with pytest.raises(UnprocessableEntityError, match="current terms"):
        client.account.accept_terms("2024-01")
    with pytest.raises(ValueError):
        client.account.accept_terms("")


def test_complete_onboarding_is_idempotent_from_the_callers_side(api, client: MatterWave) -> None:
    route = api.post("/api/me/onboarding/complete").respond(
        200,
        json={"onboarding_completed": True, "completed_at": "2026-10-31T09:00:00+00:00"},
        headers=H,
    )
    done = client.account.complete_onboarding()
    assert sent(route).content == b""
    assert done.onboarding_completed and done.completed_at.endswith("+00:00")


# --- password --------------------------------------------------------------------


def test_change_password_sends_both_and_reports_the_revocations(api, client: MatterWave) -> None:
    route = api.post("/api/me/password").respond(
        200, json={"status": "changed", "revoked": 3}, headers=H
    )
    changed = client.account.change_password("current-passphrase", "a better twelve+")
    assert body_of(route) == {
        "current_password": "current-passphrase",
        "new_password": "a better twelve+",
    }
    assert changed.status == "changed" and changed.revoked == 3


def test_change_password_bounds_are_checked_here(api, client: MatterWave) -> None:
    route = api.post("/api/me/password").respond(
        200, json={"status": "changed", "revoked": 0}, headers=H
    )
    with pytest.raises(ValueError, match="12"):
        client.account.change_password("current-passphrase", "short")
    with pytest.raises(ValueError, match="non-empty"):
        client.account.change_password("", "a better twelve+")
    with pytest.raises(ValueError, match="256"):
        client.account.change_password("x" * 257, "a better twelve+")
    assert route.call_count == 0


def test_change_password_refusals_arrive_as_their_statuses(api, client: MatterWave) -> None:
    # A session only: an API key is refused before anything is checked.
    api.post("/api/me/password").respond(
        403,
        json={"detail": "A password is changed from a signed-in session, not through an API key."},
        headers=H,
    )
    with pytest.raises(PermissionDeniedError, match="signed-in session"):
        client.account.change_password("current", "a better twelve+")
    # The weak-password answer is a 400 carrying a stable `code`.
    api.post("/api/me/password").respond(
        400,
        json={
            "detail": "Use at least 12 characters: length is what makes a password hard to "
            "guess, not symbols.",
            "code": "weak_password",
        },
        headers=H,
    )
    with pytest.raises(Exception, match="weak_password|12 characters"):
        client.account.change_password("current", "short")


# --- email change -------------------------------------------------------------------


def test_email_change_start_verifies_the_address_shape_and_mails_a_code(
    api, client: MatterWave
) -> None:
    route = api.post("/api/me/email").respond(
        200,
        json={"status": "code_sent", "new_email": "ada@example.test", "mail_sent": True},
        headers=H,
    )
    started = client.account.start_email_change("Ada@Example.test", "current-passphrase")
    assert body_of(route) == {
        "new_email": "ada@example.test",
        "current_password": "current-passphrase",
    }
    assert started.new_email == "ada@example.test" and started.mail_sent
    with pytest.raises(ValueError, match="email"):
        client.account.start_email_change("not-an-address", "current-passphrase")


def test_email_change_refusals_keep_their_codes(api, client: MatterWave) -> None:
    api.post("/api/me/email").respond(
        409,
        json={
            "detail": "Your address comes from your organisation's single sign-on; change it "
            "there.",
            "code": "sso_managed",
        },
        headers=H,
    )
    with pytest.raises(Exception, match="sso_managed|single sign-on"):
        client.account.start_email_change("ada@example.test", "current-passphrase")


def test_email_verify_moves_the_address_and_reports_the_aftermath(api, client: MatterWave) -> None:
    route = api.post("/api/me/email/verify").respond(
        200,
        json={"status": "changed", "email": "ada@example.test", "revoked": 2, "notice_sent": True},
        headers=H,
    )
    changed = client.account.verify_email_change(" 123456 ")
    assert body_of(route) == {"code": "123456"}
    assert changed.email == "ada@example.test" and changed.revoked == 2
    assert changed.notice_sent is True


def test_email_cancel_and_state_round_trip(api, client: MatterWave) -> None:
    state = api.get("/api/me/email").respond(
        200, json={"email": "ada@example.test", "pending_email": "new@example.test"}, headers=H
    )
    current = client.account.email_change_state()
    assert sent(state).method == "GET"
    assert current.pending_email == "new@example.test"
    cancel = api.post("/api/me/email/cancel").respond(
        200, json={"status": "cancelled", "email": "ada@example.test"}, headers=H
    )
    cancelled = client.account.cancel_email_change()
    assert sent(cancel).content == b""
    assert cancelled.status == "cancelled"
    resend = api.post("/api/me/email/resend").respond(
        200,
        json={"status": "code_sent", "new_email": "new@example.test", "mail_sent": False},
        headers=H,
    )
    client.account.resend_email_change()
    assert resend.called


# --- sessions -------------------------------------------------------------------------


def session_body(**extra) -> dict:
    return {
        "id": SESSION_ID,
        "current": False,
        "created_at": "2026-10-01T09:00:00+00:00",
        "last_seen_at": "2026-10-31T08:00:00+00:00",
        "user_agent": "Mozilla/5.0",
        "ip": "203.0.113.7",
        **extra,
    }


def test_sessions_list_flags_the_current_one(api, client: MatterWave) -> None:
    route = api.get("/api/me/sessions").respond(
        200,
        json={
            "current_id": SESSION_ID,
            "sessions": [
                session_body(current=True),
                session_body(id="sess-0002", ip=None),
            ],
        },
        headers=H,
    )
    sessions = client.sessions.list()
    assert sent(route).headers["authorization"] == f"Bearer {API_KEY}"
    assert sessions.current_id == SESSION_ID
    assert len(sessions) == 2
    assert sessions.sessions[0].current and sessions.sessions[1].ip is None


def test_revoke_session_answers_whether_it_was_the_callers_own(api, client: MatterWave) -> None:
    route = api.delete(f"/api/me/sessions/{SESSION_ID}").respond(
        200, json={"status": "revoked", "current": True}, headers=H
    )
    revoked = client.sessions.revoke(SESSION_ID)
    assert revoked.current  # "sign out"
    assert route.called
    with pytest.raises(ValueError, match="session_id"):
        client.sessions.revoke("../keys")
    # Another person's id -- or one already gone -- is the same 404.
    api.delete("/api/me/sessions/sess-0002").respond(
        404, json={"detail": "No such session."}, headers=H
    )
    with pytest.raises(NotFoundError):
        client.sessions.revoke("sess-0002")


def test_revoke_others_counts_what_it_ended(api, client: MatterWave) -> None:
    route = api.post("/api/me/sessions/revoke-others").respond(200, json={"revoked": 4}, headers=H)
    assert client.sessions.revoke_others().revoked == 4
    assert sent(route).content == b""


# --- preferences ------------------------------------------------------------------------


PREFERENCES = {
    "email": {"product_updates": False, "usage_alerts": True, "billing_receipts": True},
    "locale": "en-GB",
    "notifications": {"budget_warn": False},
}


def test_preferences_read(api, client: MatterWave) -> None:
    route = api.get("/api/me/preferences").respond(200, json=PREFERENCES, headers=H)
    prefs = client.preferences.get()
    assert prefs.locale == "en-GB"
    assert prefs.email.usage_alerts and prefs.email.billing_receipts
    assert prefs.notifications.budget_warn is False
    assert route.called


def test_preferences_update_keeps_what_it_was_not_given(api, client: MatterWave) -> None:
    api.get("/api/me/preferences").respond(200, json=PREFERENCES, headers=H)
    saved_body = {
        **PREFERENCES,
        "email": {**PREFERENCES["email"], "product_updates": True},
    }
    route = api.put("/api/me/preferences").respond(200, json=saved_body, headers=H)
    saved = client.preferences.update(product_updates=True)
    assert body_of(route) == saved_body
    assert saved.email.product_updates is True
    client.preferences.update(locale=None)
    assert body_of(route) == {**PREFERENCES, "locale": None}
    # An unknown locale is the server's 422, by its own validator.
    api.put("/api/me/preferences").respond(
        422,
        json={"detail": [{"loc": ["body", "locale"], "msg": "must be a BCP-47 language tag"}]},
        headers=H,
    )
    with pytest.raises(UnprocessableEntityError):
        client.preferences.update(locale="not a locale")


# --- the account deletion ------------------------------------------------------------------


DELETION = {
    "status": "deleted",
    "conversations_deleted": 12,
    "attachments_marked_deleted": 3,
    "api_keys_revoked": 2,
    "custom_instructions_deleted": True,
    "retention_days": 30,
    "identity_removed": True,
    "deletion_request_id": "del-0001",
}


def test_delete_requires_the_phrase_before_anything_is_sent(api, client: MatterWave) -> None:
    route = api.delete("/api/me").mock(return_value=httpx.Response(200, json=DELETION, headers=H))
    with pytest.raises(ValueError, match="delete my account"):
        client.account.delete(confirm="yes")
    with pytest.raises(TypeError):
        client.account.delete()  # type: ignore[call-arg]
    assert route.call_count == 0
    deleted = client.account.delete(confirm="delete my account")
    assert body_of(route) == {"confirm": "delete my account"}
    assert deleted.status == "deleted"
    assert deleted.conversations_deleted == 12
    assert deleted.identity_removed is True
    assert deleted.deletion_request_id == "del-0001"


def test_delete_reports_a_login_the_provider_did_not_retire(api, client: MatterWave) -> None:
    api.delete("/api/me").respond(
        200,
        json={**DELETION, "identity_removed": False, "deletion_request_id": "del-0002"},
        headers=H,
    )
    deleted = client.account.delete(confirm="delete my account")
    assert deleted.identity_removed is False
    # The last owner of an org with members is refused before anything goes.
    api.delete("/api/me").respond(
        409,
        json={"detail": "Transfer ownership or close the organisation first."},
        headers=H,
    )
    with pytest.raises(Exception, match="ownership|close the organisation"):
        client.account.delete(confirm="delete my account")


# --- async --------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_async_account_makes_the_same_requests(api, async_client: AsyncMatterWave) -> None:
    api.post("/api/me/accept-terms").respond(
        200,
        json={"terms_version": "2026-10", "terms_accepted_at": None, "terms_current": True},
        headers=H,
    )
    api.post("/api/me/password").respond(200, json={"status": "changed", "revoked": 1}, headers=H)
    api.get("/api/me/email").respond(
        200, json={"email": "a@x.test", "pending_email": None}, headers=H
    )
    api.post("/api/me/email").respond(
        200, json={"status": "code_sent", "new_email": "b@x.test", "mail_sent": True}, headers=H
    )
    api.post("/api/me/email/verify").respond(
        200,
        json={"status": "changed", "email": "b@x.test", "revoked": 0, "notice_sent": False},
        headers=H,
    )
    api.post("/api/me/email/cancel").respond(
        200, json={"status": "cancelled", "email": "a@x.test"}, headers=H
    )
    api.post("/api/me/email/resend").respond(
        200, json={"status": "code_sent", "new_email": "b@x.test", "mail_sent": True}, headers=H
    )
    api.get("/api/me/sessions").respond(
        200, json={"current_id": None, "sessions": [session_body()]}, headers=H
    )
    api.delete(f"/api/me/sessions/{SESSION_ID}").respond(
        200, json={"status": "revoked", "current": False}, headers=H
    )
    api.post("/api/me/sessions/revoke-others").respond(200, json={"revoked": 2}, headers=H)
    api.get("/api/me/preferences").respond(200, json=PREFERENCES, headers=H)
    api.put("/api/me/preferences").respond(
        200,
        json={**PREFERENCES, "email": {**PREFERENCES["email"], "product_updates": True}},
        headers=H,
    )
    api.post("/api/me/onboarding/complete").respond(
        200,
        json={"onboarding_completed": True, "completed_at": "2026-10-31T09:00:00+00:00"},
        headers=H,
    )
    api.delete("/api/me").respond(200, json=DELETION, headers=H)
    async with async_client as c:
        assert (await c.account.accept_terms("2026-10")).terms_current
        assert (await c.account.change_password("cur", "a better twelve+")).revoked == 1
        assert (await c.account.email_change_state()).email == "a@x.test"
        assert (await c.account.start_email_change("b@x.test", "cur")).status == "code_sent"
        assert (await c.account.verify_email_change("123456")).email == "b@x.test"
        assert (await c.account.cancel_email_change()).status == "cancelled"
        assert (await c.account.resend_email_change()).status == "code_sent"
        assert len(await c.sessions.list()) == 1
        assert (await c.sessions.revoke(SESSION_ID)).current is False
        assert (await c.sessions.revoke_others()).revoked == 2
        assert (await c.preferences.get()).locale == "en-GB"
        assert (await c.preferences.update(product_updates=True)).email.product_updates is True
        assert (await c.account.complete_onboarding()).onboarding_completed
        deleted = await c.account.delete(confirm="delete my account")
        assert deleted.conversations_deleted == 12
