"""`Wire`, the field-driven `from_dict` the account and organisation models
share: the rules the hand-written models follow, applied by type."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from matterwave_sdk._wire import Wire


@dataclass(frozen=True)
class Item(Wire):
    id: str
    count: int
    on: bool
    ratio: float


@dataclass(frozen=True)
class Page(Wire):
    data: list[Item]
    item: Item
    cursor: str | None
    extra: dict[str, Any]
    tags: list[str]
    request_id: str | None = field(default=None, compare=False)


def test_fields_are_read_by_type_and_the_rest_dropped() -> None:
    page = Page.from_dict(
        {
            "data": [{"id": "a", "count": 2, "on": True, "ratio": 1}, "not an object"],
            "item": {"id": "b", "count": True, "on": "yes", "ratio": "x"},
            "cursor": "c1",
            "extra": {"k": 1},
            "tags": ["x", 3],
            "added_later": 1,
        },
        request_id="req-1",
    )
    assert page.data == [Item(id="a", count=2, on=True, ratio=1.0)]
    # A field with the wrong type gets the type's empty value: a bool is not a count.
    assert page.item == Item(id="b", count=0, on=False, ratio=0.0)
    assert (page.cursor, page.extra, page.tags, page.request_id) == ("c1", {"k": 1}, ["x"], "req-1")


def test_a_missing_field_is_empty_and_an_optional_one_none() -> None:
    page = Page.from_dict({"cursor": 5})
    assert page == Page(
        data=[], item=Item(id="", count=0, on=False, ratio=0.0), cursor=None, extra={}, tags=[]
    )
