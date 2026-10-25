"""Every refusal is the right exception, with the envelope and the request id."""

from __future__ import annotations

import httpx
import pytest
from conftest import BASE_URL, REQUEST_ID, error_body

from matterwave_sdk import (
    APIError,
    AuthenticationError,
    BadRequestError,
    MatterWave,
    NotFoundError,
    PermissionDeniedError,
    RateLimit,
    RateLimitError,
    ServerError,
)


def _answer(api, status: int, body: dict | str, headers: dict | None = None) -> None:
    headers = {"X-Request-Id": REQUEST_ID, **(headers or {})}
    if isinstance(body, dict):
        response = httpx.Response(status, json=body, headers=headers)
    else:
        response = httpx.Response(status, text=body, headers=headers)
    api.post("/v1/chat/completions").mock(return_value=response)


def _call(client: MatterWave):
    return client.chat.completions.create(messages=[{"role": "user", "content": "hi"}])


def _no_retries() -> MatterWave:
    return MatterWave(api_key="k", base_url=BASE_URL, max_retries=0)


@pytest.mark.parametrize(
    ("status", "error_type", "code", "cls"),
    [
        (400, "invalid_request_error", "unsupported_parameter", BadRequestError),
        (401, "authentication_error", "invalid_api_key", AuthenticationError),
        (403, "permission_error", "insufficient_scope", PermissionDeniedError),
        (404, "invalid_request_error", "model_not_found", NotFoundError),
        (429, "insufficient_quota", "insufficient_quota", RateLimitError),
        (502, "server_error", "upstream_error", ServerError),
    ],
)
def test_each_status_is_its_own_exception(api, status, error_type, code, cls) -> None:
    _answer(api, status, error_body("nope", error_type, code, param="model"))
    with pytest.raises(cls) as caught:
        _call(_no_retries())
    err = caught.value
    assert isinstance(err, APIError)
    assert err.status_code == status
    assert err.message == "nope"
    assert err.type == error_type
    assert err.code == code
    assert err.param == "model"
    assert err.request_id == REQUEST_ID
    assert REQUEST_ID in str(err)
    assert f"[{code}]" in str(err)


def test_a_non_envelope_body_still_names_the_status_and_request_id(api) -> None:
    # An ingress or proxy answering for the API sends HTML, not the envelope.
    _answer(api, 503, "<html>upstream unavailable</html>")
    with pytest.raises(ServerError) as caught:
        _call(_no_retries())
    assert caught.value.status_code == 503
    assert "upstream unavailable" in caught.value.message
    assert caught.value.request_id == REQUEST_ID
    assert caught.value.code is None


def test_retry_after_is_surfaced_in_seconds(api) -> None:
    _answer(
        api,
        429,
        error_body("slow down", "rate_limit_error", "rate_limit_exceeded"),
        headers={"Retry-After": "7"},
    )
    with pytest.raises(RateLimitError) as caught:
        _call(_no_retries())
    assert caught.value.retry_after == 7.0
    assert caught.value.rate_limit is None  # no X-RateLimit-* headers were sent


def test_the_rate_limit_window_is_surfaced_from_the_headers(api) -> None:
    _answer(
        api,
        429,
        error_body("slow down", "rate_limit_error", "rate_limit_exceeded"),
        headers={
            "Retry-After": "7",
            "X-RateLimit-Limit": "60",
            "X-RateLimit-Remaining": "0",
            "X-RateLimit-Reset": "7",
        },
    )
    with pytest.raises(RateLimitError) as caught:
        _call(_no_retries())
    assert caught.value.rate_limit == RateLimit(limit=60, remaining=0, reset=7)


def test_a_partial_or_malformed_trio_is_no_window(api) -> None:
    _answer(
        api,
        429,
        error_body("slow down", "rate_limit_error", "rate_limit_exceeded"),
        headers={"X-RateLimit-Limit": "60", "X-RateLimit-Remaining": "soon"},
    )
    with pytest.raises(RateLimitError) as caught:
        _call(_no_retries())
    assert caught.value.rate_limit is None


def test_a_non_json_success_body_is_an_api_error(api) -> None:
    api.post("/v1/chat/completions").mock(
        return_value=httpx.Response(200, text="not json", headers={"X-Request-Id": REQUEST_ID})
    )
    with pytest.raises(APIError) as caught:
        _call(_no_retries())
    assert caught.value.request_id == REQUEST_ID
