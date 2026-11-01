"""The organisation clients against the fake API, signed in as a person:
`org` (with `org.domains` and `org.idps`), `members`, `invites`,
`invite_links`, billing administration and the device-login decisions.
Reads first, then writes; each test answers with the body the platform's
route sends and checks the request the client made and what it parsed."""

from __future__ import annotations

import json

import pytest
from conftest import BASE_URL, REQUEST_ID

from matterwave_sdk import AsyncMatterWave, ConflictError, MatterWave

H = {"X-Request-Id": REQUEST_ID}
TOKEN = "person-token"
MEMBER = {
    "subject": "u-2",
    "email": "b@example.test",
    "name": None,
    "role": "member",
    "state": "active",
    "created_at": "2026-10-01T00:00:00+00:00",
    "last_seen_at": None,
}
INVITE = {
    "id": "inv-1",
    "email": "c@example.test",
    "role": "member",
    "invited_by": "u-1",
    "created_at": "2026-10-31T00:00:00+00:00",
    "expires_at": "2026-11-07T00:00:00+00:00",
    "status": "pending",
}
LINK = {
    "id": "lnk-1",
    "role": "member",
    "max_uses": 10,
    "uses": 0,
    "created_by": "u-1",
    "created_at": "2026-10-31T00:00:00+00:00",
    "expires_at": "2026-11-07T00:00:00+00:00",
    "status": "active",
}
RECOVERY = {
    "id": "rc-1",
    "created_at": "2026-10-31T00:00:00+00:00",
    "expires_at": "2026-11-03T00:00:00+00:00",
    "created_by": "u-1",
    "used_at": None,
    "code": "BREAK-GLASS-SECRET",
    "shown_once": True,
}
DOMAIN = {
    "domain": "example.test",
    "verified": False,
    "verified_at": None,
    "verified_by": None,
    "created_at": "2026-10-31T00:00:00+00:00",
}
RECORD = {
    "type": "TXT",
    "host": "_matterwave-verify",
    "name": "_matterwave-verify.example.test",
    "value": "matterwave-domain-verification=abc",
}


@pytest.fixture()
def person() -> MatterWave:
    return MatterWave(access_token=TOKEN, base_url=BASE_URL)


def sent(route) -> dict:
    return json.loads(route.calls.last.request.content)


# --- reads ---------------------------------------------------------------------


def test_the_org_and_its_members_and_invites(api, person: MatterWave) -> None:
    org = api.get("/api/org").respond(
        200,
        json={"id": "org-1", "name": "Acme", "created_at": "2026-01-01", "role": "owner"},
        headers=H,
    )
    api.get("/api/org/members").respond(
        200, json={"data": [MEMBER], "role": "owner", "seats": {"used": 2, "max": 5, "billed": 5}}
    )
    api.get("/api/org/invites").respond(200, json={"data": [INVITE]})
    api.get("/api/org/invite-links").respond(200, json={"data": [LINK]})
    assert person.org.get().name == "Acme"
    assert org.calls.last.request.headers["authorization"] == f"Bearer {TOKEN}"
    members = person.members.list()
    assert [m.email for m in members] == ["b@example.test"]
    assert (members.role, members.seats.used, members.seats.max) == ("owner", 2, 5)
    assert [i.status for i in person.invites.list()] == ["pending"]
    links = person.invite_links.list()
    assert links.data[0].token is None and links.data[0].uses == 0


def test_the_org_policies(api, person: MatterWave) -> None:
    api.get("/api/org/models").respond(
        200,
        json={
            "house_model": "grt-nano",
            "data": [
                {
                    "id": "grt-nano",
                    "label": "GRT Nano",
                    "partner": False,
                    "context_window": 32768,
                    "research_preview": False,
                    "allowed": True,
                    "locked": True,
                }
            ],
            "unlisted": [],
            "restricted": False,
        },
    )
    api.get("/api/org/sharing").respond(200, json={"enabled": True})
    api.get("/api/org/shares").respond(
        200,
        json={
            "data": [
                {
                    "id": "sh-1",
                    "conversation_id": "c-1",
                    "title": "Plan",
                    "message_count": 4,
                    "include_attachments": False,
                    "created_at": "2026-10-01",
                    "expires_at": None,
                    "revoked_at": None,
                    "view_count": 3,
                    "status": "active",
                    "created_by": "u-2",
                }
            ]
        },
    )
    api.get("/api/org/sso").respond(200, json={"sso_required": False, "blockers": ["no idp"]})
    api.get("/api/org/mfa").respond(200, json={"mfa_required": True})
    policy = person.org.models()
    assert policy.data[0].locked and not policy.restricted
    assert person.org.sharing().enabled
    assert person.org.shares().data[0].view_count == 3
    sso = person.org.sso()
    assert (sso.sso_required, sso.blockers, sso.recovery) == (False, ["no idp"], None)
    assert person.org.mfa().mfa_required


def test_domains_idps_and_billing_reads(api, person: MatterWave) -> None:
    api.get("/api/org/domains").respond(200, json={"data": [DOMAIN]})
    api.get("/api/org/domains/example.test/challenge").respond(200, json={"record": RECORD})
    api.get("/api/org/idp").respond(
        200,
        json={
            "data": [
                {
                    "provider": "okta",
                    "kind": "oidc",
                    "name": "Okta",
                    "issuer": "https://acme.okta.test",
                    "created_at": "2026-10-01",
                }
            ]
        },
    )
    api.get("/api/billing/invoices").respond(
        200,
        json={
            "data": [
                {
                    "id": "in_1",
                    "number": "A-1",
                    "status": "paid",
                    "collection_method": "charge_automatically",
                    "currency": "usd",
                    "amount_due_micros": 49_000_000,
                    "amount_remaining_micros": 0,
                    "due_at": None,
                    "paid_at": "2026-10-02",
                    "hosted_invoice_url": "https://invoice.example.test/in_1",
                    "invoice_pdf": None,
                    "issued_at": "2026-10-01",
                    "dispute_status": None,
                }
            ]
        },
    )
    api.get("/api/plan/history").respond(
        200,
        json={
            "data": [
                {
                    "from_plan": None,
                    "to_plan": "team",
                    "from_status": None,
                    "to_status": "active",
                    "kind": "checkout",
                    "actor": "stripe:evt_1",
                    "reason": None,
                    "metadata": {"seats": 5},
                    "created_at": "2026-10-01",
                }
            ]
        },
    )
    assert [d.domain for d in person.org.domains.list()] == ["example.test"]
    assert person.org.domains.challenge("example.test").record.host == "_matterwave-verify"
    assert person.org.idps.list().data[0].provider == "okta"
    assert person.billing.invoices().data[0].amount_due_micros == 49_000_000
    assert person.billing.plan_history().data[0].metadata == {"seats": 5}


def test_device_lookup(api, person: MatterWave) -> None:
    route = api.post("/api/auth/device/lookup").respond(
        200, json={"user_code": "ABCD-EFGH", "client_name": "matterwave-tui", "expires_in": 480}
    )
    found = person.auth.device.lookup("abcdefgh")
    assert (found.client_name, found.expires_in) == ("matterwave-tui", 480)
    assert sent(route) == {"user_code": "abcdefgh"}


# --- writes --------------------------------------------------------------------


def test_org_rename_and_close(api, person: MatterWave) -> None:
    rename = api.patch("/api/org").respond(
        200, json={"id": "org-1", "name": "Acme Ltd", "created_at": "x", "role": "owner"}
    )
    close = api.post("/api/org/close").respond(
        200,
        json={
            "status": "closed",
            "closed_at": "2026-10-31",
            "already_closed": False,
            "conversations_deleted": 12,
            "retention_days": 30,
            "api_keys_revoked": 2,
            "invites_revoked": 1,
            "invite_links_revoked": 0,
            "subscription": "cancelled",
            "members_mailed": 3,
            "members_not_mailed": 0,
            "identity_removed": True,
        },
    )
    assert person.org.rename("Acme Ltd").name == "Acme Ltd"
    assert sent(rename) == {"name": "Acme Ltd"}
    closed = person.org.close(confirm="Acme Ltd")
    assert sent(close) == {"confirm": "Acme Ltd"}
    assert (closed.conversations_deleted, closed.subscription) == (12, "cancelled")
    with pytest.raises(ValueError):
        person.org.rename("  ")


def test_member_writes(api, person: MatterWave) -> None:
    role = api.put("/api/org/members/u-2/role").respond(200, json={**MEMBER, "role": "admin"})
    api.post("/api/org/members/u-2/transfer-ownership").respond(
        200, json={**MEMBER, "role": "owner"}
    )
    remove = api.delete("/api/org/members/u-2").respond(204)
    assert person.members.set_role("u-2", "admin").role == "admin"
    assert sent(role) == {"role": "admin"}
    assert person.members.transfer_ownership("u-2").role == "owner"
    assert person.members.remove("u-2") is None and remove.called
    with pytest.raises(ValueError, match="role must be one of"):
        person.members.set_role("u-2", "superuser")


def test_last_owner_is_the_servers_conflict(api, person: MatterWave) -> None:
    api.put("/api/org/members/u-1/role").respond(
        409, json={"detail": "An organization must keep at least one owner."}
    )
    with pytest.raises(ConflictError, match="at least one owner"):
        person.members.set_role("u-1", "member")


def test_invites_create_resend_revoke_lookup_and_accept(api, person: MatterWave) -> None:
    create = api.post("/api/org/invites").respond(201, json={**INVITE, "mail": "sent"})
    api.post("/api/org/invites/inv-1/resend").respond(
        200, json={**INVITE, "mail": "failed", "detail": "resend it"}
    )
    revoke = api.delete("/api/org/invites/inv-1").respond(204)
    lookup = api.post("/api/org/invites/lookup").respond(
        200,
        json={
            "org_name": "Acme",
            "email": "c@example.test",
            "role": "member",
            "expires_at": "x",
            "status": "pending",
        },
    )
    accept = api.post("/api/org/invites/accept").respond(200, json=MEMBER)
    invite = person.invites.create("c@example.test", role="viewer")
    assert sent(create) == {"email": "c@example.test", "role": "viewer"}
    assert invite.mail == "sent"
    resent = person.invites.resend("inv-1")
    assert (resent.mail, resent.detail) == ("failed", "resend it")
    person.invites.revoke("inv-1")
    assert revoke.called
    assert MatterWave(api_key=None, base_url=BASE_URL).invites.lookup("tok").org_name == "Acme"
    assert "authorization" not in lookup.calls.last.request.headers
    assert person.invites.accept("tok").subject == "u-2"
    assert sent(accept) == {"token": "tok"}
    with pytest.raises(ValueError):
        person.invites.create("not-an-email")


def test_invite_links_create_shows_the_token_once_and_joins(api, person: MatterWave) -> None:
    create = api.post("/api/org/invite-links").respond(
        201, json={**LINK, "token": "link-secret", "url": "https://app.test/join?link=link-secret"}
    )
    api.delete("/api/org/invite-links/lnk-1").respond(204)
    lookup = api.post("/api/join/lookup").respond(
        200, json={"org_name": "Acme", "role": "member", "expires_at": "x", "status": "active"}
    )
    join = api.post("/api/join").respond(200, json=MEMBER)
    link = person.invite_links.create(role="viewer", max_uses=3, expires_in_days=2)
    assert sent(create) == {"role": "viewer", "max_uses": 3, "expires_in_days": 2}
    assert link.token == "link-secret" and "link-secret" not in repr(link)
    person.invite_links.revoke("lnk-1")
    assert MatterWave(api_key=None, base_url=BASE_URL).invite_links.lookup("t").status == "active"
    assert "authorization" not in lookup.calls.last.request.headers
    assert person.invite_links.join("t").role == "member"
    assert join.calls.last.request.headers["authorization"] == f"Bearer {TOKEN}"
    with pytest.raises(ValueError, match="never granted by link"):
        person.invite_links.create(role="owner")
    with pytest.raises(ValueError, match="max_uses"):
        person.invite_links.create(max_uses=0)


def test_policy_writes(api, person: MatterWave) -> None:
    models = api.put("/api/org/models").respond(
        200, json={"house_model": "grt-nano", "data": [], "unlisted": [], "restricted": True}
    )
    sharing = api.put("/api/org/sharing").respond(200, json={"enabled": False})
    api.delete("/api/org/shares/sh-1").respond(200, json={"status": "revoked"})
    sso = api.put("/api/org/sso").respond(
        200, json={"sso_required": True, "blockers": [], "recovery": RECOVERY}
    )
    api.post("/api/org/sso/recovery-code").respond(201, json={"recovery": RECOVERY})
    mfa = api.put("/api/org/mfa").respond(200, json={"mfa_required": True})
    assert person.org.set_models(["grt-nano", "grt-pro"]).restricted
    assert sent(models) == {"allowed": ["grt-nano", "grt-pro"]}
    person.org.set_models(None)
    assert sent(models) == {"allowed": None}
    assert person.org.set_sharing(False).enabled is False and sent(sharing) == {"enabled": False}
    assert person.org.revoke_share("sh-1").status == "revoked"
    enforced = person.org.set_sso(True)
    assert sent(sso) == {"sso_required": True}
    assert enforced.recovery is not None and enforced.recovery.code == "BREAK-GLASS-SECRET"
    assert "BREAK-GLASS-SECRET" not in repr(enforced)
    assert person.org.mint_recovery_code().recovery.shown_once
    assert person.org.set_mfa(True).mfa_required and sent(mfa) == {"mfa_required": True}


def test_sso_that_would_lock_the_org_out_is_a_conflict(api, person: MatterWave) -> None:
    api.put("/api/org/sso").respond(
        409,
        json={
            "detail": {
                "msg": "Single sign-on cannot be enforced yet: it would lock this organisation out.",
                "blockers": ["no verified domain"],
            }
        },
    )
    with pytest.raises(ConflictError) as caught:
        person.org.set_sso(True)
    assert caught.value.body["detail"]["blockers"] == ["no verified domain"]


def test_domain_and_idp_writes(api, person: MatterWave) -> None:
    claim = api.post("/api/org/domains").respond(201, json=DOMAIN)
    api.post("/api/org/domains/example.test/verify").respond(
        202,
        json={
            **DOMAIN,
            "method": None,
            "last_checked_at": "2026-10-31",
            "last_error": None,
            "record": RECORD,
            "detail": "The TXT record was not found yet.",
            "found": ["v=spf1"],
        },
    )
    release = api.delete("/api/org/domains/example.test").respond(204)
    attach = api.post("/api/org/idp").respond(
        201,
        json={
            "provider": "okta",
            "kind": "oidc",
            "name": "Okta",
            "issuer": "https://acme.okta.test",
            "created_at": "x",
        },
    )
    detach = api.delete("/api/org/idp/okta").respond(204)
    assert person.org.domains.claim("example.test").verified is False
    assert sent(claim) == {"domain": "example.test"}
    pending = person.org.domains.verify("example.test")
    assert (pending.verified, pending.found) == (False, ["v=spf1"])
    assert pending.record is not None and pending.record.value.startswith("matterwave-domain")
    person.org.domains.release("example.test")
    assert release.called
    provider = person.org.idps.attach(
        name="Okta", issuer="https://acme.okta.test", client_id="cid", client_secret="sec"
    )
    assert provider.provider == "okta"
    assert sent(attach) == {
        "kind": "oidc",
        "name": "Okta",
        "issuer": "https://acme.okta.test",
        "client_id": "cid",
        "client_secret": "sec",
    }
    person.org.idps.detach("okta")
    assert detach.called
    with pytest.raises(ValueError, match="https"):
        person.org.idps.attach(name="x", issuer="http://idp", client_id="c", client_secret="s")


def test_billing_writes(api, person: MatterWave) -> None:
    checkout = api.post("/api/billing/checkout").respond(
        200, json={"url": "https://checkout.stripe.test/s/1"}
    )
    api.post("/api/billing/portal").respond(200, json={"url": "https://billing.stripe.test/p/1"})
    plan = api.put("/api/plan").respond(200, json={"org_id": "org-1", "plan": "free"})
    assert person.billing.checkout("pro", annual=True).url.endswith("/s/1")
    assert sent(checkout) == {"plan": "pro", "annual": True}
    assert person.billing.portal().url.endswith("/p/1")
    assert person.billing.change_plan("free", reason="winding down").plan == "free"
    assert sent(plan) == {"plan": "free", "reason": "winding down"}


def test_device_approve_and_deny(api, person: MatterWave) -> None:
    approve = api.post("/api/auth/device/approve").respond(
        200,
        json={
            "status": "approved",
            "client_name": "matterwave-tui",
            "key": {
                "id": "key-9",
                "prefix": "mw_dev",
                "name": "Device: laptop",
                "kind": "device",
                "scopes": ["chat:read", "chat:write"],
                "created_at": "2026-10-31",
            },
        },
    )
    api.post("/api/auth/device/deny").respond(200, json={"status": "denied", "client_name": None})
    decided = person.auth.device.approve("ABCD-EFGH", name="laptop")
    assert sent(approve) == {"user_code": "ABCD-EFGH", "name": "laptop"}
    assert decided.key is not None and decided.key.kind == "device" and decided.key.key is None
    assert person.auth.device.deny("ABCD-EFGH").status == "denied"


@pytest.mark.asyncio
async def test_the_async_clients_match(api) -> None:
    person = AsyncMatterWave(access_token=TOKEN, base_url=BASE_URL)
    api.get("/api/org/members").respond(
        200,
        json={"data": [MEMBER], "role": "admin", "seats": {"used": 1, "max": None, "billed": 1}},
    )
    create = api.post("/api/org/invites").respond(201, json={**INVITE, "mail": "sent"})
    api.post("/api/org/domains/example.test/verify").respond(200, json={**DOMAIN, "verified": True})
    api.get("/api/billing/invoices").respond(200, json={"data": []})
    api.post("/api/auth/device/deny").respond(200, json={"status": "denied", "client_name": None})
    members = await person.members.list()
    assert members.seats.max is None and len(members) == 1
    await person.invites.create("c@example.test")
    assert sent(create) == {"email": "c@example.test", "role": "member"}
    assert (await person.org.domains.verify("example.test")).verified
    assert len(await person.billing.invoices()) == 0
    assert (await person.auth.device.deny("X")).status == "denied"
