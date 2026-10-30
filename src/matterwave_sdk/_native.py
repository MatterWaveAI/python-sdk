"""The requests the native API clients make: keys, usage, webhooks,
attachments, library and shares. Pure, like the rest of `_base`: each
function returns the `Request` a method sends, and the sync and async
clients in `_resources.py` differ only in how they wait for it.

Bodies leave out what the caller did not pass. Where the server reads an
explicit null as "clear it" (a key's expiry, networks, rate limit or
scopes), `UNSET` marks "not passed" and None is sent as null.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import IO, Any
from urllib.parse import parse_qsl, urlsplit

from ._base import UNSET, Request, _segment

# --- keys ----------------------------------------------------------------------


def _key_path(key_id: str, suffix: str = "") -> str:
    return f"/api/keys/{_segment('key_id', key_id)}{suffix}"


def _instant(value: datetime | str) -> str:
    """An absolute expiry. The server refuses one without an offset rather
    than guess the zone, so a naive datetime is refused here first."""
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise ValueError("expires_at must carry a timezone")
        return value.isoformat()
    return value


def _key_settings(
    *,
    scopes: Sequence[str] | None,
    expires_in_days: int | None,
    expires_at: datetime | str | None,
    allowed_cidrs: Sequence[str] | None,
    rpm_limit: int | None,
) -> dict[str, Any]:
    """The fields a key is minted with and `update` may change; UNSET is
    left out, None goes as null."""
    if expires_in_days not in (None, UNSET) and expires_at not in (None, UNSET):
        raise ValueError("pass expires_in_days or expires_at, not both")
    body: dict[str, Any] = {}
    if scopes is not UNSET:
        body["scopes"] = list(scopes) if scopes is not None else None
    if expires_in_days is not UNSET:
        body["expires_in_days"] = expires_in_days
    if expires_at is not UNSET:
        body["expires_at"] = _instant(expires_at) if expires_at is not None else None
    if allowed_cidrs is not UNSET:
        body["allowed_cidrs"] = list(allowed_cidrs) if allowed_cidrs is not None else None
    if rpm_limit is not UNSET:
        body["rpm_limit"] = rpm_limit
    return body


def list_keys_request() -> Request:
    return Request("GET", "/api/keys", None, keyed=True, stream=False)


def create_key_request(
    name: str,
    *,
    scopes: Sequence[str] | None,
    expires_in_days: int | None,
    expires_at: datetime | str | None,
    allowed_cidrs: Sequence[str] | None,
    rpm_limit: int | None,
) -> Request:
    if not name or not name.strip():
        raise ValueError("a key must have a name")
    settings = _key_settings(
        scopes=scopes,
        expires_in_days=expires_in_days,
        expires_at=expires_at,
        allowed_cidrs=allowed_cidrs,
        rpm_limit=rpm_limit,
    )
    # On creation None and omitted mean the same (the default), so nulls are dropped.
    body = {"name": name, **{k: v for k, v in settings.items() if v is not None}}
    return Request("POST", "/api/keys", body, keyed=True, stream=False)


def update_key_request(
    key_id: str,
    *,
    name: str | None,
    scopes: Sequence[str] | None,
    expires_in_days: int | None,
    expires_at: datetime | str | None,
    allowed_cidrs: Sequence[str] | None,
    rpm_limit: int | None,
) -> Request:
    body = _key_settings(
        scopes=scopes,
        expires_in_days=expires_in_days,
        expires_at=expires_at,
        allowed_cidrs=allowed_cidrs,
        rpm_limit=rpm_limit,
    )
    if name is not None:
        if not name.strip():
            raise ValueError("a key must have a name")
        body["name"] = name
    if not body:
        raise ValueError(
            "update needs one of name, scopes, expires_in_days, expires_at, allowed_cidrs, "
            "rpm_limit"
        )
    return Request("PATCH", _key_path(key_id), body, keyed=True, stream=False)


def rotate_key_request(key_id: str) -> Request:
    return Request("POST", _key_path(key_id, "/rotate"), None, keyed=True, stream=False)


def revoke_key_request(key_id: str) -> Request:
    return Request("DELETE", _key_path(key_id), None, keyed=True, stream=False)


# --- usage ---------------------------------------------------------------------


def _period_params(period: str | None) -> dict[str, str]:
    return {"period": period} if period is not None else {}


def my_usage_request() -> Request:
    return Request("GET", "/api/me/usage", None, keyed=True, stream=False)


def org_usage_request(period: str | None) -> Request:
    return Request(
        "GET", "/api/org/usage", None, keyed=True, stream=False, params=_period_params(period)
    )


def org_usage_csv_request(period: str | None) -> Request:
    return Request(
        "GET",
        "/api/org/usage.csv",
        None,
        keyed=True,
        stream=False,
        headers={"Accept": "text/csv"},
        params=_period_params(period),
    )


# --- webhooks ------------------------------------------------------------------


def _endpoint_path(endpoint_id: str, suffix: str = "") -> str:
    return f"/api/org/webhooks/{_segment('endpoint_id', endpoint_id)}{suffix}"


def list_webhooks_request() -> Request:
    return Request("GET", "/api/org/webhooks", None, keyed=True, stream=False)


def create_webhook_request(url: str, events: Sequence[str], description: str | None) -> Request:
    if not events:
        raise ValueError("subscribe to at least one event")
    body: dict[str, Any] = {"url": url, "events": list(events)}
    if description is not None:
        body["description"] = description
    return Request("POST", "/api/org/webhooks", body, keyed=True, stream=False)


def get_webhook_request(endpoint_id: str) -> Request:
    return Request("GET", _endpoint_path(endpoint_id), None, keyed=True, stream=False)


def update_webhook_request(
    endpoint_id: str,
    *,
    url: str | None,
    events: Sequence[str] | None,
    description: str | None,
    enabled: bool | None,
) -> Request:
    # The server reads an omitted field and a null one alike, as "unchanged".
    body: dict[str, Any] = {}
    if url is not None:
        body["url"] = url
    if events is not None:
        if not events:
            raise ValueError("subscribe to at least one event")
        body["events"] = list(events)
    if description is not None:
        body["description"] = description
    if enabled is not None:
        body["enabled"] = enabled
    if not body:
        raise ValueError("update needs one of url, events, description, enabled")
    return Request("PATCH", _endpoint_path(endpoint_id), body, keyed=True, stream=False)


def delete_webhook_request(endpoint_id: str) -> Request:
    return Request("DELETE", _endpoint_path(endpoint_id), None, keyed=True, stream=False)


def rotate_webhook_secret_request(endpoint_id: str) -> Request:
    return Request("POST", _endpoint_path(endpoint_id, "/rotate"), None, keyed=True, stream=False)


def test_webhook_request(endpoint_id: str) -> Request:
    return Request("POST", _endpoint_path(endpoint_id, "/test"), None, keyed=True, stream=False)


DELIVERY_STATUSES = ("pending", "sending", "delivered", "failed")


def list_deliveries_request(endpoint_id: str, status: str | None) -> Request:
    if status is not None and status not in DELIVERY_STATUSES:
        raise ValueError(f"status must be one of {', '.join(DELIVERY_STATUSES)}, got {status!r}")
    return Request(
        "GET",
        _endpoint_path(endpoint_id, "/deliveries"),
        None,
        keyed=True,
        stream=False,
        params={"status": status} if status is not None else {},
    )


def redeliver_request(endpoint_id: str, delivery_id: str) -> Request:
    suffix = f"/deliveries/{_segment('delivery_id', delivery_id)}/redeliver"
    return Request("POST", _endpoint_path(endpoint_id, suffix), None, keyed=True, stream=False)


# --- attachments ---------------------------------------------------------------


def _attachment_path(attachment_id: str, suffix: str = "") -> str:
    return f"/api/attachments/{_segment('attachment_id', attachment_id)}{suffix}"


def upload_attachment_request(
    file: bytes | IO[bytes], filename: str, conversation_id: str | None
) -> Request:
    """The server decides the type from the bytes and never reads the part's
    content type, so none is guessed here. The bytes are read whole so a
    retried upload sends the same file again."""
    if not filename:
        raise ValueError("filename must be non-empty")
    data = file if isinstance(file, bytes) else file.read()
    params = {"conversation_id": conversation_id} if conversation_id is not None else {}
    return Request(
        "POST",
        "/api/attachments",
        None,
        keyed=True,
        stream=False,
        params=params,
        files={"file": (filename, data, "application/octet-stream")},
    )


def get_attachment_request(attachment_id: str) -> Request:
    return Request("GET", _attachment_path(attachment_id), None, keyed=True, stream=False)


def delete_attachment_request(attachment_id: str) -> Request:
    return Request("DELETE", _attachment_path(attachment_id), None, keyed=True, stream=False)


def download_attachment_request(download_url: str) -> Request:
    """The signed `download_url` an attachment carries, fetched with the
    key: the server wants the signature and the caller both."""
    parts = urlsplit(download_url)
    segments = parts.path.strip("/").split("/")
    if len(segments) != 4 or segments[:2] != ["api", "attachments"] or segments[3] != "content":
        raise ValueError(f"not an attachment download URL: {download_url!r}")
    return Request(
        "GET",
        _attachment_path(segments[2], "/content"),
        None,
        keyed=True,
        stream=False,
        headers={"Accept": "*/*"},
        params=dict(parse_qsl(parts.query)),
    )


def attachment_limits_request() -> Request:
    return Request("GET", "/api/attachments/limits/me", None, keyed=True, stream=False)


# --- library -------------------------------------------------------------------


def _folder_path(folder_id: str) -> str:
    return f"/api/library/folders/{_segment('folder_id', folder_id)}"


def _library_conversation_path(conversation_id: str, suffix: str) -> str:
    return f"/api/library/conversations/{_segment('conversation_id', conversation_id)}{suffix}"


def get_library_request() -> Request:
    return Request("GET", "/api/library", None, keyed=True, stream=False)


def library_search_request(q: str, cursor: str | None, limit: int | None) -> Request:
    params = {"q": q}
    if cursor is not None:
        params["cursor"] = cursor
    if limit is not None:
        params["limit"] = str(limit)
    return Request("GET", "/api/search", None, keyed=True, stream=False, params=params)


def create_folder_request(name: str) -> Request:
    return Request("POST", "/api/library/folders", {"name": name}, keyed=True, stream=False)


def update_folder_request(folder_id: str, name: str | None, position: int | None) -> Request:
    body: dict[str, Any] = {}
    if name is not None:
        body["name"] = name
    if position is not None:
        body["position"] = position
    if not body:
        raise ValueError("update needs a name or a position")
    return Request("PATCH", _folder_path(folder_id), body, keyed=True, stream=False)


def delete_folder_request(folder_id: str) -> Request:
    return Request("DELETE", _folder_path(folder_id), None, keyed=True, stream=False)


def move_conversation_request(conversation_id: str, folder_id: str | None) -> Request:
    # `folder_id` is always sent: null is "back to the main list", and the
    # server requires the key so an empty body is not read as that.
    return Request(
        "PUT",
        _library_conversation_path(conversation_id, "/folder"),
        {"folder_id": folder_id},
        keyed=True,
        stream=False,
    )


def create_tag_request(name: str) -> Request:
    return Request("POST", "/api/library/tags", {"name": name}, keyed=True, stream=False)


def delete_tag_request(tag_id: str) -> Request:
    path = f"/api/library/tags/{_segment('tag_id', tag_id)}"
    return Request("DELETE", path, None, keyed=True, stream=False)


def set_tags_request(conversation_id: str, tag_ids: Sequence[str]) -> Request:
    return Request(
        "PUT",
        _library_conversation_path(conversation_id, "/tags"),
        {"tag_ids": list(tag_ids)},
        keyed=True,
        stream=False,
    )


# --- budget --------------------------------------------------------------------


def _cap_micros(name: str, value: int | None) -> int | None:
    """A spend cap in micro-dollars at list price. None is "unlimited" (or
    "back to the default", per route) and has to be sent, so it passes
    through; a negative number is refused here rather than by a 422."""
    if value is not None and value < 0:
        raise ValueError(f"{name} must be 0 or more, got {value}")
    return value


def get_budget_request() -> Request:
    return Request("GET", "/api/budget", None, keyed=True, stream=False)


def set_overage_cap_request(overage_cap_micros: int) -> Request:
    _cap_micros("overage_cap_micros", overage_cap_micros)
    return Request(
        "PUT", "/api/budget", {"overage_cap_micros": overage_cap_micros}, keyed=True, stream=False
    )


def member_budgets_request() -> Request:
    return Request("GET", "/api/budget/members", None, keyed=True, stream=False)


def set_member_default_cap_request(member_default_cap_micros: int | None) -> Request:
    _cap_micros("member_default_cap_micros", member_default_cap_micros)
    return Request(
        "PUT",
        "/api/budget/members",
        {"member_default_cap_micros": member_default_cap_micros},
        keyed=True,
        stream=False,
    )


def set_member_cap_request(subject: str, cap_micros: int | None) -> Request:
    _cap_micros("cap_micros", cap_micros)
    path = f"/api/budget/members/{_segment('subject', subject)}"
    return Request("PUT", path, {"cap_micros": cap_micros}, keyed=True, stream=False)


# --- shares --------------------------------------------------------------------


def _share_conversation_path(conversation_id: str, suffix: str) -> str:
    return f"/api/conversations/{_segment('conversation_id', conversation_id)}{suffix}"


def create_share_request(conversation_id: str, expires_in_s: int | None) -> Request:
    return Request(
        "POST",
        _share_conversation_path(conversation_id, "/share"),
        {"expires_in_s": expires_in_s},
        keyed=True,
        stream=False,
    )


def list_shares_request(conversation_id: str) -> Request:
    path = _share_conversation_path(conversation_id, "/shares")
    return Request("GET", path, None, keyed=True, stream=False)


def list_my_shares_request() -> Request:
    return Request("GET", "/api/me/shares", None, keyed=True, stream=False)


def revoke_share_request(share_id: str) -> Request:
    path = f"/api/shares/{_segment('share_id', share_id)}"
    return Request("DELETE", path, None, keyed=True, stream=False)


def view_share_request(token: str) -> Request:
    """The public read: no key, the link's token is the whole credential."""
    return Request("GET", f"/api/share/{_segment('token', token)}", None, keyed=False, stream=False)


# --- notifications ---------------------------------------------------------------


def _notification_path(notification_id: str, suffix: str = "") -> str:
    return f"/api/me/notifications/{_segment('notification_id', notification_id)}{suffix}"


NOTIFICATIONS_LIMIT_MAX = 100


def list_notifications_request(unread: bool | None, limit: int | None) -> Request:
    """The bell's rows, newest first. `unread` filters to the unread ones
    when passed (the query flag is 0/1); `limit` is the route's 1 to 100."""
    params: dict[str, str] = {}
    if unread is not None:
        params["unread"] = "1" if unread else "0"
    if limit is not None:
        if not 1 <= limit <= NOTIFICATIONS_LIMIT_MAX:
            raise ValueError(f"limit must be 1 to {NOTIFICATIONS_LIMIT_MAX}, got {limit}")
        params["limit"] = str(limit)
    return Request("GET", "/api/me/notifications", None, keyed=True, stream=False, params=params)


def read_all_notifications_request() -> Request:
    return Request("POST", "/api/me/notifications/read-all", None, keyed=True, stream=False)


def read_notification_request(notification_id: str) -> Request:
    return Request(
        "POST", _notification_path(notification_id, "/read"), None, keyed=True, stream=False
    )


# --- announcements ---------------------------------------------------------------


def active_announcements_request() -> Request:
    """The banner's rows: only the ones inside their window now, most
    severe first. The write side is the admin surface's, one namespace up:
    `client.admin.announcements`."""
    return Request("GET", "/api/announcements", None, keyed=True, stream=False)


# --- admin: announcements ----------------------------------------------------------


ADMIN_SEVERITIES = ("info", "warning", "critical")
ADMIN_MAX_MESSAGE_LENGTH = 500
ADMIN_MAX_LINK_LENGTH = 2000


def _admin_announcement_path(announcement_id: str, suffix: str = "") -> str:
    return f"/api/admin/announcements/{_segment('announcement_id', announcement_id)}{suffix}"


def _admin_severity(severity: str) -> str:
    if severity not in ADMIN_SEVERITIES:
        raise ValueError(f"severity must be one of {', '.join(ADMIN_SEVERITIES)}, got {severity!r}")
    return severity


def _admin_message(message: str) -> str:
    """What the route's `NewAnnouncement` refuses: nothing at all, or more
    than 500 characters. The server strips surrounding whitespace itself."""
    if not message.strip():
        raise ValueError("The message is empty.")
    if len(message) > ADMIN_MAX_MESSAGE_LENGTH:
        raise ValueError(f"message must be {ADMIN_MAX_MESSAGE_LENGTH} characters or fewer")
    return message


def _valid_announcement_link(link: str) -> bool:
    """The route's own check (`valid_link` in api/matterwave/announcements.py):
    an absolute http(s) URL or a path on this site; never `//host`, which a
    browser resolves to another origin; no other scheme; no whitespace or
    control character anywhere."""
    if any(ch.isspace() or ord(ch) < 0x20 or ord(ch) == 0x7F for ch in link):
        return False
    if link.startswith("/"):
        return not link.startswith("//") and "\\" not in link
    try:
        parts = urlsplit(link)
    except ValueError:
        return False
    return parts.scheme in ("http", "https") and bool(parts.netloc)


def _admin_link(link: str) -> str:
    if len(link) > ADMIN_MAX_LINK_LENGTH:
        raise ValueError(f"link must be {ADMIN_MAX_LINK_LENGTH} characters or fewer")
    if not _valid_announcement_link(link):
        raise ValueError("A link is an http(s) URL or a path on this site.")
    return link


def _admin_link_or_none(link: str | None) -> str | None:
    """The route's `_clean_link`: trimmed, empty is no link, and what is
    left has to be a link."""
    if link is None:
        return None
    link = link.strip()
    return _admin_link(link) if link else None


def _admin_moment(value: datetime | str) -> str:
    """A window edge. The server reads one without a zone as UTC -- what
    the admin page sends and what a hand-written curl most likely meant --
    so a naive datetime goes as it is."""
    return value.isoformat() if isinstance(value, datetime) else value


def list_admin_announcements_request() -> Request:
    """The admin tab's table: every announcement, not only the active ones."""
    return Request("GET", "/api/admin/announcements", None, keyed=True, stream=False)


def create_admin_announcement_request(
    message: str,
    *,
    severity: str,
    link: str | None,
    starts_at: datetime | str | None,
    ends_at: datetime | str | None,
    dismissible: bool,
) -> Request:
    """The admin console's write. Severity and dismissibility have the
    server's defaults but are always sent, so the body reads whole; the
    window edges go only when set (None starts_at is the server's clock,
    None ends_at is open-ended)."""
    body: dict[str, Any] = {
        "message": _admin_message(message),
        "severity": _admin_severity(severity),
        "dismissible": dismissible,
    }
    cleaned = _admin_link_or_none(link)
    if cleaned is not None:
        body["link"] = cleaned
    if starts_at is not None:
        body["starts_at"] = _admin_moment(starts_at)
    if ends_at is not None:
        body["ends_at"] = _admin_moment(ends_at)
    return Request("POST", "/api/admin/announcements", body, keyed=True, stream=False)


def update_admin_announcement_request(
    announcement_id: str,
    *,
    message: str | None,
    severity: str | None,
    link: str | None,
    starts_at: datetime | str | None,
    ends_at: datetime | str | None,
    dismissible: bool | None,
    end_now: bool,
) -> Request:
    """The route reads only the fields sent. `link` and `ends_at` are the
    nullable pair: None is sent as null to clear (no link, open-ended).
    None on the others means "not passed" and is left out -- the server
    reads an explicit null there as "leave it" all the same. `end_now`
    ends it at the server's clock rather than the caller's, which is what
    the console's "End now" button does."""
    body: dict[str, Any] = {}
    if message is not None:
        body["message"] = _admin_message(message)
    if severity is not None:
        body["severity"] = _admin_severity(severity)
    if link is not UNSET:
        body["link"] = _admin_link_or_none(link)
    if starts_at is not None:
        body["starts_at"] = _admin_moment(starts_at)
    if ends_at is not UNSET:
        body["ends_at"] = None if ends_at is None else _admin_moment(ends_at)
    if dismissible is not None:
        body["dismissible"] = dismissible
    if end_now:
        body["end_now"] = True
    if not body:
        raise ValueError(
            "update needs one of message, severity, link, starts_at, ends_at, dismissible, "
            "end_now"
        )
    return Request(
        "PATCH", _admin_announcement_path(announcement_id), body, keyed=True, stream=False
    )


def delete_admin_announcement_request(announcement_id: str) -> Request:
    return Request(
        "DELETE", _admin_announcement_path(announcement_id), None, keyed=True, stream=False
    )


# --- entitlements ----------------------------------------------------------------


def entitlements_request() -> Request:
    """What the caller's org may do. Member-readable, like `/api/budget`:
    a person must be able to see the limit that will stop their request."""
    return Request("GET", "/api/entitlements", None, keyed=True, stream=False)
