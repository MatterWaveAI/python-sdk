"""The billing resources (`client.billing`, `client.plans`) against the fake API: the public
plan catalog, the move to Free, the billing trail and the Stripe surface.
Each test answers with the body the route in matterwave-app sends and
checks the request the client made.
"""

from __future__ import annotations

import re
from pathlib import Path

import httpx
import pytest
from conftest import API_KEY, REQUEST_ID

from matterwave_sdk import (
    APP_OPERATIONS,
    APIError,
    AsyncMatterWave,
    BadRequestError,
    MatterWave,
)

H = {"X-Request-Id": REQUEST_ID}


def sent(route) -> httpx.Request:
    return route.calls.last.request


def body_of(route) -> dict:
    import json

    return json.loads(sent(route).content)


def _family_operations() -> dict[str, tuple[str, str]]:
    return {k: v for k, v in APP_OPERATIONS.items() if k.split(".")[0] == "billing"}


def test_every_billing_operation_names_a_method_on_both_clients() -> None:
    for client in (
        MatterWave(api_key="k", base_url="https://matterwave.test"),
        AsyncMatterWave(api_key="k", base_url="https://matterwave.test"),
    ):
        for dotted in _family_operations():
            target: object = client
            for part in dotted.split("."):
                assert hasattr(target, part), f"{type(client).__name__} has no {dotted}"
                target = getattr(target, part)
            assert callable(target)


def _api_routes() -> set[tuple[str, str]]:
    found: set[tuple[str, str]] = set()
    for prefix_width in (2, 3, 4):
        base = Path(__file__).resolve().parents[prefix_width]
        for candidate in (
            base / "matterwave-app" / "api" / "matterwave",
            base / "api" / "matterwave",
        ):
            if candidate.is_dir():
                for source in candidate.glob("*.py"):
                    text = source.read_text(encoding="utf-8")
                    prefixes = dict(
                        re.findall(r'(\w+)\s*=\s*APIRouter\([^)]*prefix="([^"]*)"', text)
                    )
                    for router, method, path in re.findall(
                        r'@(\w+)\.(get|post|put|patch|delete)\(\s*"([^"]*)"', text
                    ):
                        found.add((method.upper(), prefixes.get(router, "") + path))
    return found


def test_every_billing_operation_is_a_route_the_api_declares() -> None:
    routes = _api_routes()
    if not routes:
        pytest.skip("matterwave-app/api/matterwave is not beside the SDK in this checkout")
    for key, (method, path) in _family_operations().items():
        assert (method, path) in routes, f"{key}: {method} {path} is not an API route"


# --- the catalog ---------------------------------------------------------------------


def plan_body(**extra) -> dict:
    return {
        "code": "free",
        "name": "Free",
        "price_cents_monthly": 0,
        "price_cents_annual": 0,
        "included_tokens": 250_000,
        "included_tokens_per_seat": None,
        "per_seat": False,
        "overage_enabled": False,
        "default_overage_cap_micros": 0,
        "trial_days": 0,
        "models": ["grt-nano"],
        "limits": {"max_conversations": 50},
        **extra,
    }


CATALOG = {
    "data": [
        plan_body(),
        plan_body(
            code="team",
            name="Team",
            price_cents_monthly=2500,
            price_cents_annual=25_000,
            included_tokens=5_000_000,
            included_tokens_per_seat=5_000_000,
            per_seat=True,
            overage_enabled=True,
            default_overage_cap_micros=10_000_000,
            trial_days=14,
            models=["grt-nano", "grt-large"],
            limits={},
        ),
    ],
    "models": [
        {
            "id": "grt-nano",
            "label": "GRT Nano",
            "multiplier": 1,
            "input_usd_per_m": 0.5,
            "output_usd_per_m": 1.5,
        },
    ],
    "default_model": "grt-nano",
}


def test_plans_catalog_is_cheapest_first_with_the_price_table(api, client: MatterWave) -> None:
    route = api.get("/api/plans").respond(200, json=CATALOG, headers=H)
    catalog = client.plans.list()
    assert [plan.code for plan in catalog] == ["free", "team"]
    assert catalog.default_model == "grt-nano"
    team = catalog.data[1]
    assert team.per_seat and team.included_tokens_per_seat == 5_000_000
    assert team.trial_days == 14
    assert team.limits == {}
    assert catalog.models[0].label == "GRT Nano" and catalog.models[0].id == "grt-nano"
    assert route.called


def test_change_plan_moves_to_free_and_carries_the_reason(api, client: MatterWave) -> None:
    route = api.put("/api/plan").respond(
        200,
        json={
            "org_id": "org-0001",
            "plan": "free",
            "plan_name": "Free",
            "status": "active",
            "seats": 1,
            "per_seat": False,
            "billed_seats": 1,
            "price_cents_monthly": 0,
            "price_cents_annual": 0,
            "implicit": True,
            "billing_interval": "monthly",
            "current_period_end": None,
            "cancel_at_period_end": False,
            "trial_end": None,
            "default_overage_cap_micros": 0,
            "limits": {},
            "over_capacity": 0,
            "writes_blocked": False,
            "read_only_reason": None,
            "dunning_stage": "clear",
        },
        headers=H,
    )
    entitlements = client.billing.change_plan("free", reason="offboarding")
    assert body_of(route) == {"plan": "free", "reason": "offboarding"}
    assert entitlements.plan == "free" and entitlements.writes_blocked is False
    with pytest.raises(ValueError, match="plan"):
        client.billing.change_plan("")
    with pytest.raises(ValueError, match="500"):
        client.billing.change_plan("free", reason="x" * 501)
    assert route.call_count == 1
    # A paid code here is the 402 pointing at checkout (a plain APIError;
    # the error table has no 402 class of its own).
    api.put("/api/plan").respond(
        402,
        json={
            "detail": "Start a subscription to move to Team.",
            "plan": "team",
            "checkout_url": "https://checkout.stripe.com/x",
        },
        headers=H,
    )
    with pytest.raises(APIError, match="subscription"):
        client.billing.change_plan("team")


def test_plan_history_is_the_append_only_trail(api, client: MatterWave) -> None:
    route = api.get("/api/plan/history").respond(
        200,
        json={
            "data": [
                {
                    "from_plan": "team",
                    "to_plan": "free",
                    "from_status": "active",
                    "to_status": "active",
                    "kind": "downgrade",
                    "actor": "stripe:evt_123",
                    "reason": None,
                    "metadata": {"event": "customer.subscription.deleted"},
                    "created_at": "2026-10-31T09:00:00+00:00",
                }
            ]
        },
        headers=H,
    )
    history = client.billing.plan_history()
    event = history.data[0]
    assert event.to_plan == "free" and event.from_plan == "team"
    assert event.actor == "stripe:evt_123"
    assert event.metadata == {"event": "customer.subscription.deleted"}
    assert event.reason is None
    assert route.called


# --- Stripe ------------------------------------------------------------------------------


def test_checkout_sends_the_plan_and_the_interval(api, client: MatterWave) -> None:
    route = api.post("/api/billing/checkout").respond(
        200, json={"url": "https://checkout.stripe.com/c/pay/x"}, headers=H
    )
    link = client.billing.checkout("team", annual=True)
    assert body_of(route) == {"plan": "team", "annual": True}
    assert link.url.startswith("https://checkout.stripe.com/")
    client.billing.checkout("team")
    assert body_of(route) == {"plan": "team", "annual": False}
    with pytest.raises(ValueError, match="plan"):
        client.billing.checkout("")


def test_portal_answers_a_url(api, client: MatterWave) -> None:
    route = api.post("/api/billing/portal").respond(
        200, json={"url": "https://billing.stripe.com/p/session/x"}, headers=H
    )
    assert client.billing.portal().url.startswith("https://billing.stripe.com/")
    assert sent(route).content == b""


def test_status_is_readable_by_any_member_and_keeps_the_plan_facts(api, client: MatterWave) -> None:
    route = api.get("/api/billing/status").respond(
        200,
        json={
            "org_id": "org-0001",
            "plan": "team",
            "plan_name": "Team",
            "status": "past_due",
            "seats": 4,
            "per_seat": True,
            "billed_seats": 4,
            "price_cents_monthly": 2500,
            "price_cents_annual": 25_000,
            "implicit": False,
            "billing_interval": "monthly",
            "current_period_end": "2026-11-30T00:00:00+00:00",
            "cancel_at_period_end": False,
            "trial_end": None,
            "default_overage_cap_micros": 10_000_000,
            "limits": {},
            "over_capacity": 0,
            "writes_blocked": False,
            "read_only_reason": None,
            "dunning_stage": "reminder",
            "billing_enabled": True,
            "collection_method": "charge_automatically",
            "days_until_due": 3,
            "dunning": {"stage": "reminder", "next_notice_at": "2026-11-01T00:00:00+00:00"},
        },
        headers=H,
    )
    status = client.billing.status()
    assert sent(route).headers["authorization"] == f"Bearer {API_KEY}"
    assert status.entitlements.plan == "team"
    assert status.entitlements.status == "past_due"
    assert status.billing_enabled is True
    assert status.collection_method == "charge_automatically"
    assert status.days_until_due == 3
    assert status.dunning is not None and status.dunning.stage == "reminder"


def test_invoices_are_newest_first_with_their_links(api, client: MatterWave) -> None:
    route = api.get("/api/billing/invoices").respond(
        200,
        json={
            "data": [
                {
                    "id": "in-0001",
                    "number": "MW-0007",
                    "status": "open",
                    "collection_method": "send_invoice",
                    "currency": "usd",
                    "amount_due_micros": 25_000_000,
                    "amount_remaining_micros": 25_000_000,
                    "due_at": "2026-11-14T00:00:00+00:00",
                    "paid_at": None,
                    "hosted_invoice_url": "https://invoice.stripe.com/i/x",
                    "invoice_pdf": "https://invoice.stripe.com/i/x/pdf",
                    "issued_at": "2026-10-31T00:00:00+00:00",
                }
            ]
        },
        headers=H,
    )
    invoices = client.billing.invoices()
    assert route.called
    invoice = invoices.data[0]
    assert invoice.number == "MW-0007" and invoice.status == "open"
    assert invoice.amount_due_micros == 25_000_000
    assert invoice.paid_at is None and invoice.due_at.endswith("+00:00")
    assert invoice.invoice_pdf.endswith("/pdf")


def test_a_bad_plan_code_is_the_servers_400(api, client: MatterWave) -> None:
    api.post("/api/billing/checkout").respond(
        400, json={"detail": "No such plan: nope."}, headers=H
    )
    with pytest.raises(BadRequestError, match="No such plan"):
        client.billing.checkout("nope")


# --- async ----------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_async_billing_makes_the_same_requests(api, async_client: AsyncMatterWave) -> None:
    api.get("/api/plans").respond(200, json=CATALOG, headers=H)
    api.put("/api/plan").respond(
        200,
        json={
            "org_id": "org-0001",
            "plan": "free",
            "plan_name": "Free",
            "status": "active",
            "seats": 1,
            "per_seat": False,
            "billed_seats": 1,
            "price_cents_monthly": 0,
            "price_cents_annual": 0,
            "implicit": True,
            "billing_interval": "monthly",
            "current_period_end": None,
            "cancel_at_period_end": False,
            "trial_end": None,
            "default_overage_cap_micros": 0,
            "limits": {},
            "over_capacity": 0,
            "writes_blocked": True,
            "read_only_reason": "Past the Free conversation cap",
            "dunning_stage": "clear",
        },
        headers=H,
    )
    api.get("/api/plan/history").respond(200, json={"data": []}, headers=H)
    api.post("/api/billing/checkout").respond(
        200, json={"url": "https://checkout.stripe.com/x"}, headers=H
    )
    api.post("/api/billing/portal").respond(
        200, json={"url": "https://billing.stripe.com/x"}, headers=H
    )
    api.get("/api/billing/status").respond(
        200,
        json={
            "org_id": "org-0001",
            "plan": "free",
            "plan_name": "Free",
            "status": "active",
            "seats": 1,
            "per_seat": False,
            "billed_seats": 1,
            "price_cents_monthly": 0,
            "price_cents_annual": 0,
            "implicit": True,
            "billing_interval": "monthly",
            "current_period_end": None,
            "cancel_at_period_end": False,
            "trial_end": None,
            "default_overage_cap_micros": 0,
            "limits": {},
            "over_capacity": 0,
            "writes_blocked": False,
            "read_only_reason": None,
            "dunning_stage": "clear",
            "billing_enabled": False,
            "collection_method": "charge_automatically",
            "days_until_due": None,
            "dunning": None,
        },
        headers=H,
    )
    api.get("/api/billing/invoices").respond(200, json={"data": []}, headers=H)
    async with async_client as c:
        assert (await c.plans.list()).data[1].code == "team"
        moved = await c.billing.change_plan("free")
        assert moved.writes_blocked is True  # the data stays, writes stop
        assert len(await c.billing.plan_history()) == 0
        assert (await c.billing.checkout("team", annual=True)).url
        assert (await c.billing.portal()).url
        status = await c.billing.status()
        assert status.billing_enabled is False and status.dunning is None
        assert len(await c.billing.invoices()) == 0
