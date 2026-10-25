"""Device login: start, the poll loop through pending and slow_down to the
token, and the refusals that end it."""

from __future__ import annotations

import httpx
import pytest

from matterwave_sdk import AsyncMatterWave, DeviceAuthError, DeviceCode, MatterWave

START = {
    "device_code": "dev-secret-half",
    "user_code": "WDJB-MJHT",
    "verification_uri": "https://matterwave.test/device",
    "verification_uri_complete": "https://matterwave.test/device?user_code=WDJB-MJHT",
    "expires_in": 600,
    "interval": 5,
}
TOKEN = {"token_type": "Bearer", "access_token": "mw_device_issued", "scopes": ["chat:read", "chat:write"]}


def _refusal(code: str, status: int = 400) -> httpx.Response:
    return httpx.Response(status, json={"error": code})


def test_start_needs_no_key_and_names_the_client(api) -> None:
    route = api.post("/api/auth/device").mock(return_value=httpx.Response(200, json=START))
    client = MatterWave(api_key=None, base_url="https://matterwave.test")
    code = client.auth.device.start(client_name="matterwave-tui")
    assert code.user_code == "WDJB-MJHT" and code.interval == 5
    sent = route.calls.last.request
    assert "Authorization" not in sent.headers
    assert sent.content == b'{"client_name":"matterwave-tui"}'


def test_poll_raises_the_rfc_code_while_pending(api, client: MatterWave) -> None:
    api.post("/api/auth/device/token").mock(return_value=_refusal("authorization_pending"))
    with pytest.raises(DeviceAuthError) as caught:
        client.auth.device.poll("dev-secret-half")
    assert caught.value.code == "authorization_pending"
    assert caught.value.status_code == 400


def test_login_polls_through_pending_and_slow_down_to_the_token(api, sleeps) -> None:
    api.post("/api/auth/device").mock(return_value=httpx.Response(200, json=START))
    poll = api.post("/api/auth/device/token").mock(
        side_effect=[
            _refusal("authorization_pending"),
            _refusal("slow_down"),
            _refusal("authorization_pending"),
            httpx.Response(200, json=TOKEN),
        ]
    )
    shown: list[DeviceCode] = []
    client = MatterWave(api_key=None, base_url="https://matterwave.test")
    token = client.auth.device.login(on_code=shown.append, timeout=120)

    assert [c.user_code for c in shown] == ["WDJB-MJHT"]
    assert token.access_token == "mw_device_issued"
    assert token.scopes == ["chat:read", "chat:write"]
    assert "mw_device_issued" not in repr(token)
    assert poll.call_count == 4
    assert all(c.request.content == b'{"device_code":"dev-secret-half"}' for c in poll.calls)
    # The interval, then five seconds more for good after the slow_down.
    assert sleeps == [5, 5, 10, 10]


@pytest.mark.parametrize("code", ["access_denied", "expired_token"])
def test_login_stops_on_a_refusal(api, sleeps, code: str) -> None:
    api.post("/api/auth/device").mock(return_value=httpx.Response(200, json=START))
    api.post("/api/auth/device/token").mock(
        side_effect=[_refusal("authorization_pending"), _refusal(code)]
    )
    client = MatterWave(api_key=None, base_url="https://matterwave.test")
    with pytest.raises(DeviceAuthError) as caught:
        client.auth.device.login(on_code=lambda _c: None)
    assert caught.value.code == code


def test_the_token_is_an_ordinary_bearer(api) -> None:
    me = api.get("/api/me").mock(
        return_value=httpx.Response(200, json={"subject": "u-1", "kind": "key", "roles": []})
    )
    client = MatterWave(api_key=TOKEN["access_token"], base_url="https://matterwave.test")
    client.me()
    assert me.calls.last.request.headers["Authorization"] == "Bearer mw_device_issued"


@pytest.mark.asyncio
async def test_async_login_awaits_the_callback(api, sleeps) -> None:
    api.post("/api/auth/device").mock(return_value=httpx.Response(200, json=START))
    api.post("/api/auth/device/token").mock(
        side_effect=[_refusal("slow_down"), httpx.Response(200, json=TOKEN)]
    )
    shown: list[str] = []

    async def on_code(code: DeviceCode) -> None:
        shown.append(code.verification_uri_complete)

    client = AsyncMatterWave(api_key=None, base_url="https://matterwave.test")
    token = await client.auth.device.login(on_code=on_code)
    assert token.access_token == "mw_device_issued"
    assert shown == [START["verification_uri_complete"]]
    assert sleeps == [5, 10]
