"""Building a response dataclass from the JSON a route sent, by its fields.

The models in `models.py` each spell out their own `from_dict`. The account
and organisation models are many and flat, so they declare their fields
and inherit `from_dict` from `Wire` instead, with the same rules the
hand-written ones follow: a field the body lacks, or sends with the wrong
type, gets the type's empty value (None for an optional field), and a field
the body has but the model does not name is dropped, so a field the
platform adds later is ignored rather than refused.
"""

from __future__ import annotations

import dataclasses
import types
import typing
from typing import Any, TypeVar, Union

WireT = TypeVar("WireT", bound="Wire")


def _empty(hint: Any) -> Any:
    origin = typing.get_origin(hint)
    if origin in (Union, types.UnionType) and type(None) in typing.get_args(hint):
        return None
    if hint is str:
        return ""
    if hint is int:
        return 0
    if hint is float:
        return 0.0
    if hint is bool:
        return False
    if origin is list:
        return []
    if origin is dict:
        return {}
    if isinstance(hint, type) and issubclass(hint, Wire):
        return hint.from_dict({})
    return None


def _coerce(hint: Any, value: Any) -> Any:
    origin = typing.get_origin(hint)
    if origin in (Union, types.UnionType):
        if value is None:
            return None
        for arm in typing.get_args(hint):
            if arm is not type(None):
                coerced = _coerce(arm, value)
                if coerced is not _MISSING:
                    return coerced
        return None
    if hint is Any:
        return value
    if hint is bool:
        return value if isinstance(value, bool) else _MISSING
    if hint is int:
        return value if isinstance(value, int) and not isinstance(value, bool) else _MISSING
    if hint is float:
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value)
        return _MISSING
    if hint is str:
        return value if isinstance(value, str) else _MISSING
    if origin is list:
        if not isinstance(value, list):
            return _MISSING
        (item,) = typing.get_args(hint) or (Any,)
        items = [_coerce(item, v) for v in value]
        return [v for v in items if v is not _MISSING]
    if origin is dict:
        return value if isinstance(value, dict) else _MISSING
    if isinstance(hint, type) and hasattr(hint, "from_dict"):
        # A Wire model, or one of the hand-written models with its own reader.
        return hint.from_dict(value) if isinstance(value, dict) else _MISSING
    return value


_MISSING = object()


@dataclasses.dataclass(frozen=True)
class Wire:
    """A response model whose `from_dict` reads its own fields. Subclasses
    are frozen dataclasses; a `request_id` field, when declared, is filled
    from the response's `X-Request-Id` rather than the body."""

    @classmethod
    def from_dict(
        cls: type[WireT], data: dict[str, Any], *, request_id: str | None = None
    ) -> WireT:
        hints = typing.get_type_hints(cls)
        values: dict[str, Any] = {}
        for field in dataclasses.fields(cls):
            if not field.init:
                continue
            if field.name == "request_id":
                values["request_id"] = request_id
                continue
            hint = hints[field.name]
            coerced = _coerce(hint, data.get(field.name)) if field.name in data else _MISSING
            values[field.name] = _empty(hint) if coerced is _MISSING else coerced
        return cls(**values)
