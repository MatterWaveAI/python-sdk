"""Structured outputs: `chat.completions.parse(response_model=Model)` --
create, then the answer's text as JSON, validated by the caller's model."""

from __future__ import annotations

import json

import httpx
import pytest
from conftest import completion_body

from matterwave_sdk import AsyncMatterWave, MatterWave, ParseError


class Row:
    """A stand-in pydantic model: `model_validate` is the v2 protocol."""

    def __init__(self, name: str, quantity: int) -> None:
        self.name = name
        self.quantity = quantity

    @classmethod
    def model_validate(cls, data: object) -> Row:
        if not isinstance(data, dict) or not isinstance(data.get("name"), str):
            raise ValueError("name must be a string")
        return cls(data["name"], int(data.get("quantity", 0)))

    @classmethod
    def refusal(cls, data: object) -> Row:
        return cls.model_validate(data)


class LegacyRow:
    """The pydantic v1 protocol: `parse_obj`."""

    def __init__(self, name: str) -> None:
        self.name = name

    @classmethod
    def parse_obj(cls, data: object) -> LegacyRow:
        if not isinstance(data, dict):
            raise ValueError("not an object")
        return cls(str(data.get("name", "")))


def _row_completion(content: str | None) -> dict:
    body = completion_body()
    body["choices"][0]["message"] = {"role": "assistant", "content": content}
    return body


def test_a_valid_answer_validates_into_the_model(api, client: MatterWave) -> None:
    api.post("/v1/chat/completions").mock(
        return_value=httpx.Response(
            200, json=_row_completion(json.dumps({"name": "bolt", "quantity": 12}))
        )
    )
    row = client.chat.completions.parse(
        messages=[{"role": "user", "content": "parts"}], response_model=Row
    )
    assert isinstance(row, Row)
    assert row.name == "bolt"
    assert row.quantity == 12


def test_the_request_asks_for_a_json_object(api, client: MatterWave) -> None:
    route = api.post("/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=_row_completion(json.dumps({"name": "bolt"})))
    )
    client.chat.completions.parse(
        messages=[{"role": "user", "content": "parts"}], response_model=Row
    )
    sent = json.loads(route.calls.last.request.content)
    assert sent["response_format"] == {"type": "json_object"}


def test_a_caller_passed_response_format_wins(api, client: MatterWave) -> None:
    route = api.post("/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=_row_completion(json.dumps({"name": "bolt"})))
    )
    schema = {"type": "json_schema", "json_schema": {"name": "row", "strict": True}}
    client.chat.completions.parse(
        messages=[{"role": "user", "content": "parts"}], response_model=Row, response_format=schema
    )
    sent = json.loads(route.calls.last.request.content)
    assert sent["response_format"] == schema


def test_an_answer_that_is_not_json_is_a_parse_error(api, client: MatterWave) -> None:
    api.post("/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=_row_completion("Here are the parts you asked for."))
    )
    with pytest.raises(ParseError, match="not JSON"):
        client.chat.completions.parse(
            messages=[{"role": "user", "content": "parts"}], response_model=Row
        )


def test_a_tool_call_with_no_content_is_a_parse_error(api, client: MatterWave) -> None:
    body = completion_body()
    body["choices"][0]["message"] = {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {"id": "call_0", "type": "function", "function": {"name": "parts", "arguments": "{}"}}
        ],
    }
    api.post("/v1/chat/completions").mock(return_value=httpx.Response(200, json=body))
    with pytest.raises(ParseError, match="no content to parse"):
        client.chat.completions.parse(
            messages=[{"role": "user", "content": "parts"}], response_model=Row
        )


def test_a_schema_refusal_raises_the_models_own_error(api, client: MatterWave) -> None:
    api.post("/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=_row_completion(json.dumps({"quantity": 3})))
    )
    with pytest.raises(ValueError, match="name must be a string"):
        client.chat.completions.parse(
            messages=[{"role": "user", "content": "parts"}], response_model=Row
        )


def test_the_pydantic_v1_protocol_validates_too(api, client: MatterWave) -> None:
    api.post("/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=_row_completion(json.dumps({"name": "bolt"})))
    )
    row = client.chat.completions.parse(
        messages=[{"role": "user", "content": "parts"}], response_model=LegacyRow
    )
    assert isinstance(row, LegacyRow)
    assert row.name == "bolt"


def test_a_model_with_neither_protocol_is_refused_before_any_request(
    api, client: MatterWave
) -> None:
    route = api.post("/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=_row_completion(json.dumps({"name": "bolt"})))
    )
    with pytest.raises(TypeError, match="model_validate"):
        client.chat.completions.parse(
            messages=[{"role": "user", "content": "parts"}],
            response_model=dict,  # type: ignore[arg-type]
        )
    assert route.call_count == 0


@pytest.mark.asyncio
async def test_the_async_parse_matches(api, async_client: AsyncMatterWave) -> None:
    api.post("/v1/chat/completions").mock(
        return_value=httpx.Response(
            200, json=_row_completion(json.dumps({"name": "bolt", "quantity": 12}))
        )
    )
    row = await async_client.chat.completions.parse(
        messages=[{"role": "user", "content": "parts"}], response_model=Row
    )
    assert isinstance(row, Row)
    assert row.quantity == 12


@pytest.mark.asyncio
async def test_the_async_parse_refuses_bad_json(api, async_client: AsyncMatterWave) -> None:
    api.post("/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=_row_completion("{not json"))
    )
    with pytest.raises(ParseError):
        await async_client.chat.completions.parse(
            messages=[{"role": "user", "content": "parts"}], response_model=Row
        )
