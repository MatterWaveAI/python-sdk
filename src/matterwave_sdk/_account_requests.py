"""The requests the account and data clients make: the caller's own name,
terms, preferences and sessions, data exports and imports, speech, an
attachment's scan and inline view, the enforcement banner, the billing
status, and the public plan and policy figures. Pure, like `_native`: each
function returns the `Request` a method sends.
"""

from __future__ import annotations

import mimetypes
from typing import IO, Any
from urllib.parse import parse_qsl, urlsplit

from ._base import Request, _segment


def _bytes(file: bytes | IO[bytes]) -> bytes:
    """The upload's bytes, read whole so a retried upload sends the same file."""
    return file if isinstance(file, bytes) else file.read()


# --- the account ---------------------------------------------------------------


def update_me_request(full_name: str) -> Request:
    if not full_name.strip():
        raise ValueError("full_name must be non-empty")
    return Request("PATCH", "/api/me", {"full_name": full_name}, keyed=True, stream=False)


def accept_terms_request(version: str) -> Request:
    if not version:
        raise ValueError("version must be the terms version the person accepted")
    return Request("POST", "/api/me/accept-terms", {"version": version}, keyed=True, stream=False)


def enforcement_request() -> Request:
    return Request("GET", "/api/me/enforcement", None, keyed=True, stream=False)


def get_preferences_request() -> Request:
    return Request("GET", "/api/me/preferences", None, keyed=True, stream=False)


def put_preferences_request(preferences: dict[str, Any]) -> Request:
    """The whole object: the route replaces what is stored, so a field left
    out would go back to its default."""
    return Request("PUT", "/api/me/preferences", preferences, keyed=True, stream=False)


def list_sessions_request() -> Request:
    return Request("GET", "/api/me/sessions", None, keyed=True, stream=False)


def revoke_session_request(session_id: str) -> Request:
    path = f"/api/me/sessions/{_segment('session_id', session_id)}"
    return Request("DELETE", path, None, keyed=True, stream=False)


def revoke_other_sessions_request() -> Request:
    return Request("POST", "/api/me/sessions/revoke-others", None, keyed=True, stream=False)


# --- exports and imports -------------------------------------------------------


def _export_path(export_id: str, suffix: str = "") -> str:
    return f"/api/me/export/{_segment('export_id', export_id)}{suffix}"


def export_document_request() -> Request:
    return Request("GET", "/api/me/export", None, keyed=True, stream=False)


def create_export_request() -> Request:
    return Request("POST", "/api/me/export", None, keyed=True, stream=False)


def get_export_request(export_id: str) -> Request:
    return Request("GET", _export_path(export_id), None, keyed=True, stream=False)


def download_export_request(download_url: str) -> Request:
    """The signed `download_url` a ready export carries, fetched with the
    credential: the signature is bound to the owner, so it takes both."""
    parts = urlsplit(download_url)
    segments = parts.path.strip("/").split("/")
    if len(segments) != 5 or segments[:3] != ["api", "me", "export"] or segments[4] != "download":
        raise ValueError(f"not an export download URL: {download_url!r}")
    return Request(
        "GET",
        _export_path(segments[3], "/download"),
        None,
        keyed=True,
        stream=False,
        headers={"Accept": "application/json"},
        params=dict(parse_qsl(parts.query)),
    )


IMPORT_LIMIT_BYTES = 100 * 1024 * 1024


def create_import_request(file: bytes | IO[bytes], filename: str) -> Request:
    """ChatGPT's `conversations.json` or its whole zip, or Claude's JSON
    export; the server tells them apart by their contents."""
    if not filename:
        raise ValueError("filename must be non-empty")
    data = _bytes(file)
    if len(data) > IMPORT_LIMIT_BYTES:
        raise ValueError("an import is 100 MiB at most")
    kind = "application/zip" if filename.lower().endswith(".zip") else "application/json"
    return Request(
        "POST",
        "/api/me/imports",
        None,
        keyed=True,
        stream=False,
        files={"file": (filename, data, kind)},
    )


def list_imports_request() -> Request:
    return Request("GET", "/api/me/imports", None, keyed=True, stream=False)


def get_import_request(import_id: str) -> Request:
    path = f"/api/me/imports/{_segment('import_id', import_id)}"
    return Request("GET", path, None, keyed=True, stream=False)


# --- speech and attachments ----------------------------------------------------

SPEECH_LIMIT_BYTES = 10 * 1024 * 1024
# The audio types the route takes, by the extension a clip usually carries.
AUDIO_TYPES = {
    ".webm": "audio/webm",
    ".ogg": "audio/ogg",
    ".oga": "audio/ogg",
    ".mp4": "audio/mp4",
    ".m4a": "audio/mp4",
    ".mp3": "audio/mpeg",
    ".mpeg": "audio/mpeg",
    ".wav": "audio/wav",
    ".flac": "audio/flac",
    ".aac": "audio/aac",
}


def transcribe_request(
    file: bytes | IO[bytes], filename: str, *, language: str | None, content_type: str | None
) -> Request:
    """The clip and its type: the route refuses a part that does not say
    it is audio (415), so the type is named from the extension when the
    caller does not give one."""
    data = _bytes(file)
    if not data:
        raise ValueError("the clip is empty")
    if len(data) > SPEECH_LIMIT_BYTES:
        raise ValueError("a clip is 10 MiB at most; send shorter pieces")
    suffix = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    kind = content_type or AUDIO_TYPES.get(suffix) or mimetypes.guess_type(filename)[0]
    if kind is None:
        raise ValueError(f"name the clip's content_type; {filename!r} does not say it")
    form = {"language": language} if language is not None else None
    return Request(
        "POST",
        "/api/speech/transcribe",
        None,
        keyed=True,
        stream=False,
        files={"file": (filename, data, kind)},
        form=form,
    )


def _attachment_path(attachment_id: str, suffix: str) -> str:
    return f"/api/attachments/{_segment('attachment_id', attachment_id)}{suffix}"


def attachment_scan_request(attachment_id: str) -> Request:
    return Request("GET", _attachment_path(attachment_id, "/scan"), None, keyed=True, stream=False)


def view_attachment_request(attachment_id: str) -> Request:
    """The route answers with a redirect to a ten-minute signed link on the
    same origin; the client follows it, with the credential the link is
    bound to, to the file's bytes."""
    return Request(
        "GET",
        _attachment_path(attachment_id, "/view"),
        None,
        keyed=True,
        stream=False,
        headers={"Accept": "*/*"},
        follow_redirects=True,
    )


# --- billing, plans and policy -------------------------------------------------


def billing_status_request() -> Request:
    return Request("GET", "/api/billing/status", None, keyed=True, stream=False)


def list_plans_request() -> Request:
    """The public price list: no credential is sent."""
    return Request("GET", "/api/plans", None, keyed=False, stream=False)


def policy_stats_request() -> Request:
    """The public policy page's counts: no credential is sent."""
    return Request("GET", "/api/policy/stats", None, keyed=False, stream=False)
