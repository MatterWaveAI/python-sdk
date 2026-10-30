"""The small families 0.3 added: notifications (the bell), announcements
(the banner), entitlements (the plan facts) and conversation reports. Each
test answers with the body the route in api/matterwave sends and checks
the request the client made and what it parsed.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

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


# --- the admin side of the announcements -------------------------------------------

ANN_ID = "ann-0001"


def admin_announcement_row(aid: str, *, state: str = "active") -> dict:
    return {
        "id": aid,
        "message": "The model gateway is degraded; turns may be slow.",
        "severity": "warning",
        "link": "https://status.example/incidents/1",
        "starts_at": "2026-10-30T08:00:00+00:00",
        "ends_at": "2026-10-31T08:00:00+00:00",
        "dismissible": True,
        "created_by": "op-0001",
        "created_at": "2026-10-30T07:59:00+00:00",
        "updated_by": "op-0001",
        "updated_at": "2026-10-30T07:59:00+00:00",
        "state": state,
    }


def test_admin_announcements_list_reads_every_column(api, client: MatterWave) -> None:
    route = api.get("/api/admin/announcements").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": [
                    admin_announcement_row("ann-0001"),
                    admin_announcement_row("ann-0002", state="scheduled"),
                    admin_announcement_row("ann-0003", state="ended"),
                ]
            },
            headers=H,
        )
    )
    listed = client.admin.announcements.list()
    assert listed.request_id == REQUEST_ID
    assert [a.state for a in listed] == ["active", "scheduled", "ended"]
    row = listed.data[0]
    assert row.created_by == "op-0001" and row.updated_by == "op-0001"
    assert row.starts_at.endswith("+00:00") and row.ends_at is not None
    assert row.severity == "warning" and row.dismissible
    assert sent(route).method == "GET"
    assert sent(route).url.path == "/api/admin/announcements"


def test_admin_announcements_create_sends_the_operator_s_body(
    api, client: MatterWave
) -> None:
    route = api.post("/api/admin/announcements").mock(
        return_value=httpx.Response(201, json=admin_announcement_row(ANN_ID), headers=H)
    )
    row = client.admin.announcements.create(
        "The model gateway is degraded; turns may be slow.",
        severity="warning",
        link="https://status.example/incidents/1",
        starts_at=datetime(2026, 10, 30, 8, 0, tzinfo=timezone.utc),
        dismissible=True,
    )
    assert row.id == ANN_ID and row.state == "active"
    assert row.created_by == "op-0001"
    assert row.request_id == REQUEST_ID
    assert sent(route).method == "POST"
    assert sent(route).url.path == "/api/admin/announcements"
    assert json.loads(sent(route).content) == {
        "message": "The model gateway is degraded; turns may be slow.",
        "severity": "warning",
        "dismissible": True,
        "link": "https://status.example/incidents/1",
        "starts_at": "2026-10-30T08:00:00+00:00",
    }


def test_admin_announcements_create_leaves_the_defaults_to_the_server(
    api, client: MatterWave
) -> None:
    route = api.post("/api/admin/announcements").mock(
        return_value=httpx.Response(201, json=admin_announcement_row(ANN_ID), headers=H)
    )
    client.admin.announcements.create("Maintenance on Sunday.")
    assert json.loads(sent(route).content) == {
        "message": "Maintenance on Sunday.",
        "severity": "info",
        "dismissible": True,
    }


def test_admin_announcements_create_accepts_a_naive_datetime_as_the_server_does(
    api, client: MatterWave
) -> None:
    # The route reads a timestamp without a zone as UTC, which is what the
    # admin page sends; the client sends it as it is rather than refuse it.
    route = api.post("/api/admin/announcements").mock(
        return_value=httpx.Response(201, json=admin_announcement_row(ANN_ID), headers=H)
    )
    client.admin.announcements.create(
        "Back soon.", ends_at=datetime(2026, 10, 31, 8, 0)
    )
    assert json.loads(sent(route).content)["ends_at"] == "2026-10-31T08:00:00"


def test_admin_announcements_create_refuses_what_the_route_would(
    api, client: MatterWave
) -> None:
    route = api.post("/api/admin/announcements").mock(
        return_value=httpx.Response(201, json=admin_announcement_row(ANN_ID), headers=H)
    )
    with pytest.raises(ValueError, match="severity must be one of"):
        client.admin.announcements.create("x", severity="loud")
    with pytest.raises(ValueError, match="empty"):
        client.admin.announcements.create("   ")
    with pytest.raises(ValueError, match="500 characters"):
        client.admin.announcements.create("x" * 501)
    with pytest.raises(ValueError, match="http\\(s\\) URL or a path"):
        client.admin.announcements.create("x", link="javascript:alert(1)")
    with pytest.raises(ValueError, match="http\\(s\\) URL or a path"):
        client.admin.announcements.create("x", link="//other-origin/x")
    assert not route.called


def test_admin_announcements_update_sends_only_what_changed(
    api, client: MatterWave
) -> None:
    route = api.patch(f"/api/admin/announcements/{ANN_ID}").mock(
        return_value=httpx.Response(200, json=admin_announcement_row(ANN_ID), headers=H)
    )
    row = client.admin.announcements.update(ANN_ID, severity="critical")
    assert row.id == ANN_ID and row.request_id == REQUEST_ID
    assert sent(route).method == "PATCH"
    assert sent(route).url.path == f"/api/admin/announcements/{ANN_ID}"
    assert json.loads(sent(route).content) == {"severity": "critical"}


def test_admin_announcements_update_clears_the_nullable_pair_and_ends_now(
    api, client: MatterWave
) -> None:
    route = api.patch(f"/api/admin/announcements/{ANN_ID}").mock(
        return_value=httpx.Response(200, json=admin_announcement_row(ANN_ID), headers=H)
    )
    client.admin.announcements.update(ANN_ID, link=None, ends_at=None, end_now=True)
    assert json.loads(sent(route).content) == {"link": None, "ends_at": None, "end_now": True}


def test_admin_announcements_update_refuses_an_empty_change(client: MatterWave) -> None:
    with pytest.raises(ValueError, match="update needs one of"):
        client.admin.announcements.update(ANN_ID)


def test_admin_announcements_update_404s_for_an_id_that_is_not_one(
    api, client: MatterWave
) -> None:
    api.patch("/api/admin/announcements/ann-other").mock(
        return_value=httpx.Response(404, json={"detail": "No such announcement."}, headers=H)
    )
    with pytest.raises(NotFoundError):
        client.admin.announcements.update("ann-other", message="x")


def test_admin_announcements_delete_is_a_204(api, client: MatterWave) -> None:
    route = api.delete(f"/api/admin/announcements/{ANN_ID}").mock(
        return_value=httpx.Response(204, headers=H)
    )
    assert client.admin.announcements.delete(ANN_ID) is None
    assert sent(route).method == "DELETE"
    assert sent(route).url.path == f"/api/admin/announcements/{ANN_ID}"


def test_admin_announcements_delete_404s_for_an_id_that_is_not_one(
    api, client: MatterWave
) -> None:
    api.delete("/api/admin/announcements/ann-other").mock(
        return_value=httpx.Response(404, json={"detail": "No such announcement."}, headers=H)
    )
    with pytest.raises(NotFoundError):
        client.admin.announcements.delete("ann-other")


def test_an_id_that_would_change_the_path_is_refused(client: MatterWave) -> None:
    with pytest.raises(ValueError):
        client.admin.announcements.update("../announcements", message="x")
    with pytest.raises(ValueError):
        client.admin.announcements.delete("")


@pytest.mark.asyncio
async def test_the_async_admin_announcements_match_the_sync_requests(
    api, async_client: AsyncMatterWave  # noqa: ANN001
) -> None:
    list_route = api.get("/api/admin/announcements").mock(
        return_value=httpx.Response(
            200, json={"data": [admin_announcement_row(ANN_ID)]}, headers=H
        )
    )
    create_route = api.post("/api/admin/announcements").mock(
        return_value=httpx.Response(201, json=admin_announcement_row(ANN_ID), headers=H)
    )
    patch_route = api.patch(f"/api/admin/announcements/{ANN_ID}").mock(
        return_value=httpx.Response(200, json=admin_announcement_row(ANN_ID), headers=H)
    )
    delete_route = api.delete(f"/api/admin/announcements/{ANN_ID}").mock(
        return_value=httpx.Response(204, headers=H)
    )
    listed = await async_client.admin.announcements.list()
    assert [a.id for a in listed] == [ANN_ID]
    assert (await async_client.admin.announcements.create("x")).state == "active"
    assert json.loads(create_route.calls.last.request.content)["severity"] == "info"
    await async_client.admin.announcements.update(ANN_ID, end_now=True)
    assert json.loads(patch_route.calls.last.request.content) == {"end_now": True}
    await async_client.admin.announcements.delete(ANN_ID)
    assert delete_route.calls.last.request.method == "DELETE"
    assert list_route.calls.last.request.url.path == "/api/admin/announcements"


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


# --- conversation reports ------------------------------------------------------

CONV = "c0ffee00-0000-4000-8000-000000000001"


def test_report_files_a_report_and_reads_the_acknowledgement(
    api, client: MatterWave
) -> None:
    route = api.post(f"/api/conversations/{CONV}/report").mock(
        return_value=httpx.Response(201, json={"id": "rep-0001", "kind": "unsafe"}, headers=H)
    )
    filed = client.conversations.report(
        CONV, "unsafe", message_id="m-asst", note="The reply asked for personal data."
    )
    assert filed.id == "rep-0001" and filed.kind == "unsafe"
    assert filed.request_id == REQUEST_ID
    assert sent(route).url.path == f"/api/conversations/{CONV}/report"
    assert sent(route).method == "POST"
    assert json.loads(sent(route).content) == {
        "kind": "unsafe",
        "message_id": "m-asst",
        "note": "The reply asked for personal data.",
    }


def test_report_sends_only_the_kind_when_that_is_all_there_is(
    api, client: MatterWave
) -> None:
    route = api.post(f"/api/conversations/{CONV}/report").mock(
        return_value=httpx.Response(201, json={"id": "rep-0002", "kind": "unhelpful"}, headers=H)
    )
    filed = client.conversations.report(CONV, "unhelpful")
    assert filed.kind == "unhelpful"
    assert json.loads(sent(route).content) == {"kind": "unhelpful"}


def test_report_refuses_an_unknown_kind_and_an_overlong_note(
    api, client: MatterWave
) -> None:
    route = api.post(f"/api/conversations/{CONV}/report").mock(
        return_value=httpx.Response(201, json={"id": "rep-0003", "kind": "unsafe"}, headers=H)
    )
    with pytest.raises(ValueError, match="kind must be one of"):
        client.conversations.report(CONV, "vibes")
    with pytest.raises(ValueError, match="2000 characters"):
        client.conversations.report(CONV, "unsafe", note="x" * 2001)
    assert not route.called


def test_report_404s_for_a_conversation_that_is_not_the_caller_s(
    api, client: MatterWave
) -> None:
    api.post("/api/conversations/c0ffee00-0000-4000-8000-000000000009/report").mock(
        return_value=httpx.Response(404, json={"detail": "No such conversation"}, headers=H)
    )
    with pytest.raises(NotFoundError):
        client.conversations.report(
            "c0ffee00-0000-4000-8000-000000000009", "unsafe"
        )


@pytest.mark.asyncio
async def test_the_async_report_sends_the_same_body(
    api, async_client: AsyncMatterWave  # noqa: ANN001
) -> None:
    route = api.post(f"/api/conversations/{CONV}/report").mock(
        return_value=httpx.Response(201, json={"id": "rep-0004", "kind": "unhelpful"}, headers=H)
    )
    filed = await async_client.conversations.report(CONV, "unhelpful", note="Refused for no reason.")
    assert filed.id == "rep-0004"
    assert json.loads(sent(route).content) == {
        "kind": "unhelpful",
        "note": "Refused for no reason.",
    }
