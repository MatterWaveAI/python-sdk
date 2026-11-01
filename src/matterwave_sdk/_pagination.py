"""Walking a cursor-paged list: every page, or every item of every page.

The API pages its long lists the same way (`{"data": [...], "next_cursor":
...}`, the next request carrying `cursor=<next_cursor>`, None on the last
page), so one walk serves every one of them:

    from matterwave_sdk import items_of, pages_of

    pages = pages_of(lambda cursor: client.audit.list(event="org.sso", cursor=cursor))
    for event in items_of(pages):
        print(event.action)

`client.conversations`, `client.audit` and `client.library` use it for
their own `pages` / `items` methods; it is public for a list this client
does not wrap yet. Each page is fetched only when the previous one has been
consumed, so stopping early fetches nothing more.
"""

from __future__ import annotations

from collections.abc import AsyncIterable, AsyncIterator, Awaitable, Callable, Iterable, Iterator
from typing import Protocol, TypeVar

ItemT = TypeVar("ItemT")
ItemT_co = TypeVar("ItemT_co", covariant=True)


class CursorPage(Protocol[ItemT_co]):
    """What a page needs for the walk: its items, and the cursor of the
    page after it (None on the last)."""

    @property
    def next_cursor(self) -> str | None: ...

    def __iter__(self) -> Iterator[ItemT_co]: ...


PageT = TypeVar("PageT", bound=CursorPage[object])


def pages_of(fetch: Callable[[str | None], PageT]) -> Iterator[PageT]:
    """Every page, first to last. `fetch(None)` is the first page and
    `fetch(cursor)` each later one."""
    cursor: str | None = None
    while True:
        page = fetch(cursor)
        yield page
        cursor = page.next_cursor
        if cursor is None:
            return


def items_of(pages: Iterable[Iterable[ItemT]]) -> Iterator[ItemT]:
    """Every item of every page, one at a time."""
    for page in pages:
        yield from page


async def async_pages_of(fetch: Callable[[str | None], Awaitable[PageT]]) -> AsyncIterator[PageT]:
    """`pages_of` for a coroutine `fetch`."""
    cursor: str | None = None
    while True:
        page = await fetch(cursor)
        yield page
        cursor = page.next_cursor
        if cursor is None:
            return


async def async_items_of(pages: AsyncIterable[Iterable[ItemT]]) -> AsyncIterator[ItemT]:
    """`items_of` for pages that arrive asynchronously."""
    async for page in pages:
        for item in page:
            yield item
