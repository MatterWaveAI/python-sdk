"""A person's token in place of an API key: `access_token=` static or as a
callable, and `client.auth.person`, the device-code sign-in at the identity
provider with the refresh that keeps the token alive."""

from __future__ import annotations

from urllib.parse import parse_qs

import httpx
import pytest
import respx
from conftest import BASE_URL, REQUEST_ID

from matterwave_sdk import (
    CREDENTIALS,
    APIError,
    AsyncMatterWave,
    DeviceAuthError,
    MatterWave,
    MatterWaveError,
    MissingAPIKeyError,
    PersonToken,
)

ISSUER = "https://auth.matterwave.test"
DEVICE_ENDPOINT = f"{ISSUER}/oauth/v2/device_authorization"
TOKEN_ENDPOINT = f"{ISSUER}/oauth/v2/token"
NATIVE_CLIENT = "native-client-1"
AUDIENCE = "urn:zitadel:iam:org:project:id:42:aud"
AUTH_CONFIG = {
    "auth_mode": "oidc",
    "issuer": ISSUER,
    "client_id": "web-client",
    "token_endpoint": TOKEN_ENDPOINT,
    "scopes": ["openid", "profile", "email", AUDIENCE],
    "device_authorization_endpoint": "/api/auth/device",
    "native_client_id": NATIVE_CLIENT,
    "issuer_device_authorization_endpoint": DEVICE_ENDPOINT,
}
DEVICE_CODE = {
    "device_code": "dev-code-1",
    "user_code": "WXYZ-1234",
    "verification_uri": f"{ISSUER}/device",
    "verification_uri_complete": f"{ISSUER}/device?user_code=WXYZ-1234",
    "expires_in": 300,
    "interval": 5,
}
ME = {"subject": "person-1", "kind": "user", "roles": [], "org_id": "org-1"}


def form(route: respx.Route, call: int = -1) -> dict[str, str]:
    body = route.calls[call].request.content.decode()
    return {key: values[0] for key, values in parse_qs(body).items()}


def token_body(access: str = "at-1", refresh: str | None = "rt-1", expires_in: int = 3600) -> dict:
    body: dict = {"access_token": access, "token_type": "Bearer", "expires_in": expires_in}
    if refresh is not None:
        body["refresh_token"] = refresh
    return body


@pytest.fixture()
def idp():
    with respx.mock(assert_all_called=False) as mock:
        mock.get(f"{BASE_URL}/api/auth-config").respond(200, json=AUTH_CONFIG)
        yield mock


# --- access_token= -----------------------------------------------------------------


def test_a_static_access_token_is_the_bearer(api, monkeypatch) -> None:
    monkeypatch.setenv("MATTERWAVE_API_KEY", "mw_from_env")
    route = api.get("/api/me").respond(200, json=ME, headers={"X-Request-Id": REQUEST_ID})
    client = MatterWave(base_url=BASE_URL, access_token="person-token")
    assert client.me().subject == "person-1"
    assert route.calls.last.request.headers["authorization"] == "Bearer person-token"


def test_a_key_and_a_token_together_are_refused() -> None:
    with pytest.raises(ValueError, match="not both"):
        MatterWave(base_url=BASE_URL, api_key="mw_k", access_token="person-token")
    with pytest.raises(TypeError):
        MatterWave(base_url=BASE_URL, access_token=42)  # type: ignore[arg-type]


def test_a_callable_is_asked_once_per_call_and_never_for_a_public_route(api, sleeps) -> None:
    api.get("/api/me").mock(
        side_effect=[
            httpx.Response(503),
            httpx.Response(200, json=ME),
            httpx.Response(200, json=ME),
        ]
    )
    api.get("/v1").respond(200, json={"object": "api", "version": "v1"})
    asked: list[int] = []

    def provider() -> str:
        asked.append(1)
        return f"token-{len(asked)}"

    client = MatterWave(base_url=BASE_URL, access_token=provider)
    client.index()
    assert asked == []
    client.me()  # a 503, then the retry: the same call, one token
    client.me()
    assert len(asked) == 2
    sent = [call.request.headers.get("authorization") for call in api.calls]
    assert sent[1:] == ["Bearer token-1", "Bearer token-1", "Bearer token-2"]


def test_a_callable_that_answers_nothing_useful_is_refused(api) -> None:
    api.get("/api/me").respond(200, json=ME)
    with pytest.raises(TypeError, match="non-empty str"):
        MatterWave(base_url=BASE_URL, access_token=lambda: "").me()

    async def coroutine() -> str:
        return "token"

    with pytest.raises(TypeError, match="AsyncMatterWave"):
        MatterWave(base_url=BASE_URL, access_token=coroutine).me()  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_the_async_client_awaits_a_coroutine_provider(api) -> None:
    route = api.get("/api/me").respond(200, json=ME)

    async def provider() -> str:
        return "async-token"

    client = AsyncMatterWave(base_url=BASE_URL, access_token=provider)
    await client.me()
    assert route.calls.last.request.headers["authorization"] == "Bearer async-token"


def test_no_credential_says_both_ways_to_give_one(monkeypatch) -> None:
    monkeypatch.delenv("MATTERWAVE_API_KEY", raising=False)
    with pytest.raises(MissingAPIKeyError, match="access_token="):
        MatterWave(base_url=BASE_URL).me()


def test_every_operation_names_its_credential() -> None:
    from matterwave_sdk import APP_OPERATIONS, OPERATIONS

    assert set(CREDENTIALS) == set(OPERATIONS) | set(APP_OPERATIONS)
    kinds = {value.split(":", 1)[0] for value in CREDENTIALS.values()}
    assert kinds <= {"public", "key", "person"}


# --- client.auth.person -------------------------------------------------------------


def test_start_asks_the_native_client_for_a_code_with_offline_access(idp) -> None:
    route = idp.post(DEVICE_ENDPOINT).respond(200, json=DEVICE_CODE)
    code = MatterWave(base_url=BASE_URL).auth.person.start()
    assert code.user_code == "WXYZ-1234"
    sent = form(route)
    assert sent["client_id"] == NATIVE_CLIENT
    scopes = sent["scope"].split(" ")
    assert scopes[:4] == ["openid", "profile", "email", AUDIENCE]
    assert "offline_access" in scopes
    assert "authorization" not in route.calls.last.request.headers


def test_the_credential_never_goes_to_the_identity_provider(idp) -> None:
    route = idp.post(DEVICE_ENDPOINT).respond(200, json=DEVICE_CODE)
    MatterWave(base_url=BASE_URL, api_key="mw_secret").auth.person.start()
    assert "authorization" not in route.calls.last.request.headers


def test_login_polls_through_pending_and_slow_down_to_the_token(idp, sleeps) -> None:
    idp.post(DEVICE_ENDPOINT).respond(200, json=DEVICE_CODE)
    route = idp.post(TOKEN_ENDPOINT).mock(
        side_effect=[
            httpx.Response(400, json={"error": "authorization_pending"}),
            httpx.Response(400, json={"error": "slow_down"}),
            httpx.Response(200, json=token_body()),
        ]
    )
    shown = []
    token = MatterWave(base_url=BASE_URL).auth.person.login(on_code=shown.append)
    assert shown[0].verification_uri_complete.endswith("WXYZ-1234")
    assert token.access_token == "at-1" and token.refresh_token == "rt-1"
    assert sleeps == [5, 5, 10]
    sent = form(route)
    assert sent == {
        "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
        "device_code": "dev-code-1",
        "client_id": NATIVE_CLIENT,
    }


def test_login_raises_when_the_person_declines(idp, sleeps) -> None:
    idp.post(DEVICE_ENDPOINT).respond(200, json=DEVICE_CODE)
    idp.post(TOKEN_ENDPOINT).respond(
        400, json={"error": "access_denied", "error_description": "The user denied it."}
    )
    with pytest.raises(DeviceAuthError) as caught:
        MatterWave(base_url=BASE_URL).auth.person.login(on_code=lambda code: None)
    assert caught.value.code == "access_denied"
    assert str(caught.value).startswith("The user denied it.")


def test_a_deployment_without_a_native_client_says_so(api) -> None:
    api.get("/api/auth-config").respond(200, json={"auth_mode": "oidc", "scopes": []})
    with pytest.raises(MatterWaveError, match="no native sign-in client"):
        MatterWave(base_url=BASE_URL).auth.person.start()


def test_refresh_keeps_the_refresh_token_when_none_is_rotated_in(idp) -> None:
    route = idp.post(TOKEN_ENDPOINT).respond(200, json=token_body("at-2", refresh=None))
    signin = MatterWave(base_url=BASE_URL).auth.person
    old = PersonToken(access_token="at-1", expires_at=0, refresh_token="rt-1")
    new = signin.refresh(old)
    assert (new.access_token, new.refresh_token) == ("at-2", "rt-1")
    assert form(route) == {
        "grant_type": "refresh_token",
        "refresh_token": "rt-1",
        "client_id": NATIVE_CLIENT,
    }


def test_a_dead_grant_is_the_identity_providers_error(idp) -> None:
    idp.post(TOKEN_ENDPOINT).respond(400, json={"error": "invalid_grant"})
    signin = MatterWave(base_url=BASE_URL).auth.person
    with pytest.raises(APIError) as caught:
        signin.refresh(PersonToken(access_token="a", expires_at=0, refresh_token="r"))
    assert caught.value.code == "invalid_grant"
    assert caught.value.status_code == 400


def test_a_session_refreshes_ahead_of_expiry_and_reports_each_new_set(idp) -> None:
    idp.post(TOKEN_ENDPOINT).respond(200, json=token_body("at-2", "rt-2"))
    me = idp.get(f"{BASE_URL}/api/me").respond(200, json=ME)
    signin = MatterWave(base_url=BASE_URL).auth.person
    import time

    fresh = PersonToken(access_token="at-1", expires_at=time.time() + 3600, refresh_token="rt-1")
    stored: list[PersonToken] = []
    session = signin.session(fresh, on_refresh=stored.append)
    person = MatterWave(base_url=BASE_URL, access_token=session)
    person.me()
    assert me.calls.last.request.headers["authorization"] == "Bearer at-1"
    assert stored == []
    session._token = PersonToken(
        access_token="at-1", expires_at=time.time() + 30, refresh_token="rt-1"
    )
    person.me()
    assert me.calls.last.request.headers["authorization"] == "Bearer at-2"
    assert [t.refresh_token for t in stored] == ["rt-2"]
    assert session.token.access_token == "at-2"


def test_a_token_set_never_shows_its_tokens() -> None:
    token = PersonToken(access_token="secret-a", expires_at=1.0, refresh_token="secret-r")
    assert "secret" not in repr(token)


@pytest.mark.asyncio
async def test_the_async_sign_in_and_session_match(idp, sleeps) -> None:
    idp.post(DEVICE_ENDPOINT).respond(200, json=DEVICE_CODE)
    idp.post(TOKEN_ENDPOINT).mock(
        side_effect=[
            httpx.Response(400, json={"error": "authorization_pending"}),
            httpx.Response(200, json=token_body("at-1", "rt-1", expires_in=10)),
            httpx.Response(200, json=token_body("at-2", "rt-2")),
        ]
    )
    me = idp.get(f"{BASE_URL}/api/me").respond(200, json=ME)
    signin = AsyncMatterWave(base_url=BASE_URL).auth.person

    async def show(code) -> None:
        assert code.user_code == "WXYZ-1234"

    token = await signin.login(on_code=show)
    assert token.access_token == "at-1"
    stored: list[PersonToken] = []

    async def keep(new: PersonToken) -> None:
        stored.append(new)

    person = AsyncMatterWave(base_url=BASE_URL, access_token=signin.session(token, on_refresh=keep))
    await person.me()  # ten seconds left: refreshed before the call
    assert me.calls.last.request.headers["authorization"] == "Bearer at-2"
    assert [t.access_token for t in stored] == ["at-2"]
