"""The receiver's half of webhooks: `webhooks.unwrap` against signatures
the platform's own scheme produces (`t=<unix>,v1=<hex HMAC-SHA256>` over
`<t>.<body>`), and the refusals: tampered bodies, stale timestamps, missing
and malformed headers."""

from __future__ import annotations

import hashlib
import hmac as hmac_mod
import json

import pytest

from matterwave_sdk import WebhookVerificationError, webhooks

SECRET = "whsec_test_secret"
BODY = json.dumps({"id": "evt_1", "event": "message.completed", "data": {"n": 1}}).encode()


def signed_header(secret: str, body: bytes, timestamp: int) -> str:
    mac = hmac_mod.new(secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256)
    return f"t={timestamp},v1={mac.hexdigest()}"


def headers_with(signature: str) -> dict[str, str]:
    return {
        "MatterWave-Signature": signature,
        "MatterWave-Event-Id": "evt_1",
        "MatterWave-Event-Type": "message.completed",
    }


NOW = 1_800_000_000.0


def test_a_valid_delivery_unwraps_to_the_event() -> None:
    header = webhooks.sign(SECRET, int(NOW), BODY)
    event = webhooks.unwrap(BODY, headers_with(header), SECRET, now=NOW)
    assert event == {"id": "evt_1", "event": "message.completed", "data": {"n": 1}}
    assert webhooks.verify_signature(SECRET, headers_with(header), BODY, now=NOW) is True


def test_the_header_is_found_whichever_case_the_framework_handed_over() -> None:
    header = webhooks.sign(SECRET, int(NOW), BODY)
    lowered = {k.lower(): v for k, v in headers_with(header).items()}
    assert webhooks.unwrap(BODY, lowered, SECRET, now=NOW)["id"] == "evt_1"


def test_a_tampered_body_is_refused() -> None:
    header = webhooks.sign(SECRET, int(NOW), BODY)
    tampered = BODY.replace(b'"n": 1', b'"n": 9999')
    assert webhooks.verify_signature(SECRET, headers_with(header), tampered, now=NOW) is False
    with pytest.raises(WebhookVerificationError, match="missing, stale or wrong"):
        webhooks.unwrap(tampered, headers_with(header), SECRET, now=NOW)


def test_the_wrong_secret_is_refused() -> None:
    header = webhooks.sign("whsec_other", int(NOW), BODY)
    with pytest.raises(WebhookVerificationError):
        webhooks.unwrap(BODY, headers_with(header), SECRET, now=NOW)


def test_a_stale_timestamp_is_refused() -> None:
    stale = webhooks.sign(SECRET, int(NOW) - webhooks.SIGNATURE_TOLERANCE_S - 1, BODY)
    with pytest.raises(WebhookVerificationError, match="missing, stale or wrong"):
        webhooks.unwrap(BODY, headers_with(stale), SECRET, now=NOW)
    # Inside the window, the same delivery is accepted.
    fresh = webhooks.sign(SECRET, int(NOW) - webhooks.SIGNATURE_TOLERANCE_S + 30, BODY)
    assert webhooks.verify_signature(SECRET, headers_with(fresh), BODY, now=NOW) is True


def test_a_timestamp_in_the_future_beyond_the_window_is_refused() -> None:
    ahead = webhooks.sign(SECRET, int(NOW) + 400, BODY)
    with pytest.raises(WebhookVerificationError):
        webhooks.unwrap(BODY, headers_with(ahead), SECRET, now=NOW)


def test_a_missing_header_is_refused() -> None:
    with pytest.raises(WebhookVerificationError, match="missing, stale or wrong"):
        webhooks.unwrap(BODY, {"MatterWave-Event-Id": "evt_1"}, SECRET, now=NOW)
    assert webhooks.verify_signature(SECRET, {}, BODY, now=NOW) is False


@pytest.mark.parametrize(
    "header",
    [
        "",
        "v1=" + "0" * 64,  # a MAC with no timestamp
        "t=1800000000",  # a timestamp with no MAC
        "t=not-a-number,v1=" + "0" * 64,  # a timestamp that is not a number
        "garbage",  # no key=value pairs at all
        f"t={int(NOW)},v1=zzzz",  # a MAC that is not hex of the right length
    ],
)
def test_malformed_headers_are_refused(header: str) -> None:
    assert webhooks.verify_signature(SECRET, headers_with(header), BODY, now=NOW) is False
    with pytest.raises(WebhookVerificationError):
        webhooks.unwrap(BODY, headers_with(header), SECRET, now=NOW)


def test_any_v1_element_may_match_so_a_rotated_secret_verifies() -> None:
    old_mac = hmac_mod.new(SECRET.encode(), f"{int(NOW)}.".encode() + BODY, hashlib.sha256)
    new_mac = hmac_mod.new(b"whsec_rotated", f"{int(NOW)}.".encode() + BODY, hashlib.sha256)
    header = f"t={int(NOW)},v1={old_mac.hexdigest()},v1={new_mac.hexdigest()}"
    event = webhooks.unwrap(BODY, headers_with(header), "whsec_rotated", now=NOW)
    assert event["id"] == "evt_1"
    with pytest.raises(WebhookVerificationError):
        webhooks.unwrap(BODY, headers_with(header), "whsec_third", now=NOW)


def test_a_body_that_is_not_json_is_refused_after_the_signature() -> None:
    body = b"<html>not json</html>"
    header = webhooks.sign(SECRET, int(NOW), body)
    with pytest.raises(WebhookVerificationError, match="not JSON"):
        webhooks.unwrap(body, headers_with(header), SECRET, now=NOW)


def test_a_body_that_is_json_but_not_an_object_is_refused() -> None:
    body = b"[1, 2, 3]"
    header = webhooks.sign(SECRET, int(NOW), body)
    with pytest.raises(WebhookVerificationError, match="not an object"):
        webhooks.unwrap(body, headers_with(header), SECRET, now=NOW)


def test_sign_writes_the_scheme_the_platform_sends() -> None:
    header = webhooks.sign(SECRET, 1_800_000_000, BODY)
    assert header.startswith("t=1800000000,v1=")
    # Independent of `sign`: the value the platform's own snippet computes.
    expected = hmac_mod.new(SECRET.encode(), b"1800000000." + BODY, hashlib.sha256).hexdigest()
    assert header == f"t=1800000000,v1={expected}"


def test_tolerance_is_wideable_for_a_slow_receiver() -> None:
    old = webhooks.sign(SECRET, int(NOW) - 600, BODY)
    assert (
        webhooks.verify_signature(SECRET, headers_with(old), BODY, now=NOW, tolerance_s=700) is True
    )


# --- Standard Webhooks ----------------------------------------------------------

# The Standard Webhooks specification's own test vector, which the platform's
# sender is tested against too.
STD_SECRET = "whsec_MfKQ9r8GKYqrTwjUPD8ILPZIo2LaLaSw"
STD_ID = "msg_p5jXN8AQM9LWM0D4loKWxJek"
STD_T = 1614265330
STD_BODY = b'{"test": 2432232314}'
STD_SIGNATURE = "v1,g0hM9SsE+OTPJTGt/tmIKtSyZlE3uFJELVlNIOLJ1OE="


def standard_headers(signature: str, msg_id: str = STD_ID, timestamp: int = STD_T) -> dict:
    return {
        "webhook-id": msg_id,
        "webhook-timestamp": str(timestamp),
        "webhook-signature": signature,
    }


def test_sign_standard_matches_the_specifications_vector() -> None:
    assert webhooks.sign_standard(STD_SECRET, STD_ID, STD_T, STD_BODY) == STD_SIGNATURE


def test_a_standard_signature_verifies_and_its_refusals() -> None:
    headers = standard_headers(STD_SIGNATURE)
    verify = webhooks.verify_standard_signature
    assert verify(STD_SECRET, headers, STD_BODY, now=STD_T + 1) is True
    assert verify(STD_SECRET, headers, STD_BODY + b" ", now=STD_T + 1) is False
    assert verify(STD_SECRET, headers, STD_BODY, now=STD_T + 301) is False
    assert verify(STD_SECRET, headers, STD_BODY, now=STD_T + 301, tolerance_s=600) is True
    assert verify("whsec_" + "A" * 32, headers, STD_BODY, now=STD_T) is False
    assert (
        verify(STD_SECRET, standard_headers(STD_SIGNATURE, msg_id="msg_other"), STD_BODY, now=STD_T)
        is False
    )
    assert (
        verify(STD_SECRET, standard_headers("v2," + STD_SIGNATURE[3:]), STD_BODY, now=STD_T)
        is False
    )
    assert (
        verify(STD_SECRET, {**headers, "webhook-timestamp": "soon"}, STD_BODY, now=STD_T) is False
    )
    for missing in ("webhook-id", "webhook-timestamp", "webhook-signature"):
        partial = {k: v for k, v in headers.items() if k != missing}
        assert verify(STD_SECRET, partial, STD_BODY, now=STD_T) is False


def test_any_v1_element_of_a_rotation_may_match_and_the_names_are_case_blind() -> None:
    other = webhooks.sign_standard("whsec_" + "B" * 32, STD_ID, STD_T, STD_BODY)
    headers = {k.upper(): v for k, v in standard_headers(f"{other} {STD_SIGNATURE}").items()}
    assert webhooks.verify_standard_signature(STD_SECRET, headers, STD_BODY, now=STD_T) is True


def test_a_legacy_url_safe_secret_still_decodes() -> None:
    legacy = "whsec_" + "-_" * 21 + "A"
    assert len(webhooks.secret_key_bytes(legacy)) == 32
    signature = webhooks.sign_standard(legacy, STD_ID, STD_T, STD_BODY)
    headers = standard_headers(signature)
    assert webhooks.verify_standard_signature(legacy, headers, STD_BODY, now=STD_T) is True


def test_unwrap_accepts_a_delivery_with_only_the_standard_headers() -> None:
    signature = webhooks.sign_standard(SECRET, "evt_1", int(NOW), BODY)
    headers = standard_headers(signature, msg_id="evt_1", timestamp=int(NOW))
    assert webhooks.unwrap(BODY, headers, SECRET, now=NOW)["id"] == "evt_1"


def test_unwrap_accepts_the_platforms_delivery_carrying_both_schemes() -> None:
    headers = {
        **headers_with(webhooks.sign(SECRET, int(NOW), BODY)),
        **standard_headers(
            webhooks.sign_standard(SECRET, "evt_1", int(NOW), BODY), "evt_1", int(NOW)
        ),
    }
    assert webhooks.unwrap(BODY, headers, SECRET, now=NOW)["id"] == "evt_1"


def test_unwrap_refuses_a_standard_signature_under_another_secret() -> None:
    signature = webhooks.sign_standard("whsec_" + "C" * 32, "evt_1", int(NOW), BODY)
    headers = standard_headers(signature, msg_id="evt_1", timestamp=int(NOW))
    with pytest.raises(WebhookVerificationError, match="missing, stale or wrong"):
        webhooks.unwrap(BODY, headers, SECRET, now=NOW)
