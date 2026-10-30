"""The small families 0.3 added: notifications (the bell), announcements
(the banner) and entitlements (the plan facts). Each test answers with the
body the route in api/matterwave sends and checks the request the client
made and what it parsed.
"""

from __future__ import annotations

import httpx
import pytest
from conftest import REQUEST_ID

from matterwave_sdk import (
    AsyncMatterWave,
    Entitlements,
    MatterWave,
    NotFoundError,
)

H = {"X-Request-Id": REQUEST_ID}


def sent(route) -> httpx.Request:
    return route.calls.last.request


def notification_row(nid: str, *, read: bool = False) -> dict:
    return {
        "id": nid,
        "kind": "budget.warn",
        "title": "80% of this period's included tokens used",
        "body": "The organisation is approaching the tokens its plan includes.",
        "link": "/settings/usage",
        "created_at": "2026-10-30T09:00:00+00:00",
        "read_at": "2026-10-30T10:00:00+00:00" if read else None,
    }


# --- notifications -----------------------------------------------------------------


def test_notifications_list_reads_the_rows_and_the_unread_count(
    api, client: MatterWave
) -> None:
    route = api.get("/api/me/notifications").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": [notification_row("n-1"), notification_row("n-2", read=True)],
                "unread_count": 1,
            },
            headers=H,
        )
    )
    listed = client.notifications.list()
    assert listed.request_id == REQUEST_ID
    assert listed.unread_count == 1
    assert [n.id for n in listed] == ["n-1", "n-2"]
    first, second = listed.data
    assert first.read_at is None and second.read_at is not None
    assert first.link == "/settings/usage" and first.kind == "budget.warn"
    # No query when nothing was asked for.
    assert dict(route.calls.last.request.url.params) == {}
    assert sent(route).method == "GET"
    assert sent(route).url.path == "/api/me/notifications"


def test_notifications_list_sends_the_unread_flag_and_limit(api, client: MatterWave) -> None:
    route = api.get("/api/me/notifications").mock(
        return_value=httpx.Response(200, json={"data": [], "unread_count": 0}, headers=H)
    )
    client.notifications.list(unread=True, limit=10)
    assert dict(route.calls[0].request.url.params) == {"unread": "1", "limit": "10"}
    client.notifications.list(unread=False)
    assert dict(route.calls[1].request.url.params) == {"unread": "0"}


def test_notifications_list_refuses_a_limit_outside_the_route_s_range(
    client: MatterWave,
) -> None:
    with pytest.raises(ValueError):
        client.notifications.list(limit=0)
    with pytest.raises(ValueError):
        client.notifications.list(limit=101)


def test_notifications_read_marks_one_read_and_carries_the_count(
    api, client: MatterWave
) -> None:
    route = api.post("/api/me/notifications/n-1/read").mock(
        return_value=httpx.Response(
            200,
            json=notification_row("n-1", read=True) | {"unread_count": 2},
            headers=H,
        )
    )
    row = client.notifications.read("n-1")
    assert row.id == "n-1" and row.read_at is not None
    assert row.unread_count == 2
    assert row.request_id == REQUEST_ID
    assert sent(route).url.path == "/api/me/notifications/n-1/read"
    assert sent(route).method == "POST"


def test_notifications_read_404s_for_another_person_s_row(api, client: MatterWave) -> None:
    api.post("/api/me/notifications/n-other/read").mock(
        return_value=httpx.Response(
            404, json={"detail": "No such notification."}, headers=H
        )
    )
    with pytest.raises(NotFoundError):
        client.notifications.read("n-other")


def test_notifications_read_all_answers_how_many_it_marked(api, client: MatterWave) -> None:
    route = api.post("/api/me/notifications/read-all").mock(
        return_value=httpx.Response(200, json={"read": 3, "unread_count": 0}, headers=H)
    )
    done = client.notifications.read_all()
    assert done.read == 3 and done.unread_count == 0
    assert done.request_id == REQUEST_ID
    assert sent(route).url.path == "/api/me/notifications/read-all"


@pytest.mark.asyncio
async def test_the_async_notifications_match_the_sync_requests(
    api, async_client: AsyncMatterWave  # noqa: ANN001
) -> None:
    list_route = api.get("/api/me/notifications").mock(
        return_value=httpx.Response(
            200,
            json={"data": [notification_row("n-1")], "unread_count": 1},
            headers=H,
        )
    )
    api.post("/api/me/notifications/n-1/read").mock(
        return_value=httpx.Response(
            200, json=notification_row("n-1", read=True) | {"unread_count": 0}, headers=H
        )
    )
    api.post("/api/me/notifications/read-all").mock(
        return_value=httpx.Response(200, json={"read": 1, "unread_count": 0}, headers=H)
    )
    listed = await async_client.notifications.list(unread=True, limit=5)
    assert [n.id for n in listed] == ["n-1"] and listed.unread_count == 1
    assert dict(list_route.calls.last.request.url.params) == {"unread": "1", "limit": "5"}
    assert (await async_client.notifications.read("n-1")).unread_count == 0
    assert (await async_client.notifications.read_all()).read == 1


# --- announcements -----------------------------------------------------------------


def announcement_row(aid: str, severity: str) -> dict:
    return {
        "id": aid,
        "message": "The model gateway is degraded; turns may be slow.",
        "severity": severity,
        "link": "https://status.example/incidents/1",
        "starts_at": "2026-10-30T08:00:00+00:00",
        "ends_at": None,
        "dismissible": severity != "critical",
    }


def test_announcements_list_reads_the_active_rows(api, client: MatterWave) -> None:
    route = api.get("/api/announcements").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": [announcement_row("a-1", "critical"), announcement_row("a-2", "info")]
            },
            headers=H,
        )
    )
    listed = client.announcements.list()
    assert listed.request_id == REQUEST_ID
    assert [a.id for a in listed] == ["a-1", "a-2"]
    critical, plain = listed.data
    assert critical.severity == "critical" and not critical.dismissible
    assert critical.ends_at is None and plain.dismissible
    assert plain.link == "https://status.example/incidents/1"
    assert len(listed) == 2
    assert sent(route).url.path == "/api/announcements"


def test_announcements_list_tolerates_an_empty_banner(api, client: MatterWave) -> None:
    # Without Postgres the route answers an empty list, so a laptop boots.
    api.get("/api/announcements").mock(return_value=httpx.Response(200, json={"data": []}))
    assert len(client.announcements.list()) == 0


@pytest.mark.asyncio
async def test_the_async_announcements_read_the_same_body(
    api, async_client: AsyncMatterWave  # noqa: ANN001
) -> None:
    route = api.get("/api/announcements").mock(
        return_value=httpx.Response(
            200, json={"data": [announcement_row("a-1", "warning")]}, headers=H
        )
    )
    listed = await async_client.announcements.list()
    assert listed.data[0].severity == "warning"
    assert sent(route).method == "GET"


# --- entitlements ------------------------------------------------------------------


def entitlements_body() -> dict:
    return {
        "org_id": "org-0001",
        "plan": "team",
        "plan_name": "Team",
        "status": "active",
        "seats": 5,
        "per_seat": True,
        "billed_seats": 5,
        "price_cents_monthly": 2500,
        "price_cents_annual": 25000,
        "implicit": False,
        "billing_interval": "monthly",
        "current_period_end": "2026-11-01T00:00:00+00:00",
        "cancel_at_period_end": False,
        "trial_end": None,
        "default_overage_cap_micros": 2_500_000,
        "limits": {
            "included_tokens": 5_000_000,
            "included_tokens_per_seat": 1_000_000,
            "overage_enabled": True,
            "budget_duration": "monthly",
            "tpm_limit": None,
            "rpm_limit": 600,
            "max_parallel_requests": None,
            "max_conversations": 1000,
            "max_seats": 50,
            "allowed_models": ["grt-nano", "grt-max"],
            "features": {"web_search": True},
        },
        "over_capacity": 0,
        "writes_blocked": False,
        "read_only_reason": None,
        "dunning_stage": "clear",
    }


def test_entitlements_get_reads_the_plan_facts(api, client: MatterWave) -> None:
    route = api.get("/api/entitlements").mock(
        return_value=httpx.Response(200, json=entitlements_body(), headers=H)
    )
    ent = client.entitlements.get()
    assert isinstance(ent, Entitlements)
    assert ent.request_id == REQUEST_ID
    assert ent.plan == "team" and ent.plan_name == "Team"
    assert ent.seats == 5 and ent.billed_seats == 5 and ent.per_seat
    assert not ent.implicit and ent.billing_interval == "monthly"
    assert ent.current_period_end == "2026-11-01T00:00:00+00:00"
    assert ent.default_overage_cap_micros == 2_500_000
    limits = ent.limits
    assert limits.included_tokens == 5_000_000
    assert limits.included_tokens_per_seat == 1_000_000
    assert limits.overage_enabled
    assert limits.rpm_limit == 600 and limits.tpm_limit is None
    assert limits.max_conversations == 1000 and limits.max_seats == 50
    assert limits.allowed_models == ["grt-nano", "grt-max"]
    assert limits.features == {"web_search": True}
    assert not ent.writes_blocked and ent.read_only_reason is None
    assert sent(route).url.path == "/api/entitlements"


def test_entitlements_get_reports_a_downgraded_org(api, client: MatterWave) -> None:
    # The free read: what the route answers for an org over its cap.
    over = entitlements_body() | {
        "plan": "free",
        "plan_name": "Free",
        "status": "active",
        "seats": 5,
        "per_seat": False,
        "billed_seats": 1,
        "implicit": True,
        "over_capacity": 12,
        "writes_blocked": True,
        "read_only_reason": "This organisation holds 12 conversations more than the "
        "Free plan's limit of 200.",
    }
    api.get("/api/entitlements").mock(
        return_value=httpx.Response(200, json=over, headers=H)
    )
    ent = client.entitlements.get()
    assert ent.writes_blocked and ent.over_capacity == 12
    assert ent.read_only_reason is not None and ent.read_only_reason.startswith(
        "This organisation"
    )


def test_entitlements_get_tolerates_a_sparse_body(api, client: MatterWave) -> None:
    # Every field reads as its default when the gateway annotated nothing.
    api.get("/api/entitlements").mock(
        return_value=httpx.Response(200, json={"org_id": "org-0001"}, headers=H)
    )
    ent = client.entitlements.get()
    assert ent.status == "active" and ent.seats == 1
    assert not ent.writes_blocked and ent.dunning_stage == "clear"
    assert ent.limits.included_tokens == 0
    assert ent.limits.allowed_models is None and ent.limits.features == {}


@pytest.mark.asyncio
async def test_the_async_entitlements_read_the_same_body(
    api, async_client: AsyncMatterWave  # noqa: ANN001
) -> None:
    route = api.get("/api/entitlements").mock(
        return_value=httpx.Response(200, json=entitlements_body(), headers=H)
    )
    ent = await async_client.entitlements.get()
    assert ent.plan == "team" and ent.limits.overage_enabled
    assert sent(route).method == "GET"
