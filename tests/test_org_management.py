"""The org management resource (`client.org`) against the fake API: the
org itself, its model allow-list, its sharing switch and every share, its
SSO/domains/IdP configuration and its MFA policy. Each test answers with
the body the route in matterwave-app sends and checks the request the
client made. The audit trail is `client.audit`, which shipped earlier.
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

DOMAIN = "example.test"
PROVIDER = "acme-oidc"
SHARE_ID = "sha-0001"
H = {"X-Request-Id": REQUEST_ID}


def sent(route) -> httpx.Request:
    return route.calls.last.request


def body_of(route) -> dict:
    import json

    return json.loads(sent(route).content)


def _family_operations() -> dict[str, tuple[str, str]]:
    return {k: v for k, v in APP_OPERATIONS.items() if k.split(".")[0] == "org"}


def test_every_org_operation_names_a_method_on_both_clients() -> None:
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


def test_every_org_operation_is_a_route_the_api_declares() -> None:
    routes = _api_routes()
    if not routes:
        pytest.skip("matterwave-app/api/matterwave is not beside the SDK in this checkout")
    for key, (method, path) in _family_operations().items():
        assert (method, path) in routes, f"{key}: {method} {path} is not an API route"


# --- the org itself -------------------------------------------------------------------


def org_body(**extra) -> dict:
    return {
        "id": "org-0001",
        "name": "Acme",
        "created_at": "2026-10-01T09:00:00+00:00",
        "role": "owner",
        **extra,
    }


def test_org_get_is_any_members_and_rename_is_admins(api, client: MatterWave) -> None:
    got = api.get("/api/org").respond(200, json=org_body(), headers=H)
    org = client.org.get()
    assert sent(got).headers["authorization"] == f"Bearer {API_KEY}"
    assert org.name == "Acme" and org.role == "owner"
    renamed = api.patch("/api/org").respond(200, json=org_body(name="Acme Ltd"), headers=H)
    renamed_org = client.org.rename("  Acme Ltd ")
    assert body_of(renamed) == {"name": "Acme Ltd"}
    assert renamed_org.name == "Acme Ltd"
    with pytest.raises(ValueError, match="name"):
        client.org.rename("   ")


def test_close_requires_the_name_and_reports_each_side_effect(api, client: MatterWave) -> None:
    route = api.post("/api/org/close").respond(
        200,
        json={
            "status": "closed",
            "closed_at": "2026-10-31T09:00:00+00:00",
            "already_closed": False,
            "conversations_deleted": 42,
            "retention_days": 30,
            "api_keys_revoked": 3,
            "invites_revoked": 1,
            "invite_links_revoked": 2,
            "subscription": "canceled",
            "members_mailed": 4,
            "members_not_mailed": 0,
            "identity_removed": True,
        },
        headers=H,
    )
    closed = client.org.close("Acme")
    assert body_of(route) == {"confirm": "Acme"}
    assert closed.status == "closed" and closed.already_closed is False
    assert closed.conversations_deleted == 42 and closed.retention_days == 30
    assert closed.subscription == "canceled" and closed.identity_removed is True
    # A legal hold is the 409 the route answers, named.
    api.post("/api/org/close").respond(
        409,
        json={
            "detail": "This organisation is under a legal hold and cannot be closed. "
            "Contact support."
        },
        headers=H,
    )
    from matterwave_sdk import MatterWaveError

    with pytest.raises(MatterWaveError, match="legal hold"):
        client.org.close("Acme")


# --- the model allow-list ----------------------------------------------------------------


def model_policy_body(**extra) -> dict:
    return {
        "house_model": "grt-nano",
        "data": [
            {
                "id": "grt-nano",
                "label": "GRT Nano",
                "partner": False,
                "context_window": 128_000,
                "research_preview": False,
                "allowed": True,
                "locked": True,
            },
            {
                "id": "grt-large",
                "label": "GRT Large",
                "partner": True,
                "context_window": None,
                "research_preview": False,
                "allowed": False,
                "locked": False,
            },
        ],
        "unlisted": ["grt-preview"],
        "restricted": True,
        **extra,
    }


def test_model_policy_read(api, client: MatterWave) -> None:
    route = api.get("/api/org/models").respond(200, json=model_policy_body(), headers=H)
    policy = client.org.models()
    assert policy.house_model == "grt-nano"
    assert len(policy) == 2
    assert policy.data[0].locked and policy.data[0].allowed
    assert policy.data[1].partner and policy.data[1].context_window is None
    assert policy.unlisted == ["grt-preview"] and policy.restricted
    assert route.called


def test_model_policy_put_sends_null_to_lift_and_list_to_restrict(api, client: MatterWave) -> None:
    route = api.put("/api/org/models").mock(
        return_value=httpx.Response(200, json=model_policy_body(), headers=H)
    )
    client.org.set_models(["grt-nano", "grt-large"])
    assert body_of(route) == {"allowed": ["grt-nano", "grt-large"]}
    client.org.set_models(None)
    assert body_of(route) == {"allowed": None}
    with pytest.raises(ValueError, match="at least one"):
        client.org.set_models([])
    assert route.call_count == 2
    # An id the plan does not permit is the server's 422 naming it.
    api.put("/api/org/models").respond(
        422, json={"detail": "Not available to this organisation: grt-preview."}, headers=H
    )
    with pytest.raises(UnprocessableEntityError, match="grt-preview"):
        client.org.set_models(["grt-preview"])


# --- sharing ---------------------------------------------------------------------------


def test_sharing_get_and_set(api, client: MatterWave) -> None:
    got = api.get("/api/org/sharing").respond(200, json={"enabled": True}, headers=H)
    assert client.org.sharing().enabled is True
    assert got.called
    put = api.put("/api/org/sharing").respond(200, json={"enabled": False}, headers=H)
    off = client.org.set_sharing(False)
    assert body_of(put) == {"enabled": False}
    assert off.enabled is False


def test_org_shares_list_the_creator_and_revoke_answers(api, client: MatterWave) -> None:
    listed = api.get("/api/org/shares").respond(
        200,
        json={
            "data": [
                {
                    "id": SHARE_ID,
                    "conversation_id": "c0ffee00-0000-4000-8000-000000000001",
                    "title": "Mirror notes",
                    "message_count": 9,
                    "include_attachments": True,
                    "created_at": "2026-10-31T09:00:00+00:00",
                    "expires_at": None,
                    "revoked_at": None,
                    "view_count": 5,
                    "status": "active",
                    "created_by": "user-0002",
                }
            ]
        },
        headers=H,
    )
    shares = client.org.shares()
    assert shares.data[0].created_by == "user-0002"
    assert shares.data[0].include_attachments is True
    api.delete(f"/api/org/shares/{SHARE_ID}").respond(200, json={"status": "revoked"}, headers=H)
    assert client.org.revoke_share(SHARE_ID).status == "revoked"
    # A link of another org and one that does not exist are the same 404.
    api.delete(f"/api/org/shares/{SHARE_ID}").respond(
        404, json={"detail": "No such share."}, headers=H
    )
    with pytest.raises(NotFoundError):
        client.org.revoke_share(SHARE_ID)
    assert listed.called


# --- SSO ----------------------------------------------------------------------------------


def recovery_body(**extra) -> dict:
    return {
        "id": "rc-0001",
        "created_at": "2026-10-31T09:00:00+00:00",
        "expires_at": "2026-11-30T09:00:00+00:00",
        "created_by": "user-0001",
        "used_at": None,
        "code": "break-glass-1234",
        "shown_once": True,
        **extra,
    }


def test_sso_read_carries_the_blockers(api, client: MatterWave) -> None:
    route = api.get("/api/org/sso").respond(
        200,
        json={"sso_required": False, "blockers": ["no verified domain"]},
        headers=H,
    )
    settings = client.org.sso()
    assert settings.sso_required is False
    assert settings.blockers == ["no verified domain"]
    assert route.called


def test_sso_enable_hands_back_the_break_glass_code_once(api, client: MatterWave) -> None:
    route = api.put("/api/org/sso").respond(
        200, json={"sso_required": True, "recovery": recovery_body()}, headers=H
    )
    enforced = client.org.set_sso(True)
    assert body_of(route) == {"sso_required": True}
    assert enforced.sso_required is True
    assert enforced.recovery is not None
    assert enforced.recovery.code == "break-glass-1234"
    assert "break-glass-1234" not in repr(enforced.recovery)
    # Disabling answers without a code.
    api.put("/api/org/sso").respond(200, json={"sso_required": False}, headers=H)
    relaxed = client.org.set_sso(False)
    assert relaxed.sso_required is False and relaxed.recovery is None


def test_sso_recovery_code_mint(api, client: MatterWave) -> None:
    route = api.post("/api/org/sso/recovery-code").respond(
        201, json={"recovery": recovery_body()}, headers=H
    )
    code = client.org.mint_recovery_code()
    assert sent(route).content == b""
    assert code.recovery.id == "rc-0001" and code.recovery.shown_once is True


# --- domains ---------------------------------------------------------------------------------


def domain_body(**extra) -> dict:
    return {
        "domain": DOMAIN,
        "verified": False,
        "verified_at": None,
        "verified_by": None,
        "created_at": "2026-10-31T09:00:00+00:00",
        **extra,
    }


def test_domains_claim_list_and_release(api, client: MatterWave) -> None:
    claim = api.post("/api/org/domains").respond(201, json=domain_body(), headers=H)
    claimed = client.org.domains.claim(" Example.TEST ")
    assert body_of(claim) == {"domain": "example.test"}
    assert claimed.verified is False
    listed = api.get("/api/org/domains").respond(200, json={"data": [domain_body()]}, headers=H)
    assert len(client.org.domains.list()) == 1
    release = api.delete(f"/api/org/domains/{DOMAIN}").respond(204)
    assert client.org.domains.release(DOMAIN) is None
    assert listed.called and release.called
    with pytest.raises(ValueError, match="domain"):
        client.org.domains.claim("not a domain")


def test_domain_challenge_is_stable_and_verify_has_three_answers(api, client: MatterWave) -> None:
    challenge = api.get(f"/api/org/domains/{DOMAIN}/challenge").respond(
        200,
        json={
            "record": {
                "type": "TXT",
                "host": "_matterwave",
                "name": "_matterwave.example.test",
                "value": "mw-verify=abc123",
            }
        },
        headers=H,
    )
    record = client.org.domains.challenge(DOMAIN).record
    assert record.type == "TXT" and record.value == "mw-verify=abc123"
    # Not there yet: 202, the claim accepted and pending.
    api.post(f"/api/org/domains/{DOMAIN}/verify").respond(
        202,
        json={
            "domain": DOMAIN,
            "verified": False,
            "verified_at": None,
            "verified_by": None,
            "method": None,
            "last_checked_at": "2026-10-31T09:01:00+00:00",
            "last_error": None,
            "record": {
                "type": "TXT",
                "host": "_matterwave",
                "name": "_matterwave.example.test",
                "value": "mw-verify=abc123",
            },
            "detail": "The record was not found.",
            "found": [],
        },
        headers=H,
    )
    pending = client.org.domains.verify(DOMAIN)
    assert pending.verified is False and pending.detail == "The record was not found."
    assert pending.found == []
    # Proven: 200.
    api.post(f"/api/org/domains/{DOMAIN}/verify").respond(
        200,
        json={
            "domain": DOMAIN,
            "verified": True,
            "verified_at": "2026-10-31T09:02:00+00:00",
            "verified_by": "user-0001",
            "method": "txt",
            "last_checked_at": "2026-10-31T09:02:00+00:00",
            "last_error": None,
            "record": {
                "type": "TXT",
                "host": "_matterwave",
                "name": "_matterwave.example.test",
                "value": "mw-verify=abc123",
            },
        },
        headers=H,
    )
    proven = client.org.domains.verify(DOMAIN)
    assert proven.verified is True and proven.method == "txt"
    assert challenge.called


# --- identity providers ------------------------------------------------------------------------


def idp_body(**extra) -> dict:
    return {
        "provider": PROVIDER,
        "kind": "oidc",
        "name": "Acme SSO",
        "issuer": "https://id.example.test",
        "created_at": "2026-10-31T09:00:00+00:00",
        **extra,
    }


def test_idp_list_never_carries_a_secret(api, client: MatterWave) -> None:
    route = api.get("/api/org/idp").respond(200, json={"data": [idp_body()]}, headers=H)
    idps = client.org.idps.list()
    assert idps.data[0].provider == PROVIDER
    assert "secret" not in str(idps)
    assert route.called


def test_idp_attach_is_https_only_and_detach_removes(api, client: MatterWave) -> None:
    route = api.post("/api/org/idp").respond(201, json=idp_body(), headers=H)
    attached = client.org.idps.attach(
        "Acme SSO",
        "https://id.example.test/",
        "client-123",
        "secret-value",
        scopes=["openid", "profile"],
    )
    assert body_of(route) == {
        "kind": "oidc",
        "name": "Acme SSO",
        "issuer": "https://id.example.test",
        "client_id": "client-123",
        "client_secret": "secret-value",
        "scopes": ["openid", "profile"],
    }
    assert attached.issuer == "https://id.example.test"
    with pytest.raises(ValueError, match="https"):
        client.org.idps.attach("Bad", "http://id.example.test", "c", "s")
    detach = api.delete(f"/api/org/idp/{PROVIDER}").respond(204)
    assert client.org.idps.detach(PROVIDER) is None
    assert detach.called


# --- the org's MFA policy -------------------------------------------------------------------------


def test_org_mfa_policy_get_and_set(api, client: MatterWave) -> None:
    got = api.get("/api/org/mfa").respond(200, json={"mfa_required": False}, headers=H)
    assert client.org.mfa().mfa_required is False
    assert got.called
    put = api.put("/api/org/mfa").respond(200, json={"mfa_required": True}, headers=H)
    required = client.org.set_mfa(True)
    assert body_of(put) == {"mfa_required": True}
    assert required.mfa_required is True


def test_admin_scoping_is_what_the_server_answers(api, client: MatterWave) -> None:
    """The org panes gate on the membership row, so an API key is refused
    outright on the admin routes; the SDK raises it named."""
    api.get("/api/org/models").respond(
        403,
        json={"detail": "You need the admin or owner role in this organisation."},
        headers=H,
    )
    with pytest.raises(PermissionDeniedError, match="owner role"):
        client.org.models()


# --- async -----------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_async_org_makes_the_same_requests(api, async_client: AsyncMatterWave) -> None:
    api.get("/api/org").respond(200, json=org_body(), headers=H)
    api.patch("/api/org").respond(200, json=org_body(name="Acme Ltd"), headers=H)
    api.post("/api/org/close").respond(
        200,
        json={
            "status": "closed",
            "closed_at": "2026-10-31T09:00:00+00:00",
            "already_closed": True,
            "conversations_deleted": 0,
            "retention_days": 30,
            "members_mailed": 0,
            "members_not_mailed": 0,
            "api_keys_revoked": None,
            "invites_revoked": None,
            "invite_links_revoked": None,
            "subscription": "canceled",
            "identity_removed": False,
        },
        headers=H,
    )
    api.get("/api/org/models").respond(200, json=model_policy_body(), headers=H)
    api.put("/api/org/models").respond(200, json=model_policy_body(), headers=H)
    api.get("/api/org/sharing").respond(200, json={"enabled": True}, headers=H)
    api.put("/api/org/sharing").respond(200, json={"enabled": False}, headers=H)
    api.get("/api/org/shares").respond(200, json={"data": []}, headers=H)
    api.delete(f"/api/org/shares/{SHARE_ID}").respond(200, json={"status": "revoked"}, headers=H)
    api.get("/api/org/sso").respond(200, json={"sso_required": False, "blockers": []}, headers=H)
    api.put("/api/org/sso").respond(
        200, json={"sso_required": True, "recovery": recovery_body()}, headers=H
    )
    api.post("/api/org/sso/recovery-code").respond(
        201, json={"recovery": recovery_body()}, headers=H
    )
    api.get("/api/org/domains").respond(200, json={"data": [domain_body()]}, headers=H)
    api.post("/api/org/domains").respond(201, json=domain_body(), headers=H)
    api.get(f"/api/org/domains/{DOMAIN}/challenge").respond(
        200,
        json={
            "record": {
                "type": "TXT",
                "host": "_matterwave",
                "name": "_matterwave.example.test",
                "value": "mw-verify=abc123",
            }
        },
        headers=H,
    )
    api.post(f"/api/org/domains/{DOMAIN}/verify").respond(
        200,
        json={
            "domain": DOMAIN,
            "verified": True,
            "verified_at": "2026-10-31T09:02:00+00:00",
            "verified_by": "user-0001",
            "method": "txt",
            "last_checked_at": "2026-10-31T09:02:00+00:00",
            "last_error": None,
            "record": {
                "type": "TXT",
                "host": "_matterwave",
                "name": "_matterwave.example.test",
                "value": "mw-verify=abc123",
            },
        },
        headers=H,
    )
    api.delete(f"/api/org/domains/{DOMAIN}").respond(204)
    api.get("/api/org/idp").respond(200, json={"data": [idp_body()]}, headers=H)
    api.post("/api/org/idp").respond(201, json=idp_body(), headers=H)
    api.delete(f"/api/org/idp/{PROVIDER}").respond(204)
    api.get("/api/org/mfa").respond(200, json={"mfa_required": True}, headers=H)
    api.put("/api/org/mfa").respond(200, json={"mfa_required": True}, headers=H)
    async with async_client as c:
        assert (await c.org.get()).role == "owner"
        assert (await c.org.rename("Acme Ltd")).name == "Acme Ltd"
        closed = await c.org.close("Acme")
        assert closed.already_closed is True and closed.identity_removed is False
        assert closed.api_keys_revoked is None  # a step that could not be confirmed
        assert len(await c.org.models()) == 2
        assert (await c.org.set_models(None)).restricted
        assert (await c.org.sharing()).enabled
        assert (await c.org.set_sharing(False)).enabled is False
        assert len(await c.org.shares()) == 0
        assert (await c.org.revoke_share(SHARE_ID)).status == "revoked"
        assert (await c.org.sso()).blockers == []
        assert (await c.org.set_sso(True)).recovery is not None
        assert (await c.org.mint_recovery_code()).recovery.id == "rc-0001"
        assert len(await c.org.domains.list()) == 1
        assert (await c.org.domains.claim(DOMAIN)).verified is False
        assert (await c.org.domains.challenge(DOMAIN)).record.host == "_matterwave"
        assert (await c.org.domains.verify(DOMAIN)).verified is True
        assert await c.org.domains.release(DOMAIN) is None
        assert (await c.org.idps.list()).data[0].kind == "oidc"
        assert (await c.org.idps.attach("Acme SSO", "https://id.example.test", "c", "s")).name
        assert await c.org.idps.detach(PROVIDER) is None
        assert (await c.org.mfa()).mfa_required is True
        assert (await c.org.set_mfa(True)).mfa_required is True
