"""What the client says about its own traffic: a log line per attempt,
and a warning the first time a route the API has deprecated is called.

Lines go to the `matterwave_sdk` logger. `MATTERWAVE_LOG=info` writes one
line per response or failure to stderr -- method, path, status, request id,
time taken -- and `MATTERWAVE_LOG=debug` adds each attempt as it goes out
and each wait before a retry. Bodies and headers are never logged: a body
can hold a person's words and a header the key. Configuring the logger the
usual `logging` way works as well, with or without the variable.

The API marks a route it will retire with `Deprecation` (RFC 9745's
`@<unix time>`, or the older `true` / HTTP date) and `Sunset` (RFC 8594,
the date it stops answering). The first response from such a route raises
an `APIDeprecationWarning` naming the route and those dates, once per route
per process; warnings' own filters silence or escalate it.
"""

from __future__ import annotations

import logging
import os
import re
import sys
import threading
import time
import warnings
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import httpx

from ._base import APP_OPERATIONS, OPERATIONS, Request, request_id_of
from .errors import APIDeprecationWarning

LOG_ENV = "MATTERWAVE_LOG"
LEVELS = {"debug": logging.DEBUG, "info": logging.INFO}

logger = logging.getLogger("matterwave_sdk")

_configured = False
_lock = threading.Lock()


def configure_from_env() -> None:
    """Honour `MATTERWAVE_LOG`, once per process: set the logger's level and
    give it a stderr handler if the application has not given it one. An
    unknown value is ignored rather than failing the client."""
    global _configured
    with _lock:
        if _configured:
            return
        _configured = True
        level = LEVELS.get(os.environ.get(LOG_ENV, "").strip().lower())
        if level is None:
            return
        logger.setLevel(level)
        if not logger.handlers:
            handler = logging.StreamHandler(sys.stderr)
            handler.setFormatter(
                logging.Formatter("%(asctime)s %(name)s %(levelname)s %(message)s")
            )
            logger.addHandler(handler)


def started() -> float:
    return time.perf_counter()


def _elapsed_ms(since: float) -> int:
    return round((time.perf_counter() - since) * 1000)


def attempt(request: Request, number: int) -> None:
    logger.debug("%s %s attempt %d", request.method, request.path, number + 1)


def response(request: Request, answer: httpx.Response, since: float) -> None:
    logger.info(
        "%s %s -> %d in %dms request_id=%s",
        request.method,
        request.path,
        answer.status_code,
        _elapsed_ms(since),
        request_id_of(answer) or "-",
    )


def failure(request: Request, exc: BaseException, since: float) -> None:
    logger.info(
        "%s %s failed in %dms: %s",
        request.method,
        request.path,
        _elapsed_ms(since),
        type(exc).__name__,
    )


def retry(request: Request, delay: float, number: int) -> None:
    logger.debug(
        "%s %s retrying in %.2fs (attempt %d)", request.method, request.path, delay, number + 2
    )


# --- deprecation -------------------------------------------------------------

_warned: set[tuple[str, str]] = set()


def _template_pattern(template: str) -> re.Pattern[str]:
    parts = re.split(r"(\{[^}]+\})", template)
    return re.compile("".join("[^/]+" if p.startswith("{") else re.escape(p) for p in parts) + "$")


def _routes() -> list[tuple[str, str, re.Pattern[str]]]:
    known = [(op["method"], op["path"]) for op in OPERATIONS.values()]
    known += list(APP_OPERATIONS.values())
    return [(method, path, _template_pattern(path)) for method, path in known]


def route_of(method: str, path: str) -> str:
    """The route a concrete path belongs to (`/api/keys/{key_id}` for
    `/api/keys/k-1`), so one deprecation warns once rather than once per id;
    the path itself when no known route matches."""
    for known_method, template, pattern in _routes():
        if known_method == method and pattern.match(path):
            return template
    return path


def _date(value: str) -> datetime | None:
    value = value.strip()
    if value.startswith("@"):
        try:
            return datetime.fromtimestamp(int(value[1:]), tz=timezone.utc)
        except (ValueError, OverflowError, OSError):
            return None
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    return when if when.tzinfo else when.replace(tzinfo=timezone.utc)


def _link(header: str | None, rel: str) -> str | None:
    for part in (header or "").split(","):
        match = re.match(r"\s*<([^>]+)>(.*)$", part)
        if match and re.search(rf'rel="?[^";]*\b{rel}\b', match.group(2)):
            return match.group(1)
    return None


def deprecation_message(method: str, route: str, headers: httpx.Headers) -> str | None:
    """The warning for a response that carries `Deprecation` or `Sunset`,
    or None for one that carries neither."""
    deprecation = headers.get("deprecation")
    sunset = headers.get("sunset")
    if deprecation is None and sunset is None:
        return None
    if deprecation is not None and deprecation.strip().lower() == "false":
        return None
    message = f"{method} {route} is deprecated by the MatterWave API"
    since = _date(deprecation) if deprecation else None
    if since is not None:
        message += f" (since {since.date().isoformat()})"
    ends = _date(sunset) if sunset else None
    if ends is not None:
        message += f"; it stops answering on {ends.date().isoformat()}"
    link = _link(headers.get("link"), "deprecation") or _link(headers.get("link"), "sunset")
    if link is not None:
        message += f". See {link}"
    return message + "."


def warn_if_deprecated(request: Request, answer: httpx.Response) -> None:
    """Warn once per route per process when the API says it is deprecated."""
    route = route_of(request.method, request.path)
    message = deprecation_message(request.method, route, answer.headers)
    if message is None:
        return
    with _lock:
        if (request.method, route) in _warned:
            return
        _warned.add((request.method, route))
    logger.warning(message)
    warnings.warn(message, APIDeprecationWarning, stacklevel=4)
