"""`MATTERWAVE_LOG` and the deprecation warning: a line per attempt with
the method, path, status, request id and time, never a body or a header;
one `APIDeprecationWarning` per deprecated route."""

from __future__ import annotations

import logging
import warnings

import httpx
import pytest
from conftest import API_KEY, BASE_URL, REQUEST_ID, completion_body, error_body

from matterwave_sdk import (
    APIConnectionError,
    APIDeprecationWarning,
    AsyncMatterWave,
    MatterWave,
    NotFoundError,
    _log,
)

RATE_LIMITED = error_body("Rate limit exceeded.", "rate_limit_error", "rate_limit_exceeded")
SECRET_WORDS = "my private question about the merger"


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    """Each test sees an unconfigured logger and no routes warned about."""
    monkeypatch.setattr(_log, "_configured", False)
    monkeypatch.setattr(_log, "_warned", set())
    before = (_log.logger.level, list(_log.logger.handlers))
    yield
    _log.logger.setLevel(before[0])
    _log.logger.handlers[:] = before[1]


def test_info_logs_each_response_and_never_the_body_or_the_key(api, client, caplog) -> None:
    api.post("/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=completion_body(), headers={"X-Request-Id": REQUEST_ID})
    )
    with caplog.at_level(logging.INFO, logger="matterwave_sdk"):
        client.chat.completions.create(messages=[{"role": "user", "content": SECRET_WORDS}])
    lines = [r.getMessage() for r in caplog.records]
    assert len(lines) == 1
    assert lines[0].startswith("POST /v1/chat/completions -> 200 in ")
    assert lines[0].endswith(f"request_id={REQUEST_ID}")
    assert SECRET_WORDS not in caplog.text
    assert API_KEY not in caplog.text
    assert "Hello there" not in caplog.text


def test_debug_adds_the_attempts_and_the_waits(api, client, caplog, sleeps) -> None:
    api.post("/v1/chat/completions").mock(
        side_effect=[
            httpx.Response(429, json=RATE_LIMITED, headers={"Retry-After": "2"}),
            httpx.Response(200, json=completion_body()),
        ]
    )
    with caplog.at_level(logging.DEBUG, logger="matterwave_sdk"):
        client.chat.completions.create(messages=[{"role": "user", "content": "hi"}])
    lines = [r.getMessage() for r in caplog.records]
    assert lines[0] == "POST /v1/chat/completions attempt 1"
    assert lines[1].startswith("POST /v1/chat/completions -> 429 in ")
    assert lines[2] == "POST /v1/chat/completions retrying in 2.00s (attempt 2)"
    assert lines[3] == "POST /v1/chat/completions attempt 2"
    assert lines[4].startswith("POST /v1/chat/completions -> 200 in ")


def test_a_failed_connection_is_logged_by_its_kind(api, client, caplog, sleeps) -> None:
    api.get("/api/me").mock(side_effect=httpx.ConnectError("refused"))
    with caplog.at_level(logging.INFO, logger="matterwave_sdk"), pytest.raises(APIConnectionError):
        client.me()
    assert all("failed in" in r.getMessage() and "ConnectError" in r.getMessage() for r in caplog.records)
    assert len(caplog.records) == 3


@pytest.mark.parametrize("value, level", [("debug", logging.DEBUG), ("INFO", logging.INFO)])
def test_the_variable_sets_the_level_and_a_stderr_handler(monkeypatch, value, level) -> None:
    monkeypatch.setenv("MATTERWAVE_LOG", value)
    _log.logger.handlers[:] = []
    MatterWave(api_key="k", base_url=BASE_URL)
    assert _log.logger.level == level
    assert len(_log.logger.handlers) == 1
    MatterWave(api_key="k", base_url=BASE_URL)
    assert len(_log.logger.handlers) == 1


def test_an_unknown_value_changes_nothing(monkeypatch) -> None:
    monkeypatch.setenv("MATTERWAVE_LOG", "loud")
    _log.logger.handlers[:] = []
    level = _log.logger.level
    MatterWave(api_key="k", base_url=BASE_URL)
    assert _log.logger.level == level
    assert _log.logger.handlers == []


def test_logging_reaches_stderr_with_the_variable(monkeypatch, api, capsys) -> None:
    monkeypatch.setenv("MATTERWAVE_LOG", "info")
    _log.logger.handlers[:] = []
    api.get("/api/me").mock(
        return_value=httpx.Response(200, json={"subject": "u-1", "kind": "key", "roles": [], "org_id": "o-1"})
    )
    MatterWave(api_key="k", base_url=BASE_URL).me()
    assert "GET /api/me -> 200 in " in capsys.readouterr().err


# --- deprecation ---------------------------------------------------------------

SUNSET = "Sat, 01 May 2027 00:00:00 GMT"


def test_a_deprecated_route_warns_once_with_its_dates(api, client) -> None:
    headers = {
        "Deprecation": "@1793491200",
        "Sunset": SUNSET,
        "Link": '<https://docs.example/models>; rel="deprecation"',
    }
    api.get("/v1/models/grt-nano").mock(
        return_value=httpx.Response(200, json={"id": "grt-nano", "object": "model"}, headers=headers)
    )
    api.get("/v1/models/grt-mini").mock(
        return_value=httpx.Response(404, json=error_body("no", "invalid_request_error", None), headers=headers)
    )
    with pytest.warns(APIDeprecationWarning) as caught:
        client.models.retrieve("grt-nano")
    assert len(caught) == 1
    message = str(caught[0].message)
    assert message.startswith("GET /v1/models/{model_id} is deprecated by the MatterWave API")
    assert "(since 2026-11-01)" in message
    assert "stops answering on 2027-05-01" in message
    assert message.endswith("See https://docs.example/models.")
    # The same route again, under any id and whatever the status: no second warning.
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        client.models.retrieve("grt-nano")
        with pytest.raises(NotFoundError):
            client.models.retrieve("grt-mini")


def test_a_route_without_the_headers_does_not_warn(api, client) -> None:
    api.get("/api/me").mock(
        return_value=httpx.Response(200, json={"subject": "u-1", "kind": "key", "roles": [], "org_id": "o-1"})
    )
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        client.me()


@pytest.mark.parametrize(
    "deprecation, expected",
    [
        ("@1793491200", " (since 2026-11-01)"),
        ("Sun, 01 Nov 2026 00:00:00 GMT", " (since 2026-11-01)"),
        ("true", ""),
    ],
)
def test_each_deprecation_form_is_read(deprecation, expected) -> None:
    message = _log.deprecation_message("GET", "/v1/models", httpx.Headers({"Deprecation": deprecation}))
    assert message == f"GET /v1/models is deprecated by the MatterWave API{expected}."


def test_deprecation_false_and_no_headers_are_no_warning() -> None:
    assert _log.deprecation_message("GET", "/v1", httpx.Headers({"Deprecation": "false"})) is None
    assert _log.deprecation_message("GET", "/v1", httpx.Headers({})) is None


def test_a_route_is_found_from_its_concrete_path() -> None:
    assert _log.route_of("GET", "/v1/models/grt-nano") == "/v1/models/{model_id}"
    assert _log.route_of("POST", "/api/conversations/c-1/messages") == (
        "/api/conversations/{conversation_id}/messages"
    )
    assert _log.route_of("GET", "/api/not-a-route") == "/api/not-a-route"


@pytest.mark.asyncio
async def test_the_async_client_logs_and_warns_the_same_way(api, caplog) -> None:
    client = AsyncMatterWave(api_key="k", base_url=BASE_URL)
    api.get("/v1/models").mock(
        return_value=httpx.Response(200, json={"object": "list", "data": []}, headers={"Sunset": SUNSET})
    )
    with caplog.at_level(logging.INFO, logger="matterwave_sdk"), pytest.warns(APIDeprecationWarning):
        await client.models.list()
    assert any(r.getMessage().startswith("GET /v1/models -> 200 in ") for r in caplog.records)
