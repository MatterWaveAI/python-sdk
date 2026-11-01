"""The auth and input families (`client.passkeys`, `client.mfa`,
`client.accounts`, `client.speech`) against the fake API: the Security
pane's passkeys and second factor, the browser's account switcher and
dictation. Each test answers with the body the route in matterwave-app
sends and checks the request the client made.
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
    NotFoundError,
)

PASSKEY_ID = "pk-0001"
SLOT = 2
H = {"X-Request-Id": REQUEST_ID}


def sent(route) -> httpx.Request:
    return route.calls.last.request


def body_of(route) -> dict:
    return json.loads(sent(route).content)


def _family_operations() -> dict[str, tuple[str, str]]:
    keep = ("passkeys", "mfa", "accounts", "speech")
    return {k: v for k, v in APP_OPERATIONS.items() if k.split(".")[0] in keep}


def test_every_auth_operation_names_a_method_on_both_clients() -> None:
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


def test_every_auth_operation_is_a_route_the_api_declares() -> None:
    routes = _api_routes()
    if not routes:
        pytest.skip("matterwave-app/api/matterwave is not beside the SDK in this checkout")
    for key, (method, path) in _family_operations().items():
        assert (method, path) in routes, f"{key}: {method} {path} is not an API route"


# --- passkeys ------------------------------------------------------------------------


def passkey_body(**extra) -> dict:
    return {
        "id": PASSKEY_ID,
        "name": "Work laptop",
        "created_at": "2026-10-31T09:00:00+00:00",
        **extra,
    }


def test_passkeys_list_carries_the_rp_id_and_the_cap(api, client: MatterWave) -> None:
    route = api.get("/api/me/passkeys").respond(
        200,
        json={"passkeys": [passkey_body()], "rp_id": "matterwave.test", "max": 10},
        headers=H,
    )
    passkeys = client.passkeys.list()
    assert sent(route).headers["authorization"] == f"Bearer {API_KEY}"
    assert len(passkeys) == 1
    assert passkeys.data[0].name == "Work laptop"
    assert passkeys.rp_id == "matterwave.test" and passkeys.max == 10


def test_passkey_start_and_verify(api, client: MatterWave) -> None:
    start = api.post("/api/me/passkeys/start").respond(
        200,
        json={"passkey_id": PASSKEY_ID, "options": {"challenge": "create-challenge"}},
        headers=H,
    )
    started = client.passkeys.start()
    assert sent(start).content == b""
    assert started.passkey_id == PASSKEY_ID
    assert started.options == {"challenge": "create-challenge"}
    verify = api.post(f"/api/me/passkeys/{PASSKEY_ID}/verify").respond(
        201, json=passkey_body(), headers=H
    )
    attestation = {"id": "abc", "rawId": "abc", "response": {}, "type": "public-key"}
    registered = client.passkeys.verify(PASSKEY_ID, attestation, "  Work   laptop ")
    assert body_of(verify) == {"credential": attestation, "name": "Work laptop"}
    assert registered.name == "Work laptop"
    with pytest.raises(ValueError, match="name"):
        client.passkeys.verify(PASSKEY_ID, attestation, "  ")
    with pytest.raises(ValueError, match="credential"):
        client.passkeys.verify(PASSKEY_ID, {}, "Work laptop")
    with pytest.raises(ValueError, match="passkey_id"):
        client.passkeys.rename("../keys", "X")


def test_passkey_rename_and_remove(api, client: MatterWave) -> None:
    rename = api.patch(f"/api/me/passkeys/{PASSKEY_ID}").respond(
        200, json=passkey_body(name="Pocket key"), headers=H
    )
    renamed = client.passkeys.rename(PASSKEY_ID, "Pocket key")
    assert body_of(rename) == {"name": "Pocket key"}
    assert renamed.name == "Pocket key"
    remove = api.delete(f"/api/me/passkeys/{PASSKEY_ID}").respond(204)
    assert client.passkeys.remove(PASSKEY_ID) is None
    assert remove.called
    api.delete(f"/api/me/passkeys/{PASSKEY_ID}").respond(
        404, json={"detail": "No such passkey."}, headers=H
    )
    with pytest.raises(NotFoundError):
        client.passkeys.remove(PASSKEY_ID)


def test_passkey_login_is_anonymous_on_both_halves(api, client: MatterWave) -> None:
    start = api.post("/api/auth/passkey/start").respond(
        200,
        json={"challenge": "sealed-opaque", "options": {"challenge": "get-challenge"}},
        headers=H,
    )
    started = client.passkeys.login_start("Ada@Example.test", "req-123")
    request = sent(start)
    assert "authorization" not in request.headers
    assert body_of(start) == {"email": "ada@example.test", "auth_request": "req-123"}
    assert started.challenge == "sealed-opaque"
    with pytest.raises(ValueError, match="email"):
        client.passkeys.login_start("nope", "req-123")
    finish = api.post("/api/auth/passkey/finish").respond(
        200, json={"redirect": "/api/auth/callback?x=1"}, headers=H
    )
    assertion = {"id": "abc", "rawId": "abc", "response": {}, "type": "public-key"}
    finished = client.passkeys.login_finish("sealed-opaque", assertion)
    assert "authorization" not in sent(finish).headers
    assert body_of(finish) == {"challenge": "sealed-opaque", "credential": assertion}
    assert finished.redirect.startswith("/api/auth/callback")
    with pytest.raises(ValueError, match="challenge"):
        client.passkeys.login_finish("", assertion)


# --- MFA ------------------------------------------------------------------------------


def test_mfa_state(api, client: MatterWave) -> None:
    route = api.get("/api/me/mfa").respond(
        200,
        json={"totp_enrolled": True, "recovery_codes_left": 7, "org_requires_mfa": False},
        headers=H,
    )
    state = client.mfa.state()
    assert state.totp_enrolled is True
    assert state.recovery_codes_left == 7
    assert state.org_requires_mfa is False
    assert route.called


def test_mfa_enrol_round_trip_shows_the_codes_once(api, client: MatterWave) -> None:
    start = api.post("/api/me/mfa/totp/start").respond(
        200,
        json={
            "secret": "JBSWY3DPEHPK3PXP",
            "uri": "otpauth://totp/MatterWave:ada?secret=JBSW",
            "qr_svg": "data:image/svg+xml;base64,abc",
        },
        headers=H,
    )
    enrolment = client.mfa.totp_start()
    assert sent(start).content == b""
    assert enrolment.secret == "JBSWY3DPEHPK3PXP"
    assert "JBSWY3DPEHPK3PXP" not in repr(enrolment)
    assert enrolment.qr_svg.startswith("data:image/svg+xml")
    confirm = api.post("/api/me/mfa/totp/confirm").respond(
        200,
        json={
            "totp_enrolled": True,
            "recovery_codes": ["1111-2222", "3333-4444"],
            "recovery_codes_left": 2,
        },
        headers=H,
    )
    confirmed = client.mfa.totp_confirm(" 123 456 ")
    assert body_of(confirm) == {"code": "123456"}
    assert confirmed.recovery_codes == ["1111-2222", "3333-4444"]
    assert "1111-2222" not in repr(confirmed)  # shown once, kept out of reprs
    with pytest.raises(ValueError, match="six digits"):
        client.mfa.totp_confirm("12ab56")


def test_mfa_remove_needs_a_proof(api, client: MatterWave) -> None:
    route = api.delete("/api/me/mfa/totp").mock(
        return_value=httpx.Response(
            200, json={"totp_enrolled": False, "recovery_codes_left": 0}, headers=H
        )
    )
    with pytest.raises(ValueError, match="prove it"):
        client.mfa.totp_remove()
    assert route.call_count == 0
    removed = client.mfa.totp_remove(code="123456")
    assert body_of(route) == {"code": "123456"}
    assert removed.totp_enrolled is False
    by_recovery = client.mfa.totp_remove(recovery_code="1111-2222")
    assert body_of(route) == {"recovery_code": "1111-2222"}
    assert by_recovery.recovery_codes_left == 0
    # An org that requires one refuses the removal outright.
    api.delete("/api/me/mfa/totp").respond(
        409,
        json={
            "detail": "Your organisation requires an authenticator on every account, so it "
            "cannot be removed."
        },
        headers=H,
    )
    from matterwave_sdk import ConflictError

    with pytest.raises(ConflictError, match="requires an authenticator"):
        client.mfa.totp_remove(code="123456")


def test_mfa_recovery_reissue(api, client: MatterWave) -> None:
    route = api.post("/api/me/mfa/recovery-codes").respond(
        200,
        json={"recovery_codes": ["5555-6666"], "recovery_codes_left": 1},
        headers=H,
    )
    reissued = client.mfa.recovery_reissue("654321")
    assert body_of(route) == {"code": "654321"}
    assert reissued.recovery_codes == ["5555-6666"]
    assert reissued.totp_enrolled is None  # the route sends no such field here
    with pytest.raises(ValueError, match="six digits"):
        client.mfa.recovery_reissue("1")


# --- account switching --------------------------------------------------------------------


def account_body(**extra) -> dict:
    return {
        "slot": 0,
        "current": True,
        "subject": "user-0001",
        "email": "ada@example.test",
        "name": "Ada",
        "org_name": "Acme",
        **extra,
    }


def test_accounts_list_offers_the_parked_ones(api, client: MatterWave) -> None:
    route = api.get("/api/accounts").respond(
        200,
        json={
            "accounts": [
                account_body(),
                account_body(slot=1, current=False, subject="user-0002", email="bob@example.test"),
            ],
            "max": 4,
            "room": True,
        },
    )
    accounts = client.accounts.list()
    assert "authorization" not in sent(route).headers  # cookies are the credential
    assert len(accounts) == 2
    assert accounts.data[0].current and accounts.data[1].slot == 1
    assert accounts.max == 4 and accounts.room is True


def test_accounts_switch_logout_and_logout_all(api, client: MatterWave) -> None:
    switch = api.post("/api/accounts/switch").respond(
        200, json={"status": "ok", "subject": "user-0002", "email": "bob@example.test"}
    )
    switched = client.accounts.switch(SLOT)
    assert body_of(switch) == {"slot": 2}
    assert switched.subject == "user-0002"
    with pytest.raises(ValueError, match="slot"):
        client.accounts.switch(0)  # slot 0 is already acting
    logout = api.post(f"/api/accounts/{SLOT}/logout").respond(200, json={"status": "ok"})
    assert client.accounts.logout(SLOT).status == "ok"
    assert sent(logout).url.path == f"/api/accounts/{SLOT}/logout"
    logout_all = api.post("/api/accounts/logout-all").respond(
        200,
        json={"status": "ok", "end_session_endpoint": "https://id.test/oidc/v1/end_session"},
    )
    signed_out = client.accounts.logout_all()
    assert signed_out.end_session_endpoint is not None
    assert logout_all.called


# --- speech ---------------------------------------------------------------------------------


def test_speech_transcribe_posts_multipart_with_the_language_hint(api, client: MatterWave) -> None:
    route = api.post("/api/speech/transcribe").respond(
        200, json={"text": "Hello from the dictation test."}, headers=H
    )
    clip = b"\x1aE\xdf\xa3webm-bytes"
    result = client.speech.transcribe(clip, "clip.webm", language="en-GB")
    request = sent(route)
    assert request.headers["authorization"] == f"Bearer {API_KEY}"
    assert "multipart/form-data" in request.headers["content-type"]
    assert b"clip.webm" in request.content
    assert b"en" in request.content  # the hint goes, main subtag only is the server's job
    assert result.text == "Hello from the dictation test."
    assert result.request_id == REQUEST_ID


def test_speech_refuses_unknown_media_and_bad_language_here(api, client: MatterWave) -> None:
    route = api.post("/api/speech/transcribe").respond(200, json={"text": "unreachable"}, headers=H)
    with pytest.raises(ValueError, match="content_type"):
        client.speech.transcribe(b"x", "clip.txt", content_type="text/plain")
    with pytest.raises(ValueError, match="language"):
        client.speech.transcribe(b"x", "clip.webm", language="x" * 40)
    assert route.call_count == 0


# --- async ------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_async_auth_families_make_the_same_requests(
    api, async_client: AsyncMatterWave
) -> None:
    api.get("/api/me/passkeys").respond(
        200,
        json={"passkeys": [passkey_body()], "rp_id": "matterwave.test", "max": 10},
        headers=H,
    )
    api.post("/api/me/passkeys/start").respond(
        200, json={"passkey_id": PASSKEY_ID, "options": {}}, headers=H
    )
    api.post(f"/api/me/passkeys/{PASSKEY_ID}/verify").respond(201, json=passkey_body(), headers=H)
    api.patch(f"/api/me/passkeys/{PASSKEY_ID}").respond(
        200, json=passkey_body(name="Renamed"), headers=H
    )
    api.delete(f"/api/me/passkeys/{PASSKEY_ID}").respond(204)
    api.post("/api/auth/passkey/start").respond(
        200, json={"challenge": "sealed", "options": {}}, headers=H
    )
    api.post("/api/auth/passkey/finish").respond(
        200, json={"redirect": "/api/auth/callback"}, headers=H
    )
    api.get("/api/me/mfa").respond(
        200,
        json={"totp_enrolled": False, "recovery_codes_left": 0, "org_requires_mfa": True},
        headers=H,
    )
    api.post("/api/me/mfa/totp/start").respond(
        200,
        json={"secret": "S", "uri": "otpauth://x", "qr_svg": "data:image/svg+xml;base64,x"},
        headers=H,
    )
    api.post("/api/me/mfa/totp/confirm").respond(
        200,
        json={"totp_enrolled": True, "recovery_codes": ["1-2"], "recovery_codes_left": 1},
        headers=H,
    )
    api.delete("/api/me/mfa/totp").respond(
        200, json={"totp_enrolled": False, "recovery_codes_left": 0}, headers=H
    )
    api.post("/api/me/mfa/recovery-codes").respond(
        200, json={"recovery_codes": ["3-4"], "recovery_codes_left": 1}, headers=H
    )
    api.get("/api/accounts").respond(
        200, json={"accounts": [account_body()], "max": 4, "room": False}
    )
    api.post("/api/accounts/switch").respond(
        200, json={"status": "ok", "subject": "user-0002", "email": "b@x.test"}
    )
    api.post(f"/api/accounts/{SLOT}/logout").respond(200, json={"status": "ok"})
    api.post("/api/accounts/logout-all").respond(200, json={"status": "ok"})
    api.post("/api/speech/transcribe").respond(200, json={"text": "words"}, headers=H)
    async with async_client as c:
        assert len(await c.passkeys.list()) == 1
        assert (await c.passkeys.start()).passkey_id == PASSKEY_ID
        assert (
            await c.passkeys.verify(PASSKEY_ID, {"id": "a", "type": "public-key"}, "Key")
        ).name == "Work laptop"
        assert (await c.passkeys.rename(PASSKEY_ID, "Renamed")).name == "Renamed"
        assert await c.passkeys.remove(PASSKEY_ID) is None
        assert (await c.passkeys.login_start("a@x.test", "r")).challenge == "sealed"
        assert (await c.passkeys.login_finish("sealed", {"id": "a"})).redirect
        state = await c.mfa.state()
        assert state.org_requires_mfa is True and state.totp_enrolled is False
        assert (await c.mfa.totp_start()).uri.startswith("otpauth://")
        confirmed = await c.mfa.totp_confirm("123456")
        assert confirmed.recovery_codes == ["1-2"]
        assert (await c.mfa.totp_remove(code="123456")).totp_enrolled is False
        assert (await c.mfa.recovery_reissue("123456")).recovery_codes == ["3-4"]
        accounts = await c.accounts.list()
        assert accounts.room is False and len(accounts) == 1
        assert (await c.accounts.switch(SLOT)).email == "b@x.test"
        assert (await c.accounts.logout(SLOT)).status == "ok"
        assert (await c.accounts.logout_all()).status == "ok"
        assert (await c.speech.transcribe(b"x", "clip.webm")).text == "words"
