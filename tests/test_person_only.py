"""The methods whose routes refuse every API key: each says so in its
docstring, with the platform's 403 message; a key gets that 403 as a
`PermissionDeniedError`; and a person's token reaches the route."""

from __future__ import annotations

import inspect
from collections.abc import Callable
from typing import Any

import pytest
from conftest import BASE_URL, REQUEST_ID

from matterwave_sdk import (
    CREDENTIALS,
    AsyncMatterWave,
    MatterWave,
    PermissionDeniedError,
)

DETAIL = "Membership is managed by a person, not an API key."
H = {"X-Request-Id": REQUEST_ID}

AUDIT_PAGE = {"data": [], "actions": [], "next_cursor": None}
BUDGET = {
    "period": "2026-10",
    "overage_micros": 0,
    "overage_cap_micros": 5_000_000,
    "member_default_cap_micros": None,
}
MEMBERS = {"data": [], "member_default_cap_micros": None}
MEMBER = {"subject": "u-2", "cap_micros": 1_000_000, "spent_micros": 0}

# Each person-only method: how to call it, its route, and what the route answers.
CALLS: dict[str, tuple[Callable[[Any], Any], str, str, Any]] = {
    "audit.list": (lambda c: c.audit.list(), "GET", "/api/org/audit", AUDIT_PAGE),
    "audit.export_csv": (lambda c: c.audit.export_csv(), "GET", "/api/org/audit.csv", None),
    "instructions.org": (
        lambda c: c.instructions.org(),
        "GET",
        "/api/org/instructions",
        {"text": ""},
    ),
    "instructions.update_org": (
        lambda c: c.instructions.update_org("Be brief."),
        "PUT",
        "/api/org/instructions",
        {"text": "Be brief."},
    ),
    "budget.set_overage_cap": (
        lambda c: c.budget.set_overage_cap(5_000_000),
        "PUT",
        "/api/budget",
        BUDGET,
    ),
    "budget.members": (lambda c: c.budget.members(), "GET", "/api/budget/members", MEMBERS),
    "budget.set_member_default_cap": (
        lambda c: c.budget.set_member_default_cap(None),
        "PUT",
        "/api/budget/members",
        MEMBERS,
    ),
    "budget.set_member_cap": (
        lambda c: c.budget.set_member_cap("u-2", 1_000_000),
        "PUT",
        "/api/budget/members/u-2",
        MEMBER,
    ),
}


def method_of(client: object, dotted: str) -> Any:
    target: Any = client
    for part in dotted.split("."):
        target = getattr(target, part)
    return target


def test_the_person_only_methods_are_exactly_the_person_credentials_outside_admin() -> None:
    person = {name for name, need in CREDENTIALS.items() if need == "person"}
    assert set(CALLS) == {name for name in person if not name.startswith("admin.")}


@pytest.mark.parametrize("name", sorted(CALLS))
def test_each_says_it_needs_a_person_and_quotes_the_refusal(name: str) -> None:
    doc = " ".join(
        inspect.getdoc(method_of(MatterWave(api_key="k", base_url=BASE_URL), name)).split()
    )
    assert DETAIL in doc
    assert "person's token" in doc


@pytest.mark.parametrize("name", sorted(CALLS))
def test_a_key_gets_the_documented_403(api, name: str) -> None:
    call, method, path, _ = CALLS[name]
    api.route(method=method, path=path).respond(403, json={"detail": DETAIL}, headers=H)
    with pytest.raises(PermissionDeniedError) as caught:
        call(MatterWave(api_key="mw_key", base_url=BASE_URL))
    assert str(caught.value).startswith(DETAIL)
    assert caught.value.status_code == 403


@pytest.mark.parametrize("name", sorted(CALLS))
def test_a_persons_token_reaches_the_route(api, name: str) -> None:
    call, method, path, body = CALLS[name]
    if body is None:
        route = api.route(method=method, path=path).respond(200, text="id,action\r\n", headers=H)
    else:
        route = api.route(method=method, path=path).respond(200, json=body, headers=H)
    call(MatterWave(access_token="person-token", base_url=BASE_URL))
    assert route.calls.last.request.headers["authorization"] == "Bearer person-token"


@pytest.mark.asyncio
async def test_the_async_client_carries_the_persons_token_too(api) -> None:
    route = api.get("/api/budget/members").respond(200, json=MEMBERS, headers=H)
    await AsyncMatterWave(access_token="person-token", base_url=BASE_URL).budget.members()
    assert route.calls.last.request.headers["authorization"] == "Bearer person-token"
