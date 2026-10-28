"""Every failure the client raises, and the request id that goes with it.

The API answers every refusal in OpenAI's envelope -- `{"error": {"message",
"type", "param", "code"}}` -- and stamps every response, refusals included,
with `X-Request-Id`. Both land on the exception: the envelope so code can
branch on `code` (`insufficient_quota` is not `rate_limit_exceeded`), the
request id so a bug report can name the exact call the server logged.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class RateLimit:
    """The window the server reported in `X-RateLimit-*`: its ceiling in
    requests per minute, the requests left in it after this one, and the
    seconds until the oldest request ages out and a slot is back."""

    limit: int
    remaining: int
    reset: int


class MatterWaveError(Exception):
    """Base class for everything this package raises."""


class MissingAPIKeyError(MatterWaveError):
    """A keyed operation was called with no key configured: neither an
    `api_key` argument nor `MATTERWAVE_API_KEY` in the environment."""


class APIConnectionError(MatterWaveError):
    """The request never produced a response: DNS, refused, reset, TLS."""

    def __init__(self, message: str, *, cause: BaseException | None = None) -> None:
        super().__init__(message)
        self.__cause__ = cause


class APITimeoutError(APIConnectionError):
    """The request produced no response within the client's timeout."""


class APIError(MatterWaveError):
    """A response the API sent to say no, parsed from its error envelope.

    `status_code` is None for an error that arrived inside a stream, after
    a 200 had already been sent: the body carries the envelope, the status
    line could not.
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None,
        error_type: str | None = None,
        code: str | None = None,
        param: str | None = None,
        request_id: str | None = None,
        retry_after: float | None = None,
        rate_limit: RateLimit | None = None,
        body: Any = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.type = error_type
        self.code = code
        self.param = param
        self.request_id = request_id
        self.retry_after = retry_after
        self.rate_limit = rate_limit
        self.body = body

    def __str__(self) -> str:
        parts = [self.message]
        if self.status_code is not None:
            parts.append(f"(status {self.status_code})")
        if self.code:
            parts.append(f"[{self.code}]")
        if self.request_id:
            parts.append(f"request_id={self.request_id}")
        return " ".join(parts)


class BadRequestError(APIError):
    """400: a malformed body, an unsupported parameter, a non-text part."""


class AuthenticationError(APIError):
    """401: no key, or an invalid, expired or revoked one."""


class PermissionDeniedError(APIError):
    """403: the key lacks the scope, or the account is suspended."""


class NotFoundError(APIError):
    """404: an unknown model or a conversation that is not the key owner's."""


class ConflictError(APIError):
    """409: the request is valid but the state does not allow it, such as a
    compaction with nothing to fold yet."""


class RateLimitError(APIError):
    """429: the per-key window, or the organisation's monthly budget.

    `code` tells them apart: `rate_limit_exceeded` clears when `retry_after`
    seconds have passed and the client retries it on its own;
    `insufficient_quota` clears when an admin raises the budget, so it is
    raised at once.

    `rate_limit` is the window that refused, from the `X-RateLimit-*`
    headers (None when the server sent none, as on `insufficient_quota`):
    what to pace by once the wait is over.
    """


class ServerError(APIError):
    """5xx: the API or the model gateway behind it failed."""


class DeviceAuthError(APIError):
    """A device login the server has not approved. `code` is RFC 8628's:
    `authorization_pending` and `slow_down` mean poll again (later, for the
    second); `expired_token` and `access_denied` mean start over."""


class McpError(MatterWaveError):
    """A JSON-RPC error the `/mcp` endpoint answered with.

    `code` is the JSON-RPC code: -32601 for a method the server does not
    have, -32022 for a protocol version it does not serve (the ones it does
    ride in `data["supported"]`), -32020 for a mirrored header that disagrees
    with the body. A refusal that happens before any method runs -- no key,
    a revoked one, the key's rate limit -- is not here: it travels with its
    HTTP status and is raised as the ordinary `APIError` family
    (`RateLimitError` and friends).
    """

    def __init__(self, message: str, *, code: int | None = None, data: Any = None) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.data = data

    def __str__(self) -> str:
        parts = [self.message]
        if self.code is not None:
            parts.append(f"[{self.code}]")
        return " ".join(parts)


class McpToolError(McpError):
    """A tool that ran and refused, in the protocol's own shape: the result
    carried `isError: true` and a `structuredContent.error`, so the call
    succeeded and the answer is "no" -- the model's plan, not the transport,
    is what failed.

    `error_code` is the machine-readable reason (`budget`, `not_found`,
    `upgrade_required`, `moderation`, ...), `detail` the prose, `status` the
    HTTP status of the underlying refusal where there was one, `request_id`
    the server's id for finding the call in its logs, and `error` the whole
    structured error as sent.
    """

    def __init__(
        self,
        message: str,
        *,
        tool: str,
        error_code: str,
        detail: str,
        request_id: str | None = None,
        status: int | None = None,
        error: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.tool = tool
        self.error_code = error_code
        self.detail = detail
        self.request_id = request_id
        self.status = status
        self.error = error if error is not None else {}


def error_class_for(status_code: int) -> type[APIError]:
    if status_code == 400:
        return BadRequestError
    if status_code == 401:
        return AuthenticationError
    if status_code == 403:
        return PermissionDeniedError
    if status_code == 404:
        return NotFoundError
    if status_code == 409:
        return ConflictError
    if status_code == 429:
        return RateLimitError
    if status_code >= 500:
        return ServerError
    return APIError
