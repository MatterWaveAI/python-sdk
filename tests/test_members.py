"""The members resource (`client.members`, with `invites` and `links`)
against the fake API: the org pane's people. Each test answers with the
body the route in matterwave-app sends and checks the request the client
made (method, path, body) and what it parsed out of the answer.
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
)

SUBJECT = "user-0001"
INVITE_ID = "inv-0001"
LINK_ID = "lnk-0001"
H = {"X-Request-Id": REQUEST_ID}


def sent(route) -> httpx.Request:
    return route.calls.last.request


def body_of(route) -> dict:
    import json

    return json.loads(sent(route).content)


def _family_operations() -> dict[str, tuple[str, str]]:
    return {k: v for k, v in APP_OPERATIONS.items() if k.split(".")[0] == "members"}


def test_every_members_operation_names_a_method_on_both_clients() -> None:
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


def test_every_members_operation_is_a_route_the_api_declares() -> None:
    routes = _api_routes()
    if not routes:
        pytest.skip("matterwave-app/api/matterwave is not beside the SDK in this checkout")
    for key, (method, path) in _family_operations().items():
        assert (method, path) in routes, f"{key}: {method} {path} is not an API route"


# --- members ----------------------------------------------------------------------


def member_body(**extra) -> dict:
    return {
        "subject": SUBJECT,
        "email": "ada@example.test",
        "name": "Ada",
        "role": "owner",
        "state": "active",
        "created_at": "2026-10-01T09:00:00+00:00",
        "last_seen_at": "2026-10-31T08:00:00+00:00",
        **extra,
    }


def members_body(**extra) -> dict:
    return {
        "data": [
            member_body(),
            member_body(
                subject="user-0002", role="member", name=None, last_seen_at=None
            ),
        ],
        "role": "owner",
        "seats": {"used": 3, "max": 5, "billed": 2},
        **extra,
    }


def test_members_list_carries_the_callers_role_and_the_seats(
    api, client: MatterWave
) -> None:
    route = api.get("/api/org/members").respond(200, json=members_body(), headers=H)
    members = client.members.list()
    assert sent(route).headers["authorization"] == f"Bearer {API_KEY}"
    assert members.role == "owner"
    assert len(members) == 2
    assert members.data[1].name is None and members.data[1].last_seen_at is None
    assert members.seats.used == 3 and members.seats.max == 5
    assert members.seats.billed == 2  # a per-seat plan's paying rows


def test_set_role_sends_the_role_and_refuses_an_unknown_one(
    api, client: MatterWave
) -> None:
    route = api.put(f"/api/org/members/{SUBJECT}/role").respond(
        200, json=member_body(role="admin"), headers=H
    )
    updated = client.members.set_role(SUBJECT, "admin")
    assert body_of(route) == {"role": "admin"}
    assert updated.role == "admin"
    for bad in ("root", "Owner"):
        with pytest.raises(ValueError, match="role must be one of"):
            client.members.set_role(SUBJECT, bad)
    with pytest.raises(ValueError, match="subject"):
        client.members.set_role("../keys", "member")
    assert route.call_count == 1


def test_transfer_ownership_is_one_step(api, client: MatterWave) -> None:
    route = api.post(f"/api/org/members/{SUBJECT}/transfer-ownership").respond(
        200, json=member_body(role="owner"), headers=H
    )
    transferred = client.members.transfer_ownership(SUBJECT)
    assert sent(route).content == b""
    assert transferred.role == "owner"


def test_remove_member_is_a_delete_answered_204(api, client: MatterWave) -> None:
    route = api.delete(f"/api/org/members/{SUBJECT}").respond(204)
    assert client.members.remove(SUBJECT) is None
    assert route.called


# --- invites ------------------------------------------------------------------------


def invite_body(**extra) -> dict:
    return {
        "id": INVITE_ID,
        "email": "bob@example.test",
        "role": "member",
        "invited_by": SUBJECT,
        "created_at": "2026-10-31T09:00:00+00:00",
        "expires_at": "2026-11-07T09:00:00+00:00",
        "status": "pending",
        **extra,
    }


def test_invites_list_statuses(api, client: MatterWave) -> None:
    route = api.get("/api/org/invites").respond(
        200, json={"data": [invite_body(), invite_body(id="inv-0002", status="accepted")]},
        headers=H,
    )
    invites = client.members.invites.list()
    assert [i.status for i in invites] == ["pending", "accepted"]
    assert invites.data[0].invited_by == SUBJECT
    assert route.called


def test_invite_create_mails_and_says_whether_it_went(api, client: MatterWave) -> None:
    route = api.post("/api/org/invites").respond(
        201, json=invite_body(mail="sent"), headers=H
    )
    sent_invite = client.members.invites.create("Bob@Example.test", role="admin")
    assert body_of(route) == {"email": "bob@example.test", "role": "admin"}
    assert sent_invite.mail == "sent" and sent_invite.detail is None
    # A relay failure does not undo the row; the answer says so.
    api.post("/api/org/invites").respond(
        201,
        json=invite_body(
            mail="failed",
            detail="The invite was created but the mail could not be sent; resend it.",
        ),
        headers=H,
    )
    unsent = client.members.invites.create("carol@example.test")
    assert unsent.mail == "failed" and "resend" in (unsent.detail or "")
    with pytest.raises(ValueError, match="email"):
        client.members.invites.create("nope")


def test_invite_resend_rotates_and_revokes(api, client: MatterWave) -> None:
    resend = api.post(f"/api/org/invites/{INVITE_ID}/resend").respond(
        200, json=invite_body(mail="sent"), headers=H
    )
    assert client.members.invites.resend(INVITE_ID).mail == "sent"
    assert sent(resend).content == b""
    revoke = api.delete(f"/api/org/invites/{INVITE_ID}").respond(204)
    assert client.members.invites.revoke(INVITE_ID) is None
    assert revoke.called
    api.delete(f"/api/org/invites/{INVITE_ID}").respond(
        404, json={"detail": "No such invite."}, headers=H
    )
    with pytest.raises(NotFoundError):
        client.members.invites.revoke(INVITE_ID)


def test_invite_lookup_is_anonymous_and_accept_is_keyed(
    api, client: MatterWave
) -> None:
    lookup = api.post("/api/org/invites/lookup").respond(
        200,
        json={
            "org_name": "Acme",
            "email": "bob@example.test",
            "role": "member",
            "expires_at": "2026-11-07T09:00:00+00:00",
            "status": "pending",
        },
        headers=H,
    )
    preview = client.members.invites.lookup("tok-abc")
    request = sent(lookup)
    assert "authorization" not in request.headers
    assert body_of(lookup) == {"token": "tok-abc"}
    assert preview.org_name == "Acme" and preview.status == "pending"
    accept = api.post("/api/org/invites/accept").respond(
        200, json=member_body(role="member"), headers=H
    )
    membership = client.members.invites.accept("tok-abc")
    assert sent(accept).headers["authorization"] == f"Bearer {API_KEY}"
    assert membership.email == "ada@example.test"


# --- invite links ------------------------------------------------------------------------


def link_body(**extra) -> dict:
    return {
        "id": LINK_ID,
        "role": "member",
        "max_uses": 10,
        "uses": 3,
        "created_by": SUBJECT,
        "created_at": "2026-10-31T09:00:00+00:00",
        "expires_at": "2026-11-07T09:00:00+00:00",
        "status": "active",
        **extra,
    }


def test_links_list_never_carries_a_token(api, client: MatterWave) -> None:
    route = api.get("/api/org/invite-links").respond(
        200, json={"data": [link_body(), link_body(id="lnk-0002", status="used_up")]},
        headers=H,
    )
    links = client.members.links.list()
    assert route.called
    assert [link.status for link in links] == ["active", "used_up"]
    assert all("token" not in str(link) for link in links)


def test_link_create_hands_the_token_once_and_never_for_owner(
    api, client: MatterWave
) -> None:
    route = api.post("/api/org/invite-links").respond(
        201,
        json={**link_body(), "token": "link-tok", "url": "https://x.test/join?link=link-tok"},
        headers=H,
    )
    created = client.members.links.create(role="viewer", max_uses=25, expires_in_days=30)
    assert body_of(route) == {
        "role": "viewer",
        "max_uses": 25,
        "expires_in_days": 30,
    }
    assert created.token == "link-tok"
    assert "link-tok" not in repr(created)
    assert created.url.endswith("/join?link=link-tok")
    with pytest.raises(ValueError, match="owner"):
        client.members.links.create(role="owner")
    with pytest.raises(ValueError, match="max_uses"):
        client.members.links.create(max_uses=501)
    with pytest.raises(ValueError, match="expires_in_days"):
        client.members.links.create(expires_in_days=31)
    assert route.call_count == 1


def test_link_revoke(api, client: MatterWave) -> None:
    route = api.delete(f"/api/org/invite-links/{LINK_ID}").respond(204)
    assert client.members.links.revoke(LINK_ID) is None
    assert route.called


def test_join_lookup_and_join(api, client: MatterWave) -> None:
    lookup = api.post("/api/join/lookup").respond(
        200,
        json={
            "org_name": "Acme",
            "role": "member",
            "expires_at": "2026-11-07T09:00:00+00:00",
            "status": "active",
        },
        headers=H,
    )
    preview = client.members.join_lookup("link-tok")
    assert "authorization" not in sent(lookup).headers
    assert preview.role == "member"
    join = api.post("/api/join").respond(200, json=member_body(), headers=H)
    membership = client.members.join("link-tok")
    assert sent(join).headers["authorization"] == f"Bearer {API_KEY}"
    assert membership.role == "owner"
    # An expired or used-up link is the 410 the route answers.
    api.post("/api/join").respond(
        410, json={"detail": "That link has been used up."}, headers=H
    )
    from matterwave_sdk import MatterWaveError

    with pytest.raises(MatterWaveError, match="used up"):
        client.members.join("link-tok")


def test_role_validation_covers_the_whole_matrix(api, client: MatterWave) -> None:
    route = api.put(f"/api/org/members/{SUBJECT}/role").respond(
        200, json=member_body(role="viewer"), headers=H
    )
    for role in ("owner", "admin", "member", "viewer"):
        client.members.set_role(SUBJECT, role)
    assert route.call_count == 4
    # A role outside the matrix is refused here, before the request is
    # spent -- the server's 422 never gets the chance.
    with pytest.raises(ValueError, match="role must be one of"):
        client.members.set_role(SUBJECT, "guest")
    assert route.call_count == 4


# --- async ---------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_async_members_makes_the_same_requests(api, async_client: AsyncMatterWave) -> None:
    api.get("/api/org/members").respond(200, json=members_body(), headers=H)
    api.put(f"/api/org/members/{SUBJECT}/role").respond(
        200, json=member_body(role="admin"), headers=H
    )
    api.post(f"/api/org/members/{SUBJECT}/transfer-ownership").respond(
        200, json=member_body(role="owner"), headers=H
    )
    api.delete(f"/api/org/members/{SUBJECT}").respond(204)
    api.get("/api/org/invites").respond(200, json={"data": [invite_body()]}, headers=H)
    api.post("/api/org/invites").respond(201, json=invite_body(mail="sent"), headers=H)
    api.post(f"/api/org/invites/{INVITE_ID}/resend").respond(
        200, json=invite_body(mail="sent"), headers=H
    )
    api.delete(f"/api/org/invites/{INVITE_ID}").respond(204)
    api.post("/api/org/invites/lookup").respond(
        200,
        json={
            "org_name": "Acme",
            "email": "bob@example.test",
            "role": "member",
            "expires_at": "2026-11-07T09:00:00+00:00",
            "status": "pending",
        },
        headers=H,
    )
    api.post("/api/org/invites/accept").respond(200, json=member_body(), headers=H)
    api.get("/api/org/invite-links").respond(200, json={"data": [link_body()]}, headers=H)
    api.post("/api/org/invite-links").respond(
        201, json={**link_body(), "token": "t", "url": "https://x.test/join?link=t"}, headers=H
    )
    api.delete(f"/api/org/invite-links/{LINK_ID}").respond(204)
    api.post("/api/join/lookup").respond(
        200,
        json={
            "org_name": "Acme",
            "role": "member",
            "expires_at": "2026-11-07T09:00:00+00:00",
            "status": "active",
        },
        headers=H,
    )
    api.post("/api/join").respond(200, json=member_body(), headers=H)
    async with async_client as c:
        assert (await c.members.list()).seats.used == 3
        assert (await c.members.set_role(SUBJECT, "admin")).role == "admin"
        assert (await c.members.transfer_ownership(SUBJECT)).role == "owner"
        assert await c.members.remove(SUBJECT) is None
        assert len(await c.members.invites.list()) == 1
        assert (await c.members.invites.create("bob@example.test")).mail == "sent"
        assert (await c.members.invites.resend(INVITE_ID)).mail == "sent"
        assert await c.members.invites.revoke(INVITE_ID) is None
        assert (await c.members.invites.lookup("tok")).status == "pending"
        assert (await c.members.invites.accept("tok")).state == "active"
        assert len(await c.members.links.list()) == 1
        assert (await c.members.links.create()).token == "t"
        assert await c.members.links.revoke(LINK_ID) is None
        assert (await c.members.join_lookup("tok")).org_name == "Acme"
        assert (await c.members.join("tok")).role == "owner"
