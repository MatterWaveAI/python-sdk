"""What the sync and async clients share: configuration, the request each
operation makes, the retry decision, and the parsing of what comes back.

Nothing here does I/O. The two clients in `_client.py` are thin: they hand
an `httpx` response to these functions and sleep the way their world sleeps.
"""

from __future__ import annotations

import json
import os
import random
from collections.abc import AsyncIterable, AsyncIterator, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Literal, TypedDict

import httpx

from .errors import (
    APIError,
    DeviceAuthError,
    MissingAPIKeyError,
    RateLimit,
    error_class_for,
)

API_KEY_ENV = "MATTERWAVE_API_KEY"
BASE_URL_ENV = "MATTERWAVE_BASE_URL"
REQUEST_ID_HEADER = "x-request-id"
API_PREFIX = "/v1"
SSE_DONE = "[DONE]"

DEFAULT_TIMEOUT_S = 60.0
DEFAULT_MAX_RETRIES = 2
# Backoff for a retry the server gave no `Retry-After` for: 0.5s, 1s, 2s ...
# capped, with jitter so a fleet of clients does not retry in lockstep.
BACKOFF_BASE_S = 0.5
BACKOFF_CAP_S = 8.0

# A 429 the server says is the organisation's budget, not the window.
# Waiting does not refill a budget; retrying it only delays the real answer.
NON_RETRYABLE_429_CODES = frozenset({"insufficient_quota"})


class Operation(TypedDict):
    """One method on the client, named the way the OpenAPI document names it.
    `OPERATIONS` is what the contract test compares against the export."""

    method: str
    path: str
    operation_id: str


OPERATIONS: dict[str, Operation] = {
    "index": {"method": "GET", "path": "/v1", "operation_id": "openai_index"},
    "models.list": {"method": "GET", "path": "/v1/models", "operation_id": "openai_list_models"},
    "models.retrieve": {
        "method": "GET",
        "path": "/v1/models/{model_id}",
        "operation_id": "openai_retrieve_model",
    },
    "chat.completions.create": {
        "method": "POST",
        "path": "/v1/chat/completions",
        "operation_id": "openai_chat_completions",
    },
    "embeddings.create": {
        "method": "POST",
        "path": "/v1/embeddings",
        "operation_id": "openai_embeddings",
    },
}


# The app's own routes, which a key may call as its owner. They are not in
# docs/developers/openapi.json (that document is the `/v1` contract), so they
# are listed apart from `OPERATIONS` and the contract test holds them to the
# export only where the export names the same path.
APP_OPERATIONS: dict[str, tuple[str, str]] = {
    "me": ("GET", "/api/me"),
    "conversations.list": ("GET", "/api/conversations"),
    "conversations.search": ("GET", "/api/conversations/search"),
    "conversations.create": ("POST", "/api/conversations"),
    "conversations.get": ("GET", "/api/conversations/{conversation_id}"),
    "conversations.update": ("PATCH", "/api/conversations/{conversation_id}"),
    "conversations.tree": ("GET", "/api/conversations/{conversation_id}/tree"),
    "conversations.set_active_leaf": ("PUT", "/api/conversations/{conversation_id}/active-leaf"),
    "conversations.send": ("POST", "/api/conversations/{conversation_id}/messages"),
    "conversations.edit": (
        "POST",
        "/api/conversations/{conversation_id}/messages/{message_id}/edit",
    ),
    "conversations.regenerate": (
        "POST",
        "/api/conversations/{conversation_id}/messages/{message_id}/regenerate",
    ),
    "conversations.fork": ("POST", "/api/conversations/{conversation_id}/fork"),
    "conversations.context": ("GET", "/api/conversations/{conversation_id}/context"),
    "conversations.compact": ("POST", "/api/conversations/{conversation_id}/compact"),
    "conversations.delete": ("DELETE", "/api/conversations/{conversation_id}"),
    "conversations.pin": ("PUT", "/api/conversations/{conversation_id}/pin"),
    "conversations.unpin": ("DELETE", "/api/conversations/{conversation_id}/pin"),
    "conversations.archive": ("PUT", "/api/conversations/{conversation_id}/archive"),
    "conversations.unarchive": ("DELETE", "/api/conversations/{conversation_id}/archive"),
    "conversations.update_instructions": (
        "PUT",
        "/api/conversations/{conversation_id}/instructions",
    ),
    "conversations.report": ("POST", "/api/conversations/{conversation_id}/report"),
    "instructions.user": ("GET", "/api/me/instructions"),
    "instructions.update_user": ("PUT", "/api/me/instructions"),
    "instructions.org": ("GET", "/api/org/instructions"),
    "instructions.update_org": ("PUT", "/api/org/instructions"),
    "notifications.list": ("GET", "/api/me/notifications"),
    "notifications.read_all": ("POST", "/api/me/notifications/read-all"),
    "notifications.read": ("POST", "/api/me/notifications/{notification_id}/read"),
    "announcements.list": ("GET", "/api/announcements"),
    "admin.announcements.list": ("GET", "/api/admin/announcements"),
    "admin.announcements.create": ("POST", "/api/admin/announcements"),
    "admin.announcements.update": ("PATCH", "/api/admin/announcements/{announcement_id}"),
    "admin.announcements.delete": ("DELETE", "/api/admin/announcements/{announcement_id}"),
    "entitlements.get": ("GET", "/api/entitlements"),
    "messages.set_feedback": ("PUT", "/api/messages/{message_id}/feedback"),
    "messages.delete_feedback": ("DELETE", "/api/messages/{message_id}/feedback"),
    "messages.list_feedback": ("GET", "/api/conversations/{conversation_id}/feedback"),
    "app_models": ("GET", "/api/models"),
    "auth.device.start": ("POST", "/api/auth/device"),
    "auth.device.poll": ("POST", "/api/auth/device/token"),
    "keys.list": ("GET", "/api/keys"),
    "keys.create": ("POST", "/api/keys"),
    "keys.update": ("PATCH", "/api/keys/{key_id}"),
    "keys.rotate": ("POST", "/api/keys/{key_id}/rotate"),
    "keys.revoke": ("DELETE", "/api/keys/{key_id}"),
    "usage.get": ("GET", "/api/me/usage"),
    "usage.org": ("GET", "/api/org/usage"),
    "usage.export_csv": ("GET", "/api/org/usage.csv"),
    "audit.list": ("GET", "/api/org/audit"),
    "audit.export_csv": ("GET", "/api/org/audit.csv"),
    "webhooks.list": ("GET", "/api/org/webhooks"),
    "webhooks.create": ("POST", "/api/org/webhooks"),
    "webhooks.get": ("GET", "/api/org/webhooks/{endpoint_id}"),
    "webhooks.update": ("PATCH", "/api/org/webhooks/{endpoint_id}"),
    "webhooks.delete": ("DELETE", "/api/org/webhooks/{endpoint_id}"),
    "webhooks.rotate_secret": ("POST", "/api/org/webhooks/{endpoint_id}/rotate"),
    "webhooks.test": ("POST", "/api/org/webhooks/{endpoint_id}/test"),
    "webhooks.deliveries": ("GET", "/api/org/webhooks/{endpoint_id}/deliveries"),
    "webhooks.redeliver": (
        "POST",
        "/api/org/webhooks/{endpoint_id}/deliveries/{delivery_id}/redeliver",
    ),
    "attachments.upload": ("POST", "/api/attachments"),
    "attachments.get": ("GET", "/api/attachments/{attachment_id}"),
    "attachments.delete": ("DELETE", "/api/attachments/{attachment_id}"),
    "attachments.download": ("GET", "/api/attachments/{attachment_id}/content"),
    "attachments.limits": ("GET", "/api/attachments/limits/me"),
    "library.get": ("GET", "/api/library"),
    "library.search": ("GET", "/api/search"),
    "library.create_folder": ("POST", "/api/library/folders"),
    "library.update_folder": ("PATCH", "/api/library/folders/{folder_id}"),
    "library.delete_folder": ("DELETE", "/api/library/folders/{folder_id}"),
    "library.move": ("PUT", "/api/library/conversations/{conversation_id}/folder"),
    "library.create_tag": ("POST", "/api/library/tags"),
    "library.delete_tag": ("DELETE", "/api/library/tags/{tag_id}"),
    "library.set_tags": ("PUT", "/api/library/conversations/{conversation_id}/tags"),
    "shares.create": ("POST", "/api/conversations/{conversation_id}/share"),
    "shares.list": ("GET", "/api/conversations/{conversation_id}/shares"),
    "shares.list_mine": ("GET", "/api/me/shares"),
    "shares.revoke": ("DELETE", "/api/shares/{share_id}"),
    "shares.view": ("GET", "/api/share/{token}"),
    "budget.get": ("GET", "/api/budget"),
    "budget.set_overage_cap": ("PUT", "/api/budget"),
    "budget.members": ("GET", "/api/budget/members"),
    "budget.set_member_default_cap": ("PUT", "/api/budget/members"),
    "budget.set_member_cap": ("PUT", "/api/budget/members/{subject}"),
}


Role = Literal["system", "user", "assistant", "tool"]


class Message(TypedDict, total=False):
    """One prompt message, as OpenAI shapes it. `role` and `content` are the
    usual pair; an assistant message that made tool calls carries
    `tool_calls`, and the `tool` message answering one carries
    `tool_call_id`."""

    role: Role
    content: str | list[dict[str, Any]] | None
    tool_calls: list[dict[str, Any]]
    tool_call_id: str
    name: str


class ToolFunction(TypedDict, total=False):
    name: str
    description: str
    parameters: dict[str, Any]
    strict: bool


class Tool(TypedDict):
    type: Literal["function"]
    function: ToolFunction


ToolChoice = Literal["none", "auto", "required"] | dict[str, Any]

EmbeddingInput = str | Sequence[str] | Sequence[int] | Sequence[Sequence[int]]


# --- configuration -----------------------------------------------------------


def resolve_api_key(api_key: str | None) -> str | None:
    if api_key is not None:
        return api_key
    value = os.environ.get(API_KEY_ENV, "").strip()
    return value or None


def resolve_base_url(base_url: str | None) -> str:
    """The origin the API is served from. Accepts the OpenAI-style form
    ending in `/v1` too, since that is what the rest of the docs quote; the
    paths this client builds already carry the prefix."""
    value = base_url if base_url is not None else os.environ.get(BASE_URL_ENV, "").strip()
    if not value:
        raise ValueError(
            f"No base URL: pass base_url= or set {BASE_URL_ENV} to the MatterWave origin."
        )
    value = value.rstrip("/")
    if value.endswith(API_PREFIX):
        value = value[: -len(API_PREFIX)]
    if not value.startswith(("http://", "https://")):
        raise ValueError(f"base_url must start with http:// or https://, got {value!r}")
    return value


# --- requests ----------------------------------------------------------------


@dataclass(frozen=True)
class Request:
    method: str
    path: str
    json: dict[str, Any] | None
    keyed: bool
    stream: bool
    headers: Mapping[str, str] = field(default_factory=dict)
    params: Mapping[str, str] = field(default_factory=dict)
    # A multipart upload: field name -> (filename, bytes, content type). The
    # bytes are held whole so a retried upload sends the same file again.
    files: Mapping[str, tuple[str, bytes, str]] | None = None
    # This request's timeout, where the client's default is wrong for it:
    # an MCP tool that runs a whole model turn takes minutes, not seconds.
    timeout: float | None = None


def index_request() -> Request:
    return Request("GET", "/v1", None, keyed=False, stream=False)


def list_models_request() -> Request:
    return Request("GET", "/v1/models", None, keyed=True, stream=False)


def retrieve_model_request(model_id: str) -> Request:
    if not model_id or "/" in model_id:
        raise ValueError(f"model_id must be a non-empty id without '/', got {model_id!r}")
    return Request("GET", f"/v1/models/{model_id}", None, keyed=True, stream=False)


def chat_completion_body(
    messages: Sequence[Message],
    *,
    model: str | None,
    stream: bool,
    include_usage: bool | None,
    max_tokens: int | None,
    max_completion_tokens: int | None,
    temperature: float | None,
    top_p: float | None,
    stop: str | Sequence[str] | None,
    n: int | None,
    user: str | None,
    tools: Sequence[Tool] | None,
    tool_choice: ToolChoice | None,
    response_format: dict[str, Any] | None,
    conversation_id: str | None,
    extra_body: dict[str, Any] | None,
) -> dict[str, Any]:
    """The request body, with None left out so the server's defaults apply.
    `extra_body` is for a field this client predates: the API ignores
    unknown fields, so sending one is safe, and refusing to would make the
    client the thing that blocks a newer server."""
    if not messages:
        raise ValueError("messages must contain at least one message")
    body: dict[str, Any] = {"messages": [dict(m) for m in messages]}
    if model is not None:
        body["model"] = model
    if stream:
        body["stream"] = True
    if include_usage is not None:
        body["stream_options"] = {"include_usage": include_usage}
    if max_tokens is not None:
        body["max_tokens"] = max_tokens
    if max_completion_tokens is not None:
        body["max_completion_tokens"] = max_completion_tokens
    if temperature is not None:
        body["temperature"] = temperature
    if top_p is not None:
        body["top_p"] = top_p
    if stop is not None:
        body["stop"] = stop if isinstance(stop, str) else list(stop)
    if n is not None:
        body["n"] = n
    if user is not None:
        body["user"] = user
    if tools is not None:
        body["tools"] = [dict(t) for t in tools]
    if tool_choice is not None:
        body["tool_choice"] = tool_choice
    if response_format is not None:
        body["response_format"] = response_format
    if conversation_id is not None:
        body["metadata"] = {"conversation_id": conversation_id}
    if extra_body:
        body.update(extra_body)
    return body


def embeddings_body(
    input: EmbeddingInput,
    *,
    model: str | None,
    encoding_format: Literal["float", "base64"] | None,
    dimensions: int | None,
    user: str | None,
    extra_body: dict[str, Any] | None,
) -> dict[str, Any]:
    """`POST /v1/embeddings`. `model` may be omitted: a deployment serves
    one embedding model, and `GET /v1` names it."""
    if not input:
        raise ValueError("input must be a string or a non-empty list")
    body: dict[str, Any] = {"input": input if isinstance(input, str) else [list(i) if not isinstance(i, (str, int)) else i for i in input]}
    if model is not None:
        body["model"] = model
    if encoding_format is not None:
        body["encoding_format"] = encoding_format
    if dimensions is not None:
        body["dimensions"] = dimensions
    if user is not None:
        body["user"] = user
    if extra_body:
        body.update(extra_body)
    return body


def embeddings_request(body: dict[str, Any]) -> Request:
    return Request("POST", "/v1/embeddings", body, keyed=True, stream=False)


def chat_completions_request(body: dict[str, Any]) -> Request:
    return Request(
        "POST", "/v1/chat/completions", body, keyed=True, stream=bool(body.get("stream"))
    )


def _segment(name: str, value: str) -> str:
    """An id for a path segment. Ids are opaque to the client, but one that
    would change the path it is spliced into is a bug at the call site."""
    if not value or "/" in value or value in (".", ".."):
        raise ValueError(f"{name} must be a non-empty id without '/', got {value!r}")
    return value


def _conversation_path(conversation_id: str, suffix: str = "") -> str:
    return f"/api/conversations/{_segment('conversation_id', conversation_id)}{suffix}"


LIST_FILTERS = ("default", "all", "pinned", "archived")


def me_request() -> Request:
    return Request("GET", "/api/me", None, keyed=True, stream=False)


def list_conversations_request(
    filter: str | None, limit: int | None = None, cursor: str | None = None
) -> Request:
    if filter is not None and filter not in LIST_FILTERS:
        raise ValueError(f"filter must be one of {', '.join(LIST_FILTERS)}, got {filter!r}")
    params: dict[str, str] = {}
    if filter is not None:
        params["filter"] = filter
    if limit is not None:
        params["limit"] = str(limit)
    if cursor is not None:
        params["cursor"] = cursor
    return Request("GET", "/api/conversations", None, keyed=True, stream=False, params=params)


def search_conversations_request(q: str, limit: int | None) -> Request:
    params = {"q": q}
    if limit is not None:
        params["limit"] = str(limit)
    return Request(
        "GET", "/api/conversations/search", None, keyed=True, stream=False, params=params
    )


def create_conversation_request(
    title: str | None, model: str | None, temporary: bool | None = None
) -> Request:
    body: dict[str, Any] = {}
    if title is not None:
        body["title"] = title
    if model is not None:
        body["model"] = model
    if temporary is not None:
        body["temporary"] = temporary
    return Request("POST", "/api/conversations", body, keyed=True, stream=False)


def get_conversation_request(conversation_id: str, full: bool) -> Request:
    return Request(
        "GET",
        _conversation_path(conversation_id),
        None,
        keyed=True,
        stream=False,
        params={"full": "1"} if full else {},
    )


class _Unset:
    """A keyword that was not passed, where None is itself a value."""

    def __repr__(self) -> str:
        return "UNSET"


UNSET: Any = _Unset()


def update_conversation_request(
    conversation_id: str,
    *,
    title: str | None = None,
    default_model: str | None = UNSET,
) -> Request:
    # `default_model` is sent whenever it was passed, None included: the
    # server reads an absent key as "leave it" and null as "clear it", so a
    # sentinel rather than None marks "not passed". A title is never cleared
    # -- a conversation always has one -- so None there is simply "no rename".
    body: dict[str, Any] = {}
    if title is not None:
        if not title.strip():
            raise ValueError("title must be non-empty")
        body["title"] = title
    if default_model is not UNSET:
        body["default_model"] = default_model
    if not body:
        raise ValueError("update needs a title or a default_model")
    return Request("PATCH", _conversation_path(conversation_id), body, True, False)


def conversation_tree_request(conversation_id: str) -> Request:
    return Request("GET", _conversation_path(conversation_id, "/tree"), None, True, False)


def set_active_leaf_request(conversation_id: str, message_id: str) -> Request:
    body = {"message_id": _segment("message_id", message_id)}
    return Request("PUT", _conversation_path(conversation_id, "/active-leaf"), body, True, False)


def fork_conversation_request(conversation_id: str, message_id: str, title: str | None) -> Request:
    body: dict[str, Any] = {"message_id": _segment("message_id", message_id)}
    if title is not None:
        body["title"] = title
    return Request("POST", _conversation_path(conversation_id, "/fork"), body, True, False)


def conversation_context_request(conversation_id: str) -> Request:
    return Request("GET", _conversation_path(conversation_id, "/context"), None, True, False)


def compact_conversation_request(conversation_id: str) -> Request:
    return Request("POST", _conversation_path(conversation_id, "/compact"), {}, True, False)


def delete_conversation_request(conversation_id: str) -> Request:
    return Request("DELETE", _conversation_path(conversation_id), None, True, False)


def set_pinned_request(conversation_id: str, pinned: bool) -> Request:
    # PUT sets and DELETE clears rather than a PATCH carrying a boolean: the
    # toggle is then idempotent by method, so a retried request cannot land
    # in the opposite state from the one the caller asked for.
    return Request(
        "PUT" if pinned else "DELETE", _conversation_path(conversation_id, "/pin"), None, True, False
    )


def set_archived_request(conversation_id: str, archived: bool) -> Request:
    return Request(
        "PUT" if archived else "DELETE",
        _conversation_path(conversation_id, "/archive"),
        None,
        True,
        False,
    )


def conversation_instructions_request(conversation_id: str, text: str | None) -> Request:
    """PUT, not PATCH: the route takes `{"text": ...}` whole, and a null (or
    empty) text clears the thread's prompt so the user's instructions apply
    again."""
    return Request(
        "PUT", _conversation_path(conversation_id, "/instructions"), {"text": text}, True, False
    )


def report_conversation_request(
    conversation_id: str, kind: str, message_id: str | None, note: str | None
) -> Request:
    """The report a person files: durable or not at all (the route 503s
    rather than accept what it cannot store), so a 201 is the whole
    acknowledgement. `note` is the reporter's own words; the server bounds
    and stores it and never logs it."""
    body: dict[str, Any] = {"kind": kind}
    if message_id is not None:
        body["message_id"] = message_id
    if note is not None:
        body["note"] = note
    return Request("POST", _conversation_path(conversation_id, "/report"), body, True, False)


def user_instructions_get_request() -> Request:
    return Request("GET", "/api/me/instructions", None, keyed=True, stream=False)


def user_instructions_request(about: str, style: str) -> Request:
    # The route replaces the pair, so both fields always go: a field left
    # out would clear it on the server rather than leave it alone.
    return Request("PUT", "/api/me/instructions", {"about": about, "style": style}, True, False)


def org_instructions_get_request() -> Request:
    return Request("GET", "/api/org/instructions", None, keyed=True, stream=False)


def org_instructions_request(text: str) -> Request:
    return Request("PUT", "/api/org/instructions", {"text": text}, True, False)


def _message_path(message_id: str, suffix: str = "") -> str:
    return f"/api/messages/{_segment('message_id', message_id)}{suffix}"


def set_feedback_request(
    message_id: str, rating: int, reasons: Sequence[str], comment: str | None
) -> Request:
    body: dict[str, Any] = {"rating": rating, "reasons": list(reasons), "comment": comment}
    return Request("PUT", _message_path(message_id, "/feedback"), body, True, False)


def delete_feedback_request(message_id: str) -> Request:
    return Request("DELETE", _message_path(message_id, "/feedback"), None, True, False)


def list_feedback_request(conversation_id: str) -> Request:
    """The caller's own ratings in one conversation, read back as a list."""
    return Request(
        "GET", _conversation_path(conversation_id, "/feedback"), None, True, stream=False
    )


def app_models_request() -> Request:
    return Request("GET", "/api/models", None, keyed=True, stream=False)


def _turn_body(
    content: str | None,
    *,
    model: str | None,
    temperature: float | None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {}
    if content is not None:
        if not content:
            raise ValueError("content must be non-empty")
        body["content"] = content
    if model is not None:
        body["model"] = model
    if temperature is not None:
        body["temperature"] = temperature
    if extra:
        body.update(extra)
    return body


def send_message_request(
    conversation_id: str,
    content: str,
    *,
    model: str | None,
    temperature: float | None,
    attachment_ids: Sequence[str] | None,
    parent_id: str | None,
) -> Request:
    extra: dict[str, Any] = {}
    if attachment_ids:
        extra["attachment_ids"] = list(attachment_ids)
    if parent_id is not None:
        extra["parent_id"] = parent_id
    body = _turn_body(content, model=model, temperature=temperature, extra=extra)
    return Request("POST", _conversation_path(conversation_id, "/messages"), body, True, True)


def edit_message_request(
    conversation_id: str,
    message_id: str,
    content: str,
    *,
    model: str | None,
    temperature: float | None,
) -> Request:
    path = _conversation_path(
        conversation_id, f"/messages/{_segment('message_id', message_id)}/edit"
    )
    body = _turn_body(content, model=model, temperature=temperature)
    return Request("POST", path, body, True, True)


def regenerate_message_request(
    conversation_id: str, message_id: str, *, model: str | None, temperature: float | None
) -> Request:
    path = _conversation_path(
        conversation_id, f"/messages/{_segment('message_id', message_id)}/regenerate"
    )
    body = _turn_body(None, model=model, temperature=temperature)
    return Request("POST", path, body, True, True)


# The resume path only reads `Last-Event-ID`, but the request body is
# validated before the route runs, so it has to be a valid send. A turn that
# was not a send (an edit, a regenerate) resumes through the same route with
# this in place of the text it never had.
RESUME_PLACEHOLDER = "(resume)"


def resume_request(original: Request, conversation_id: str, last_event_id: str) -> Request:
    """The request that picks a dropped turn up after `last_event_id`. It
    goes to the send route whatever started the turn: that is where the
    server keeps the resume path, and the header makes it replay rather
    than generate, so the prompt is never billed twice."""
    body = dict(original.json or {})
    if not body.get("content"):
        body["content"] = RESUME_PLACEHOLDER
    return Request(
        "POST",
        _conversation_path(conversation_id, "/messages"),
        body,
        keyed=True,
        stream=True,
        headers={"Last-Event-ID": last_event_id},
    )


def device_start_request(client_name: str | None) -> Request:
    body = {"client_name": client_name} if client_name is not None else {}
    return Request("POST", "/api/auth/device", body, keyed=False, stream=False)


def device_token_request(device_code: str) -> Request:
    if not device_code:
        raise ValueError("device_code must be non-empty")
    return Request(
        "POST", "/api/auth/device/token", {"device_code": device_code}, keyed=False, stream=False
    )


DEVICE_ERRORS = frozenset({"authorization_pending", "slow_down", "expired_token", "access_denied"})
# RFC 8628 section 3.5: each `slow_down` adds five seconds to the interval, for good.
SLOW_DOWN_STEP_S = 5


def device_error_of(error: APIError) -> DeviceAuthError | None:
    """The device-flow refusal inside an error response, or None for any
    other failure. The RFC's `{"error": "<code>"}` is read whether it comes
    bare or inside the API's `detail`."""
    body = error.body
    code: Any = None
    if isinstance(body, dict):
        code = body.get("error")
        detail = body.get("detail")
        if code is None and isinstance(detail, dict):
            code = detail.get("error")
        elif code is None and isinstance(detail, str) and detail in DEVICE_ERRORS:
            code = detail
    if not isinstance(code, str) or code not in DEVICE_ERRORS:
        return None
    description = body.get("error_description") if isinstance(body, dict) else None
    return DeviceAuthError(
        str(description or code),
        status_code=error.status_code,
        code=code,
        request_id=error.request_id,
        body=body,
    )


def headers_for(
    request: Request,
    api_key: str | None,
    user_agent: str,
    default_headers: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """The request's headers, least specific first: the client's own
    defaults (`Accept`, `User-Agent`), then the caller's `default_headers`,
    which may replace them (a client built on this one names itself), then
    what the operation itself needs, such as `Last-Event-ID` on a resume."""
    headers = {"Accept": "application/json", "User-Agent": user_agent}
    if request.stream:
        headers["Accept"] = "text/event-stream"
    headers.update(default_headers or {})
    headers.update(request.headers)
    if request.keyed:
        if api_key is None:
            raise MissingAPIKeyError(
                f"{request.method} {request.path} needs an API key: pass api_key= or set "
                f"{API_KEY_ENV}."
            )
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


# --- responses ---------------------------------------------------------------


def request_id_of(response: httpx.Response) -> str | None:
    value = response.headers.get(REQUEST_ID_HEADER)
    return str(value) if value is not None else None


def retry_after_of(response: httpx.Response) -> float | None:
    """`Retry-After` in seconds, whether the server sent a delay or a date."""
    raw = response.headers.get("retry-after")
    if raw is None:
        return None
    raw = raw.strip()
    try:
        return max(0.0, float(raw))
    except ValueError:
        pass
    try:
        when = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return max(0.0, (when - datetime.now(timezone.utc)).total_seconds())


def rate_limit_of(response: httpx.Response) -> RateLimit | None:
    """The `X-RateLimit-*` trio, when the server sent all three as integers;
    a partial or malformed set is treated as none rather than guessed at."""
    values = []
    for name in ("x-ratelimit-limit", "x-ratelimit-remaining", "x-ratelimit-reset"):
        raw = response.headers.get(name)
        if raw is None:
            return None
        try:
            values.append(int(raw.strip()))
        except ValueError:
            return None
    return RateLimit(*values)


def error_from_response(response: httpx.Response, body: bytes | None = None) -> APIError:
    """The exception for a non-2xx response. The envelope is OpenAI's when
    the API produced the response; anything else (an ingress 502 page, a
    proxy's HTML) is kept raw so the message still says what came back."""
    raw = body if body is not None else response.content
    payload: Any = None
    try:
        payload = json.loads(raw) if raw else None
    except ValueError:
        payload = None
    error = payload.get("error") if isinstance(payload, dict) else None
    cls = error_class_for(response.status_code)
    if isinstance(error, dict):
        return cls(
            str(error.get("message") or f"HTTP {response.status_code}"),
            status_code=response.status_code,
            error_type=error.get("type"),
            code=error.get("code"),
            param=error.get("param"),
            request_id=request_id_of(response),
            retry_after=retry_after_of(response),
            rate_limit=rate_limit_of(response),
            body=payload,
        )
    detail = _detail_text(payload.get("detail")) if isinstance(payload, dict) else None
    if detail:
        # The app's own routes answer `{"detail": ..., "request_id": ...}`,
        # FastAPI's shape with the ids beside it, rather than OpenAI's.
        return cls(
            detail,
            status_code=response.status_code,
            request_id=request_id_of(response) or _opt_str(payload.get("request_id")),
            retry_after=retry_after_of(response),
            rate_limit=rate_limit_of(response),
            body=payload,
        )
    text = raw.decode("utf-8", "replace").strip() if raw else ""
    message = f"HTTP {response.status_code}"
    if text:
        message = f"{message}: {text[:200]}"
    return cls(
        message,
        status_code=response.status_code,
        request_id=request_id_of(response),
        retry_after=retry_after_of(response),
        rate_limit=rate_limit_of(response),
        body=payload if payload is not None else text,
    )


def _opt_str(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def _detail_text(detail: Any) -> str | None:
    """A FastAPI `detail` as one line: the string, or a 422's per-field
    messages joined."""
    if isinstance(detail, str):
        return detail
    if isinstance(detail, list):
        messages = [str(d.get("msg")) for d in detail if isinstance(d, dict) and d.get("msg")]
        return "; ".join(messages) or None
    return None


def error_from_envelope(payload: dict[str, Any], *, request_id: str | None) -> APIError:
    """An error that arrived inside a stream, after the 200. The API sends
    the same envelope it would have used for the status; the status itself
    is gone, so `status_code` is None and `type`/`code` carry the meaning."""
    error = payload.get("error")
    if not isinstance(error, dict):
        error = {}
    return APIError(
        str(error.get("message") or "The stream ended with an error."),
        status_code=None,
        error_type=error.get("type"),
        code=error.get("code"),
        param=error.get("param"),
        request_id=request_id,
        body=payload,
    )


def parse_json_object(response: httpx.Response) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError as exc:
        raise APIError(
            "The API answered with a body that is not JSON.",
            status_code=response.status_code,
            request_id=request_id_of(response),
            body=response.text,
        ) from exc
    if not isinstance(payload, dict):
        raise APIError(
            "The API answered with JSON that is not an object.",
            status_code=response.status_code,
            request_id=request_id_of(response),
            body=payload,
        )
    return payload


# --- retries -----------------------------------------------------------------


def should_retry(response: httpx.Response, body: bytes | None = None) -> bool:
    """429 and 5xx, except the 429 that means the budget is spent."""
    status = response.status_code
    if status >= 500:
        return True
    if status != 429:
        return False
    raw = body if body is not None else response.content
    try:
        payload = json.loads(raw) if raw else None
    except ValueError:
        return True
    error = payload.get("error") if isinstance(payload, dict) else None
    code = error.get("code") if isinstance(error, dict) else None
    return code not in NON_RETRYABLE_429_CODES


def backoff_s(attempt: int, retry_after: float | None, *, rand: random.Random | None = None) -> float:
    """How long to wait before retry number `attempt` (0-based). The server's
    `Retry-After` wins when it sent one; otherwise exponential with jitter."""
    if retry_after is not None:
        return retry_after
    rng: random.Random | Any = rand or random
    base = min(BACKOFF_CAP_S, BACKOFF_BASE_S * (2**attempt))
    return float(base * (0.5 + rng.random() * 0.5))


# --- server-sent events ------------------------------------------------------


def sse_data_frames(lines: Iterable[str]) -> Iterable[str]:
    """The `data:` payload of each event, joined across continuation lines
    and ended by a blank line, as the SSE spec reads them: `sse_events` with
    the names dropped, for `/v1`, which names none. One parser underneath,
    so the two streams cannot disagree on a frame."""
    for event in sse_events(lines):
        yield event.data


async def async_sse_data_frames(lines: AsyncIterable[str]) -> AsyncIterator[str]:
    """`sse_data_frames` for lines that arrive asynchronously."""
    async for event in async_sse_events(lines):
        yield event.data


def decode_stream_frame(frame: str, *, request_id: str | None) -> dict[str, Any] | None:
    """A frame's JSON object, or None for `[DONE]`. An error envelope on a
    frame is raised here, where every consumer of the stream passes."""
    if frame.strip() == SSE_DONE:
        return None
    try:
        payload = json.loads(frame)
    except ValueError as exc:
        raise APIError(
            "The stream carried a frame that is not JSON.",
            status_code=None,
            request_id=request_id,
            body=frame,
        ) from exc
    if not isinstance(payload, dict):
        raise APIError(
            "The stream carried a frame that is not an object.",
            status_code=None,
            request_id=request_id,
            body=payload,
        )
    if "error" in payload and "choices" not in payload:
        raise error_from_envelope(payload, request_id=request_id)
    return payload


@dataclass(frozen=True)
class SSEEvent:
    """One dispatched event: its name (`message` when the frame named
    none, as the spec has it), its joined data, and its `id:` if it had one."""

    event: str
    data: str
    id: str | None = None


class _EventReader:
    """The SSE spec's field reading, shared by the sync and async parsers so
    the two cannot disagree on a frame."""

    def __init__(self) -> None:
        self.event = ""
        self.data: list[str] = []
        self.id: str | None = None

    def feed(self, raw: str) -> SSEEvent | None:
        line = raw.rstrip("\r\n")
        if line == "":
            return self.dispatch()
        if line.startswith(":"):
            # A comment: the server's heartbeat, or a proxy's keep-alive.
            return None
        field_name, sep, value = line.partition(":")
        if sep and value.startswith(" "):
            value = value[1:]
        if field_name == "data":
            self.data.append(value)
        elif field_name == "event":
            self.event = value
        elif field_name == "id" and "\0" not in value:
            self.id = value
        return None

    def dispatch(self) -> SSEEvent | None:
        event = None
        if self.data:
            event = SSEEvent(self.event or "message", "\n".join(self.data), self.id)
        self.event, self.data, self.id = "", [], None
        return event


def sse_events(lines: Iterable[str]) -> Iterable[SSEEvent]:
    """Named events, for the conversation stream: `event:`, `data:` and `id:`
    read per the spec, comments (the `: mw-heartbeat` every few seconds)
    skipped, and a final event with no trailing blank line still delivered.
    `sse_data_frames` stays the reader for `/v1`, which names no events."""
    reader = _EventReader()
    for raw in lines:
        event = reader.feed(raw)
        if event is not None:
            yield event
    event = reader.dispatch()
    if event is not None:
        yield event


async def async_sse_events(lines: AsyncIterable[str]) -> AsyncIterator[SSEEvent]:
    reader = _EventReader()
    async for raw in lines:
        event = reader.feed(raw)
        if event is not None:
            yield event
    event = reader.dispatch()
    if event is not None:
        yield event


def decode_event(event: SSEEvent, *, request_id: str | None) -> Any:
    """An event's JSON. Every frame the conversation stream sends is JSON;
    one that is not is a broken stream, raised as the `/v1` reader does."""
    try:
        return json.loads(event.data)
    except ValueError as exc:
        raise APIError(
            f"The stream carried a {event.event!r} frame that is not JSON.",
            status_code=None,
            request_id=request_id,
            body=event.data,
        ) from exc
