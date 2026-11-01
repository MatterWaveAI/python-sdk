"""Signing a person in at the deployment's identity provider, for the routes
an API key can never call.

An API key acts as its owner within its scopes, but the routes that manage
the organisation (members, invites, SSO, billing, the audit trail, budget
caps) gate on the caller's membership and refuse every key. A person's
access token from the identity provider is what they accept: pass one as
`access_token=` and the client sends it in place of a key.

    signin = MatterWave(base_url=URL).auth.person
    token = signin.login(on_code=lambda c: print(c.verification_uri_complete))
    admin = MatterWave(base_url=URL, access_token=signin.session(token))

`login` is RFC 8628's device authorization grant against the identity
provider's public native client, the one `/api/auth-config` names
(`native_client_id`, `issuer_device_authorization_endpoint`,
`token_endpoint`); a deployment that configures none answers
`MatterWaveError` here. It asks for `offline_access` beside the
deployment's own scopes, so the answer carries a refresh token, and
`session(token)` is the callable `access_token=` takes: it hands over the
access token and refreshes it a minute before it expires. Nothing is
written to disk; persist `session.token` (it holds the refresh token) in
the OS keyring if the sign-in should outlive the process.

`client.auth.device` is the other device flow: the API's own, which ends in
an API key rather than a person's token.
"""

from __future__ import annotations

import asyncio
import inspect
import threading
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import httpx

from . import _base
from ._base import Request
from .errors import (
    APIConnectionError,
    APIError,
    APITimeoutError,
    DeviceAuthError,
    MatterWaveError,
    error_class_for,
)
from .models import DeviceCode

if TYPE_CHECKING:
    from ._client import AsyncMatterWave, MatterWave

DEVICE_GRANT_TYPE = "urn:ietf:params:oauth:grant-type:device_code"
# What a refresh token is asked for with; the deployment's own scopes do not
# include it, because the browser's session never needs one.
OFFLINE_ACCESS = "offline_access"
# Refresh this long before the access token expires, so a request started
# just before expiry does not reach the API with a dead token.
REFRESH_AHEAD_S = 60.0
# The lifetime assumed when the token endpoint names none (it is optional in
# RFC 6749): short, because guessing long would hand out an expired token.
DEFAULT_LIFETIME_S = 300.0


@dataclass(frozen=True)
class AuthConfig:
    """`GET /api/auth-config`, the part a native client signs in with.
    `native_client_id` and `device_authorization_endpoint` are None on a
    deployment that offers no native sign-in."""

    auth_mode: str
    issuer: str | None
    token_endpoint: str | None
    native_client_id: str | None
    device_authorization_endpoint: str | None
    scopes: list[str]
    request_id: str | None = field(default=None, compare=False)

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, request_id: str | None = None) -> AuthConfig:
        def text(key: str) -> str | None:
            value = data.get(key)
            return value if isinstance(value, str) and value else None

        scopes = data.get("scopes")
        return cls(
            auth_mode=text("auth_mode") or "",
            issuer=text("issuer"),
            token_endpoint=text("token_endpoint"),
            native_client_id=text("native_client_id"),
            device_authorization_endpoint=text("issuer_device_authorization_endpoint"),
            scopes=[s for s in scopes if isinstance(s, str)] if isinstance(scopes, list) else [],
            request_id=request_id,
        )

    def native(self) -> tuple[str, str, str]:
        """The client id, device endpoint and token endpoint, or the error
        that says this deployment has none."""
        if not (self.native_client_id and self.device_authorization_endpoint):
            raise MatterWaveError(
                "This deployment offers no native sign-in client, so a person cannot sign in "
                "from here; use an API key, or a token from your identity provider."
            )
        if not self.token_endpoint:
            raise MatterWaveError("The deployment's auth config names no token endpoint.")
        return self.native_client_id, self.device_authorization_endpoint, self.token_endpoint


@dataclass(frozen=True)
class PersonToken:
    """A person's token set from the identity provider. `expires_at` is in
    Unix seconds. The tokens are left out of `repr`, so logging the object
    does not log a credential."""

    access_token: str = field(repr=False)
    expires_at: float
    token_type: str = "Bearer"  # noqa: S105 - the scheme name, not a credential
    refresh_token: str | None = field(default=None, repr=False)
    id_token: str | None = field(default=None, repr=False)
    scope: str | None = None

    def expires_within(self, seconds: float, *, now: float | None = None) -> bool:
        return (now if now is not None else time.time()) + seconds >= self.expires_at

    @classmethod
    def from_response(
        cls, data: dict[str, Any], *, now: float, previous_refresh: str | None = None
    ) -> PersonToken:
        token = data.get("access_token")
        if not isinstance(token, str) or not token:
            raise MatterWaveError("The token endpoint answered without an access token.")
        lifetime = data.get("expires_in")
        seconds = (
            float(lifetime)
            if isinstance(lifetime, (int, float)) and not isinstance(lifetime, bool)
            else DEFAULT_LIFETIME_S
        )
        refresh = data.get("refresh_token")
        id_token = data.get("id_token")
        scope = data.get("scope")
        return cls(
            access_token=token,
            expires_at=now + seconds,
            token_type=str(data.get("token_type") or "Bearer"),
            # A refresh that does not rotate the refresh token keeps the old
            # one, as RFC 6749 allows; dropping it would sign the person out
            # at the next expiry for nothing.
            refresh_token=refresh if isinstance(refresh, str) and refresh else previous_refresh,
            id_token=id_token if isinstance(id_token, str) else None,
            scope=scope if isinstance(scope, str) else None,
        )


def auth_config_request() -> Request:
    return Request("GET", "/api/auth-config", None, keyed=False, stream=False)


def scopes_for(config: AuthConfig, extra: Sequence[str] = ()) -> str:
    """The deployment's scopes (its project audience among them, without
    which the API refuses the token as the wrong audience), then
    `offline_access`, then the caller's, each once."""
    wanted: list[str] = []
    for scope in [*config.scopes, "openid", OFFLINE_ACCESS, *extra]:
        if scope not in wanted:
            wanted.append(scope)
    return " ".join(wanted)


def oauth_error(response: httpx.Response) -> APIError:
    """A token or device endpoint's refusal: RFC 6749's `{"error",
    "error_description"}`. The device flow's own codes come back as
    `DeviceAuthError` so a poll loop can tell "not yet" from "never"."""
    try:
        payload: Any = response.json()
    except ValueError:
        payload = None
    code = payload.get("error") if isinstance(payload, dict) else None
    description = payload.get("error_description") if isinstance(payload, dict) else None
    message = str(description or code or f"HTTP {response.status_code}")
    if isinstance(code, str) and code in _base.DEVICE_ERRORS:
        return DeviceAuthError(message, status_code=response.status_code, code=code, body=payload)
    return error_class_for(response.status_code)(
        message,
        status_code=response.status_code,
        code=code if isinstance(code, str) else None,
        body=payload if payload is not None else response.text,
    )


# The identity provider's endpoints take a form and answer JSON. Neither the
# client's credential nor its default headers go there: they are the API's.
FORM_HEADERS = {
    "Accept": "application/json",
    "Content-Type": "application/x-www-form-urlencoded",
    "User-Agent": _base.USER_AGENT,
}


def _wait_limit(code: DeviceCode, timeout: float | None) -> tuple[float, bool]:
    if timeout is not None and timeout < code.expires_in:
        return timeout, True
    return float(code.expires_in), False


def _expired(timed_out: bool, timeout: float | None) -> MatterWaveError:
    if timed_out:
        return APITimeoutError(f"The sign-in was not approved within {timeout}s.")
    return DeviceAuthError(
        "The sign-in code expired before it was approved.", status_code=None, code="expired_token"
    )


class PersonAuth:
    """`client.auth.person`: sign a person in at the identity provider by
    device code, refresh their token, and keep it fresh for a client."""

    def __init__(self, client: MatterWave) -> None:
        self._client = client
        self._config: AuthConfig | None = None

    def config(self) -> AuthConfig:
        """`GET /api/auth-config`, read once per client and kept."""
        if self._config is None:
            response = self._client._send(auth_config_request())
            self._config = AuthConfig.from_dict(
                _base.parse_json_object(response), request_id=_base.request_id_of(response)
            )
        return self._config

    def _post(self, url: str, form: Mapping[str, str]) -> dict[str, Any]:
        try:
            response = self._client._http.post(
                url, data=dict(form), headers=FORM_HEADERS, timeout=self._client._config.timeout
            )
        except httpx.HTTPError as exc:
            raise _connection_error(url, exc) from exc
        if not response.is_success:
            raise oauth_error(response)
        return _base.parse_json_object(response)

    def start(self, *, scopes: Sequence[str] = ()) -> DeviceCode:
        """Ask the identity provider for a code: show the person
        `verification_uri_complete` (or `verification_uri` and `user_code`)."""
        config = self.config()
        client_id, device_endpoint, _ = config.native()
        body = self._post(
            device_endpoint, {"client_id": client_id, "scope": scopes_for(config, scopes)}
        )
        return DeviceCode.from_dict(body)

    def poll(self, device_code: str) -> PersonToken:
        """One poll of the token endpoint: the token set once approved;
        `DeviceAuthError` with RFC 8628's `code` while it is not."""
        client_id, _, token_endpoint = self.config().native()
        body = self._post(
            token_endpoint,
            {"grant_type": DEVICE_GRANT_TYPE, "device_code": device_code, "client_id": client_id},
        )
        return PersonToken.from_response(body, now=time.time())

    def login(
        self,
        *,
        on_code: Callable[[DeviceCode], object],
        timeout: float | None = None,
        scopes: Sequence[str] = (),
    ) -> PersonToken:
        """Start, hand the code to `on_code` to show, and poll at the
        identity provider's interval (longer after each `slow_down`) until
        the person approves. `DeviceAuthError` when they decline or the code
        expires, `APITimeoutError` when `timeout` seconds pass first."""
        code = self.start(scopes=scopes)
        on_code(code)
        interval = float(max(1, code.interval))
        limit, timed_out = _wait_limit(code, timeout)
        deadline = time.monotonic() + limit
        while True:
            if time.monotonic() + interval > deadline:
                raise _expired(timed_out, timeout)
            time.sleep(interval)
            try:
                return self.poll(code.device_code)
            except DeviceAuthError as err:
                if err.code == "slow_down":
                    interval += _base.SLOW_DOWN_STEP_S
                elif err.code != "authorization_pending":
                    raise

    def refresh(self, token: PersonToken) -> PersonToken:
        """A fresh access token from the refresh token. An `APIError` whose
        `code` is `invalid_grant` means the grant is dead (revoked, or the
        refresh token expired): the person signs in again."""
        if not token.refresh_token:
            raise MatterWaveError("This token has no refresh token; sign the person in again.")
        client_id, _, token_endpoint = self.config().native()
        body = self._post(
            token_endpoint,
            {
                "grant_type": "refresh_token",
                "refresh_token": token.refresh_token,
                "client_id": client_id,
            },
        )
        return PersonToken.from_response(
            body, now=time.time(), previous_refresh=token.refresh_token
        )

    def session(
        self, token: PersonToken, *, on_refresh: Callable[[PersonToken], object] | None = None
    ) -> PersonSession:
        """The callable `access_token=` takes, kept fresh from `token`."""
        return PersonSession(self, token, on_refresh=on_refresh)


class PersonSession:
    """A person's token set, kept fresh: call it for the access token. It
    refreshes a minute before expiry (once, however many threads ask) and
    hands each new set to `on_refresh`, so the caller can store the rotated
    refresh token. `token` is the current set."""

    def __init__(
        self,
        auth: PersonAuth,
        token: PersonToken,
        *,
        on_refresh: Callable[[PersonToken], object] | None = None,
    ) -> None:
        self._auth = auth
        self._token = token
        self._on_refresh = on_refresh
        self._lock = threading.Lock()

    @property
    def token(self) -> PersonToken:
        return self._token

    def __call__(self) -> str:
        with self._lock:
            if self._token.refresh_token and self._token.expires_within(REFRESH_AHEAD_S):
                self._token = self._auth.refresh(self._token)
                if self._on_refresh is not None:
                    self._on_refresh(self._token)
            return self._token.access_token


class AsyncPersonAuth:
    """As `PersonAuth`, every call a coroutine."""

    def __init__(self, client: AsyncMatterWave) -> None:
        self._client = client
        self._config: AuthConfig | None = None

    async def config(self) -> AuthConfig:
        if self._config is None:
            response = await self._client._send(auth_config_request())
            self._config = AuthConfig.from_dict(
                _base.parse_json_object(response), request_id=_base.request_id_of(response)
            )
        return self._config

    async def _post(self, url: str, form: Mapping[str, str]) -> dict[str, Any]:
        try:
            response = await self._client._http.post(
                url, data=dict(form), headers=FORM_HEADERS, timeout=self._client._config.timeout
            )
        except httpx.HTTPError as exc:
            raise _connection_error(url, exc) from exc
        if not response.is_success:
            raise oauth_error(response)
        return _base.parse_json_object(response)

    async def start(self, *, scopes: Sequence[str] = ()) -> DeviceCode:
        config = await self.config()
        client_id, device_endpoint, _ = config.native()
        body = await self._post(
            device_endpoint, {"client_id": client_id, "scope": scopes_for(config, scopes)}
        )
        return DeviceCode.from_dict(body)

    async def poll(self, device_code: str) -> PersonToken:
        client_id, _, token_endpoint = (await self.config()).native()
        body = await self._post(
            token_endpoint,
            {"grant_type": DEVICE_GRANT_TYPE, "device_code": device_code, "client_id": client_id},
        )
        return PersonToken.from_response(body, now=time.time())

    async def login(
        self,
        *,
        on_code: Callable[[DeviceCode], Awaitable[object] | object],
        timeout: float | None = None,
        scopes: Sequence[str] = (),
    ) -> PersonToken:
        """As `PersonAuth.login`; `on_code` may be a coroutine function."""
        code = await self.start(scopes=scopes)
        shown = on_code(code)
        if inspect.isawaitable(shown):
            await shown
        interval = float(max(1, code.interval))
        limit, timed_out = _wait_limit(code, timeout)
        deadline = time.monotonic() + limit
        while True:
            if time.monotonic() + interval > deadline:
                raise _expired(timed_out, timeout)
            await asyncio.sleep(interval)
            try:
                return await self.poll(code.device_code)
            except DeviceAuthError as err:
                if err.code == "slow_down":
                    interval += _base.SLOW_DOWN_STEP_S
                elif err.code != "authorization_pending":
                    raise

    async def refresh(self, token: PersonToken) -> PersonToken:
        if not token.refresh_token:
            raise MatterWaveError("This token has no refresh token; sign the person in again.")
        client_id, _, token_endpoint = (await self.config()).native()
        body = await self._post(
            token_endpoint,
            {
                "grant_type": "refresh_token",
                "refresh_token": token.refresh_token,
                "client_id": client_id,
            },
        )
        return PersonToken.from_response(
            body, now=time.time(), previous_refresh=token.refresh_token
        )

    def session(
        self,
        token: PersonToken,
        *,
        on_refresh: Callable[[PersonToken], Awaitable[object] | object] | None = None,
    ) -> AsyncPersonSession:
        return AsyncPersonSession(self, token, on_refresh=on_refresh)


class AsyncPersonSession:
    """As `PersonSession`; awaiting the call refreshes when it is due."""

    def __init__(
        self,
        auth: AsyncPersonAuth,
        token: PersonToken,
        *,
        on_refresh: Callable[[PersonToken], Awaitable[object] | object] | None = None,
    ) -> None:
        self._auth = auth
        self._token = token
        self._on_refresh = on_refresh
        self._lock = asyncio.Lock()

    @property
    def token(self) -> PersonToken:
        return self._token

    async def __call__(self) -> str:
        async with self._lock:
            if self._token.refresh_token and self._token.expires_within(REFRESH_AHEAD_S):
                self._token = await self._auth.refresh(self._token)
                if self._on_refresh is not None:
                    shown = self._on_refresh(self._token)
                    if inspect.isawaitable(shown):
                        await shown
            return self._token.access_token


def _connection_error(url: str, exc: httpx.HTTPError) -> MatterWaveError:
    if isinstance(exc, httpx.TimeoutException):
        return APITimeoutError(f"POST {url} timed out.", cause=exc)
    return APIConnectionError(f"POST {url} could not reach the identity provider: {exc}", cause=exc)
