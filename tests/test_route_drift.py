"""The SDK's route table against the platform's whole route document.

tests/openapi-full.json is the platform's docs/developers/openapi-full.json
at the commit tests/openapi-full.pin.json names, vendored by
scripts/refresh_openapi_full.py; the pin also carries the routes the
platform serves but leaves out of that document. The checks run in every
checkout, so a refresh that moves, removes or adds a route fails here:

* every route the SDK calls is in the document, or is one the platform
  names internal (the operator console's announcements are);
* every route in the document is wrapped by the SDK or listed in
  tests/routes-not-covered.json with the reason it is not;
* nothing listed as not covered is wrapped, or gone from the document;
* `CREDENTIALS` names, for each operation, the credential its route's
  `security` accepts -- so a route that stops taking a key fails here.

The live check (scripts/check_live_routes.py, run on a schedule) holds a
running deployment's `/openapi.json` to the same pair.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from matterwave_sdk import APP_OPERATIONS, CREDENTIALS, OPERATIONS

TESTS = Path(__file__).resolve().parent
DOCUMENT_BYTES = (TESTS / "openapi-full.json").read_bytes()
DOCUMENT = json.loads(DOCUMENT_BYTES)
PIN = json.loads((TESTS / "openapi-full.pin.json").read_text(encoding="utf-8"))
NOT_COVERED = json.loads((TESTS / "routes-not-covered.json").read_text(encoding="utf-8"))
METHODS = ("get", "post", "put", "patch", "delete")


def documented() -> set[tuple[str, str]]:
    return {
        (method.upper(), path)
        for path, item in DOCUMENT["paths"].items()
        for method in item
        if method in METHODS
    }


def internal(method: str, path: str) -> bool:
    if [method, path] in [route[:2] for route in PIN["internal_routes"]]:
        return True
    return any(path.startswith(prefix) for prefix in PIN["internal_prefixes"])


def sdk_routes() -> dict[str, tuple[str, str]]:
    routes = {name: (op["method"], op["path"]) for name, op in OPERATIONS.items()}
    routes.update(APP_OPERATIONS)
    return routes


def under(path: str, prefix: str) -> bool:
    return path == prefix.rstrip("/") or path.startswith(prefix.rstrip("/") + "/")


def listed_as_not_covered(method: str, path: str) -> bool:
    if f"{method} {path}" in NOT_COVERED["routes"]:
        return True
    return any(under(path, prefix) for prefix in NOT_COVERED["prefixes"])


def test_the_vendored_document_is_the_one_pinned() -> None:
    assert hashlib.sha256(DOCUMENT_BYTES).hexdigest() == PIN["sha256"], (
        "tests/openapi-full.json was edited by hand; refresh it with "
        "scripts/refresh_openapi_full.py instead"
    )
    assert len(PIN["platform_revision"]) == 40


def test_every_route_the_sdk_calls_is_documented_or_internal() -> None:
    known = documented()
    stray = {
        name: f"{method} {path}"
        for name, (method, path) in sdk_routes().items()
        if (method, path) not in known and not internal(method, path)
    }
    assert stray == {}, "the platform no longer serves these routes as the SDK calls them"


def test_every_documented_route_is_wrapped_or_listed_as_not_covered() -> None:
    wrapped = set(sdk_routes().values())
    unaccounted = sorted(
        f"{method} {path}"
        for method, path in documented() - wrapped
        if not listed_as_not_covered(method, path)
    )
    assert unaccounted == [], (
        "routes the platform added: wrap them, or list them in tests/routes-not-covered.json "
        "with the reason"
    )


def test_nothing_listed_as_not_covered_is_wrapped_or_gone() -> None:
    wrapped = {f"{method} {path}" for method, path in sdk_routes().values()}
    known = {f"{method} {path}" for method, path in documented()}
    for route, reason in NOT_COVERED["routes"].items():
        assert reason.strip(), f"{route} needs the reason it is left out"
        assert route not in wrapped, f"{route} is wrapped; drop it from routes-not-covered.json"
        assert route in known, f"{route} is no longer documented; drop it"
    for prefix, reason in NOT_COVERED["prefixes"].items():
        assert reason.strip(), f"{prefix} needs the reason it is left out"
        assert any(under(path, prefix) for _, path in documented()), f"nothing under {prefix}"
        assert not any(under(path, prefix) for _, path in sdk_routes().values()), prefix


def credential_of(security: list[dict[str, list[str]]]) -> str:
    """The document's `security` as `CREDENTIALS` spells it."""
    if not security:
        return "public"
    schemes = {name: scopes for requirement in security for name, scopes in requirement.items()}
    if "apiKey" not in schemes:
        return "person"
    return "key:" + ",".join(schemes["apiKey"]) if schemes["apiKey"] else "key"


def test_each_operation_names_the_credential_its_route_accepts() -> None:
    wrong = {}
    for name, (method, path) in sdk_routes().items():
        operation = DOCUMENT["paths"].get(path, {}).get(method.lower())
        # The internal routes the SDK calls are the operator console's,
        # which take the operator's own session or token.
        expected = credential_of(operation["security"]) if operation else "person"
        if CREDENTIALS.get(name) != expected:
            wrong[name] = (CREDENTIALS.get(name), expected)
    assert wrong == {}, "CREDENTIALS (SDK, document) disagree"
