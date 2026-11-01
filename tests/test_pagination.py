"""Page and item iterators for every cursor-paged list: `audit.pages` /
`audit.items`, `library.search_pages` / `library.search_items`, and the
generic `pages_of` / `items_of` they are built on. Each page is fetched only
when the previous one has been consumed, and the cursor a page names is the
one the next request carries."""

from __future__ import annotations

import httpx
import pytest

from matterwave_sdk import (
    AsyncMatterWave,
    AuditPage,
    MatterWave,
    async_items_of,
    async_pages_of,
    items_of,
    pages_of,
)


def _audit_page(ids: list[str], next_cursor: str | None) -> dict:
    return {
        "data": [
            {
                "id": event_id,
                "occurred_at": "2026-10-31T09:00:00+00:00",
                "actor": "user-1",
                "actor_email": "a@example.test",
                "action": "org.sso.enforced",
                "target": None,
                "detail": {},
            }
            for event_id in ids
        ],
        "actions": ["org.sso.enforced"],
        "next_cursor": next_cursor,
    }


def _search_page(ids: list[str], next_cursor: str | None) -> dict:
    return {
        "data": [{"id": cid, "title": cid, "snippet": "<b>hit</b>"} for cid in ids],
        "query": "hit",
        "next_cursor": next_cursor,
    }


def test_audit_pages_follow_next_cursor_with_the_same_filters(api, client: MatterWave) -> None:
    route = api.get("/api/org/audit").mock(
        side_effect=[
            httpx.Response(200, json=_audit_page(["e-1", "e-2"], "cur-1")),
            httpx.Response(200, json=_audit_page(["e-3"], None)),
        ]
    )
    pages = list(client.audit.pages(event="org.sso", limit=2))
    assert [len(page) for page in pages] == [2, 1]
    first, second = (call.request.url.params for call in route.calls)
    assert dict(first) == {"event": "org.sso", "limit": "2"}
    assert dict(second) == {"event": "org.sso", "limit": "2", "cursor": "cur-1"}


def test_audit_items_walk_every_event_and_stop_fetching_when_the_caller_stops(
    api, client: MatterWave
) -> None:
    route = api.get("/api/org/audit").mock(
        side_effect=[
            httpx.Response(200, json=_audit_page(["e-1", "e-2"], "cur-1")),
            httpx.Response(200, json=_audit_page(["e-3"], None)),
        ]
    )
    items = client.audit.items(actor="a@example.test")
    assert next(items).id == "e-1"
    assert next(items).id == "e-2"
    assert route.call_count == 1
    assert [event.id for event in items] == ["e-3"]
    assert route.call_count == 2


def test_audit_items_refuse_a_bad_limit_before_any_request(client: MatterWave) -> None:
    with pytest.raises(ValueError):
        next(client.audit.items(limit=0))


def test_library_search_pages_and_items_carry_the_query_and_the_cursor(
    api, client: MatterWave
) -> None:
    route = api.get("/api/search").mock(
        side_effect=[
            httpx.Response(200, json=_search_page(["c-1"], "s-1")),
            httpx.Response(200, json=_search_page(["c-2"], None)),
        ]
    )
    hits = [hit.conversation.id for hit in client.library.search_items("hit", limit=1)]
    assert hits == ["c-1", "c-2"]
    first, second = (call.request.url.params for call in route.calls)
    assert dict(first) == {"q": "hit", "limit": "1"}
    assert dict(second) == {"q": "hit", "limit": "1", "cursor": "s-1"}


def test_library_search_pages_stop_on_a_page_with_no_cursor(api, client: MatterWave) -> None:
    route = api.get("/api/search").respond(200, json=_search_page(["c-1", "c-2"], None))
    pages = list(client.library.search_pages("hit"))
    assert len(pages) == 1
    assert pages[0].query == "hit"
    assert route.call_count == 1


@pytest.mark.asyncio
async def test_the_async_audit_and_search_iterators_walk_the_same_way(
    api, async_client: AsyncMatterWave
) -> None:
    api.get("/api/org/audit").mock(
        side_effect=[
            httpx.Response(200, json=_audit_page(["e-1"], "cur-1")),
            httpx.Response(200, json=_audit_page(["e-2"], None)),
        ]
    )
    api.get("/api/search").mock(
        side_effect=[
            httpx.Response(200, json=_search_page(["c-1"], "s-1")),
            httpx.Response(200, json=_search_page(["c-2"], None)),
        ]
    )
    assert [e.id async for e in async_client.audit.items(event="org")] == ["e-1", "e-2"]
    pages = [page async for page in async_client.library.search_pages("hit")]
    assert [hit.conversation.id for page in pages for hit in page] == ["c-1", "c-2"]


def test_pages_of_and_items_of_walk_any_cursor_paged_list() -> None:
    pages = {
        None: AuditPage.from_dict(_audit_page(["e-1"], "a")),
        "a": AuditPage.from_dict(_audit_page(["e-2", "e-3"], "b")),
        "b": AuditPage.from_dict(_audit_page([], None)),
    }
    asked: list[str | None] = []

    def fetch(cursor: str | None) -> AuditPage:
        asked.append(cursor)
        return pages[cursor]

    assert [event.id for event in items_of(pages_of(fetch))] == ["e-1", "e-2", "e-3"]
    assert asked == [None, "a", "b"]


@pytest.mark.asyncio
async def test_the_async_helpers_walk_the_same_way() -> None:
    pages = {
        None: AuditPage.from_dict(_audit_page(["e-1"], "a")),
        "a": AuditPage.from_dict(_audit_page(["e-2"], None)),
    }

    async def fetch(cursor: str | None) -> AuditPage:
        return pages[cursor]

    assert [event.id async for event in async_items_of(async_pages_of(fetch))] == ["e-1", "e-2"]
