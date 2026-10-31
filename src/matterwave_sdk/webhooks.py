"""Verifying the webhook deliveries the platform sends, the receiver's half
of `client.webhooks` (which manages endpoints but never sees one arrive).

    from matterwave_sdk import webhooks

    event = webhooks.unwrap(payload, request.headers, secret)

Every delivery is POSTed with `MatterWave-Signature: t=<unix>,v1=<hex>` --
the hex HMAC-SHA256, under the endpoint's secret, of `<t>.<body>`: the
timestamp is inside the MAC, so a captured delivery cannot be replayed
outside the tolerance window. `unwrap` checks that the header parses, that
the timestamp is within `tolerance_s` seconds of the receiver's clock (300,
the platform's own window), and that the MAC matches in constant time; any
`v1=` element may match, which is what lets a rotated secret verify against
either value during a changeover. Only then is the payload decoded: the
dict is returned, and anything else -- a missing, stale or wrong signature,
a body that is not JSON, a body that is not an object -- raises
`WebhookVerificationError`.

`sign` is here too, for tests and for replaying a delivery to a second
receiver: it is the exact function the platform's sender runs, and the
pair is kept beside each other so a change to the scheme changes both.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from collections.abc import Mapping
from typing import Any

from .errors import WebhookVerificationError

#: The header the signed delivery rides in.
SIGNATURE_HEADER = "MatterWave-Signature"
#: The delivery's event id and type, beside the signature for the receiver's logs.
EVENT_ID_HEADER = "MatterWave-Event-Id"
EVENT_TYPE_HEADER = "MatterWave-Event-Type"
#: How far a signature's timestamp may be from the receiver's clock before
#: the delivery is refused as a replay. The platform signs with the same
#: number and its docs show the same check.
SIGNATURE_TOLERANCE_S = 300


def signed_content(timestamp: int, body: bytes) -> bytes:
    """The bytes the MAC covers: the timestamp, a dot, and the body."""
    return f"{timestamp}.".encode("ascii") + body


def sign(secret: str, timestamp: int, body: bytes) -> str:
    """The `MatterWave-Signature` value for one delivery, exactly as the
    platform's sender writes it: `t=<unix>,v1=<hex HMAC-SHA256>`. The
    timestamp is inside the MAC, so the value cannot be replayed outside
    the tolerance window."""
    mac = hmac.new(secret.encode("utf-8"), signed_content(timestamp, body), hashlib.sha256)
    return f"t={timestamp},v1={mac.hexdigest()}"


def signature_header_of(headers: Mapping[str, str]) -> str | None:
    """The signature header's value, whichever letter case the framework
    handed the headers over in."""
    for name, value in headers.items():
        if name.lower() == SIGNATURE_HEADER.lower():
            return value
    return None


def verify_signature(
    secret: str,
    headers: Mapping[str, str],
    payload: bytes,
    *,
    now: float | None = None,
    tolerance_s: int = SIGNATURE_TOLERANCE_S,
) -> bool:
    """True when `payload` carries a fresh, correct signature under
    `secret`. Constant-time on the MAC; any `v1=` element may match, which
    is what lets a rotated secret be verified against either value during a
    changeover. `now` pins the clock (tests); `tolerance_s` widens or
    narrows the replay window."""
    header = signature_header_of(headers)
    if not header:
        return False
    timestamp: int | None = None
    candidates: list[str] = []
    for element in header.split(","):
        key, sep, value = element.strip().partition("=")
        if not sep:
            return False
        if key == "t":
            try:
                timestamp = int(value)
            except ValueError:
                return False
        elif key == "v1":
            candidates.append(value)
    if timestamp is None or not candidates:
        return False
    if abs((now if now is not None else time.time()) - timestamp) > tolerance_s:
        return False
    expected = hmac.new(
        secret.encode("utf-8"), signed_content(timestamp, payload), hashlib.sha256
    ).hexdigest()
    return any(hmac.compare_digest(expected, candidate) for candidate in candidates)


def unwrap(
    payload: bytes,
    headers: Mapping[str, str],
    secret: str,
    *,
    now: float | None = None,
    tolerance_s: int = SIGNATURE_TOLERANCE_S,
) -> dict[str, Any]:
    """Verify the delivery, then decode it: the event as a dict.

    `WebhookVerificationError` for a missing, malformed, stale or wrong
    signature -- the reason is the message -- and for a body that is not a
    JSON object. The signature is checked before the body is parsed, so a
    forged delivery is never handed to the caller's code.
    """
    if not verify_signature(secret, headers, payload, now=now, tolerance_s=tolerance_s):
        raise WebhookVerificationError(
            "The webhook signature is missing, stale or wrong; the delivery is refused."
        )
    try:
        event = json.loads(payload)
    except ValueError as exc:
        raise WebhookVerificationError(
            "The signed delivery is not JSON.", cause=exc
        ) from exc
    if not isinstance(event, dict):
        raise WebhookVerificationError(
            f"The signed delivery is JSON but not an object, got {type(event).__name__}."
        )
    return event
