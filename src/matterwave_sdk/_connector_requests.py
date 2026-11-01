"""The requests the preset and connection clients make. Pure, like
`_native`: each function returns the `Request` a method sends.

Bodies leave out what the caller did not pass. Where the server reads an
explicit null as "clear it" (a preset's tools and welcome message), `UNSET`
marks "not passed" and None is sent as null. Credentials go into a request
body here and nowhere else: no answer carries them back.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

from ._base import UNSET, Request, _segment

PRESETS_PATH = "/api/org/presets"
CONNECTIONS_PATH = "/api/org/connections"
SLACK_MANIFEST_PATH = "/api/org/connections/slack/manifest"
CONNECTOR_IDENTITIES_PATH = "/api/org/connector-identities"

# The states an admin may put a connection in. `pending`, `revoked` and
# `error` are the platform's and the provider's to set.
CONNECTION_STATUSES = ("active", "disabled")
# What an admin may do to an identity, as the PATCH's `action` names it.
IDENTITY_ACTIONS = ("map", "unmap", "block", "unblock")
RATE_LIMIT_MAX = 10_000
EVENTS_LIMIT_MAX = 200

PRESET_FIELDS = ("name", "description", "model", "instructions", "tools", "welcome_message")
CONNECTION_FIELDS = ("display_name", "status", "rate_limit_per_minute", "config", "credentials")
ROUTE_FIELDS = ("channel_label", "preset_id", "paying_subject", "enabled")


def _get(path: str, params: Mapping[str, str] | None = None) -> Request:
    return Request("GET", path, None, keyed=True, stream=False, params=dict(params or {}))


def _required(name: str, value: str) -> str:
    if not value or not value.strip():
        raise ValueError(f"{name} must not be blank")
    return value


# --- presets -------------------------------------------------------------------


def _preset_path(preset_id: str) -> str:
    return f"{PRESETS_PATH}/{_segment('preset_id', preset_id)}"


def list_presets_request() -> Request:
    return _get(PRESETS_PATH)


def get_preset_request(preset_id: str) -> Request:
    return _get(_preset_path(preset_id))


def create_preset_request(
    name: str,
    model: str,
    *,
    description: str | None,
    instructions: str | None,
    tools: Sequence[str] | None,
    welcome_message: str | None,
) -> Request:
    """`tools` left out is the platform's default set; an empty list is none."""
    body: dict[str, Any] = {"name": _required("name", name), "model": _required("model", model)}
    if description is not None:
        body["description"] = description
    if instructions is not None:
        body["instructions"] = instructions
    if tools is not None:
        body["tools"] = list(tools)
    if welcome_message is not None:
        body["welcome_message"] = welcome_message
    return Request("POST", PRESETS_PATH, body, keyed=True, stream=False)


def update_preset_request(
    preset_id: str,
    *,
    name: str | None = None,
    description: str | None = None,
    model: str | None = None,
    instructions: str | None = None,
    tools: Sequence[str] | None = UNSET,
    welcome_message: str | None = UNSET,
) -> Request:
    """None leaves a name, description, model or instructions alone, as the
    route reads a null there; for `tools` it goes back to the default set
    and for `welcome_message` it drops the message, so those two send it."""
    body: dict[str, Any] = {}
    if name is not None:
        body["name"] = _required("name", name)
    if description is not None:
        body["description"] = description
    if model is not None:
        body["model"] = _required("model", model)
    if instructions is not None:
        body["instructions"] = instructions
    if tools is not UNSET:
        body["tools"] = list(tools) if tools is not None else None
    if welcome_message is not UNSET:
        body["welcome_message"] = welcome_message
    if not body:
        raise ValueError(f"update needs one of {', '.join(PRESET_FIELDS)}")
    return Request("PATCH", _preset_path(preset_id), body, keyed=True, stream=False)


def delete_preset_request(preset_id: str) -> Request:
    return Request("DELETE", _preset_path(preset_id), None, keyed=True, stream=False)


# --- connections ---------------------------------------------------------------


def _connection_path(connection_id: str, suffix: str = "") -> str:
    return f"{CONNECTIONS_PATH}/{_segment('connection_id', connection_id)}{suffix}"


def _rate_limit(value: int) -> int:
    if not 1 <= value <= RATE_LIMIT_MAX:
        raise ValueError(f"rate_limit_per_minute must be 1 to {RATE_LIMIT_MAX}, got {value}")
    return value


def _credentials(credentials: Mapping[str, Any]) -> dict[str, Any]:
    if not credentials:
        raise ValueError("credentials must name at least one field")
    return dict(credentials)


def list_connections_request() -> Request:
    return _get(CONNECTIONS_PATH)


def get_connection_request(connection_id: str) -> Request:
    return _get(_connection_path(connection_id))


def create_connection_request(
    connector: str,
    display_name: str,
    *,
    credentials: Mapping[str, Any] | None,
    config: Mapping[str, Any] | None,
    rate_limit_per_minute: int | None,
) -> Request:
    """Without `credentials` the connection is made `pending`: its ingress
    URL exists for a provider whose app is built around it (Slack's), and
    an update carrying the credentials activates it."""
    body: dict[str, Any] = {
        "connector": _required("connector", connector),
        "display_name": _required("display_name", display_name),
    }
    if credentials is not None:
        body["credentials"] = _credentials(credentials)
    if config is not None:
        body["config"] = dict(config)
    if rate_limit_per_minute is not None:
        body["rate_limit_per_minute"] = _rate_limit(rate_limit_per_minute)
    return Request("POST", CONNECTIONS_PATH, body, keyed=True, stream=False)


def update_connection_request(
    connection_id: str,
    *,
    display_name: str | None = None,
    status: str | None = None,
    rate_limit_per_minute: int | None = None,
    config: Mapping[str, Any] | None = None,
    credentials: Mapping[str, Any] | None = None,
) -> Request:
    """What was passed changes; the route reads a null as "unchanged", so
    None is left out."""
    body: dict[str, Any] = {}
    if display_name is not None:
        body["display_name"] = _required("display_name", display_name)
    if status is not None:
        if status not in CONNECTION_STATUSES:
            raise ValueError(
                f"status must be one of {', '.join(CONNECTION_STATUSES)}, got {status!r}"
            )
        body["status"] = status
    if rate_limit_per_minute is not None:
        body["rate_limit_per_minute"] = _rate_limit(rate_limit_per_minute)
    if config is not None:
        body["config"] = dict(config)
    if credentials is not None:
        body["credentials"] = _credentials(credentials)
    if not body:
        raise ValueError(f"update needs one of {', '.join(CONNECTION_FIELDS)}")
    return Request("PATCH", _connection_path(connection_id), body, keyed=True, stream=False)


def delete_connection_request(connection_id: str) -> Request:
    return Request("DELETE", _connection_path(connection_id), None, keyed=True, stream=False)


def rotate_connection_url_request(connection_id: str) -> Request:
    path = _connection_path(connection_id, "/rotate-url")
    return Request("POST", path, None, keyed=True, stream=False)


def slack_manifest_request(connection_id: str) -> Request:
    return _get(SLACK_MANIFEST_PATH, {"connection_id": _required("connection_id", connection_id)})


# --- routes --------------------------------------------------------------------


def _route_path(connection_id: str, route_id: str | None = None) -> str:
    path = _connection_path(connection_id, "/routes")
    return path if route_id is None else f"{path}/{_segment('route_id', route_id)}"


def list_routes_request(connection_id: str) -> Request:
    return _get(_route_path(connection_id))


def create_route_request(
    connection_id: str,
    external_channel_id: str,
    preset_id: str,
    paying_subject: str,
    *,
    channel_label: str | None,
    enabled: bool | None,
) -> Request:
    body: dict[str, Any] = {
        "external_channel_id": _required("external_channel_id", external_channel_id),
        "preset_id": _required("preset_id", preset_id),
        "paying_subject": _required("paying_subject", paying_subject),
    }
    if channel_label is not None:
        body["channel_label"] = channel_label
    if enabled is not None:
        body["enabled"] = enabled
    return Request("POST", _route_path(connection_id), body, keyed=True, stream=False)


def update_route_request(
    connection_id: str,
    route_id: str,
    *,
    channel_label: str | None = None,
    preset_id: str | None = None,
    paying_subject: str | None = None,
    enabled: bool | None = None,
) -> Request:
    body: dict[str, Any] = {}
    if channel_label is not None:
        body["channel_label"] = channel_label
    if preset_id is not None:
        body["preset_id"] = _required("preset_id", preset_id)
    if paying_subject is not None:
        body["paying_subject"] = _required("paying_subject", paying_subject)
    if enabled is not None:
        body["enabled"] = enabled
    if not body:
        raise ValueError(f"update needs one of {', '.join(ROUTE_FIELDS)}")
    return Request("PATCH", _route_path(connection_id, route_id), body, keyed=True, stream=False)


def delete_route_request(connection_id: str, route_id: str) -> Request:
    path = _route_path(connection_id, route_id)
    return Request("DELETE", path, None, keyed=True, stream=False)


# --- identities ----------------------------------------------------------------


def list_identities_request(connection_id: str, status: str | None) -> Request:
    params = {"status": status} if status is not None else {}
    return _get(_connection_path(connection_id, "/identities"), params)


def update_identity_request(
    connection_id: str, identity_id: str, action: str, subject: str | None = None
) -> Request:
    """`map` names the member in `subject`; `unmap`, `block` and `unblock` take none."""
    if action not in IDENTITY_ACTIONS:
        raise ValueError(f"action must be one of {', '.join(IDENTITY_ACTIONS)}, got {action!r}")
    body: dict[str, Any] = {"action": action}
    if action == "map":
        body["subject"] = _required("subject", subject or "")
    path = _connection_path(connection_id, f"/identities/{_segment('identity_id', identity_id)}")
    return Request("PATCH", path, body, keyed=True, stream=False)


def identities_for_member_request(subject: str) -> Request:
    return _get(CONNECTOR_IDENTITIES_PATH, {"subject": _required("subject", subject)})


# --- the event log -------------------------------------------------------------


def _since(value: datetime | str) -> str:
    """The route refuses a moment without a zone rather than guess one, so a
    naive datetime is refused here first."""
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise ValueError("since must carry a timezone")
        return value.isoformat()
    return value


def list_events_request(
    connection_id: str,
    *,
    outcome: str | None,
    since: datetime | str | None,
    cursor: str | None,
    limit: int | None,
) -> Request:
    if limit is not None and not 1 <= limit <= EVENTS_LIMIT_MAX:
        raise ValueError(f"limit must be 1 to {EVENTS_LIMIT_MAX}, got {limit}")
    params: dict[str, str] = {}
    if outcome is not None:
        params["outcome"] = outcome
    if since is not None:
        params["since"] = _since(since)
    if cursor is not None:
        params["cursor"] = cursor
    if limit is not None:
        params["limit"] = str(limit)
    return _get(_connection_path(connection_id, "/events"), params)
