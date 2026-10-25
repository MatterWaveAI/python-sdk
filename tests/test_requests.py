"""What each operation sends: the path, the bearer, the body with the
server's defaults left to the server."""

from __future__ import annotations

import json

import httpx
import pytest
from conftest import API_KEY, REQUEST_ID, completion_body

from matterwave_sdk import ChatCompletion, MatterWave, MissingAPIKeyError, __version__


def test_create_posts_the_openai_body_with_the_bearer(api, client: MatterWave) -> None:
    route = api.post("/v1/chat/completions").mock(
        return_value=httpx.Response(
            200, json=completion_body(), headers={"X-Request-Id": REQUEST_ID}
        )
    )

    completion = client.chat.completions.create(
        messages=[{"role": "user", "content": "Say hello in five words."}],
        model="grt-nano",
        temperature=0.2,
        max_tokens=32,
        user="customer-7",
        conversation_id="0192d4c3-0000-7000-8000-000000000001",
    )

    sent = route.calls.last.request
    assert sent.headers["Authorization"] == f"Bearer {API_KEY}"
    assert sent.headers["User-Agent"] == f"matterwave-python/{__version__}"
    assert sent.headers["Content-Type"] == "application/json"
    assert json.loads(sent.content) == {
        "model": "grt-nano",
        "messages": [{"role": "user", "content": "Say hello in five words."}],
        "temperature": 0.2,
        "max_tokens": 32,
        "user": "customer-7",
        "metadata": {"conversation_id": "0192d4c3-0000-7000-8000-000000000001"},
    }
    assert isinstance(completion, ChatCompletion)
    assert completion.content == "Hello there, friend of mine."
    assert completion.usage.total_tokens == 50
    assert completion.request_id == REQUEST_ID


def test_default_headers_replace_the_clients_own_but_not_the_operations(api) -> None:
    # A client built on this one names itself: its User-Agent has to reach
    # the wire. The operation's own headers (a resume's Last-Event-ID) and
    # the bearer still come last.
    client = MatterWave(
        api_key=API_KEY,
        base_url="https://matterwave.test",
        default_headers={"User-Agent": "my-app/2.0", "X-Trace": "t-1", "Authorization": "no"},
    )
    route = api.get("/api/me").mock(
        return_value=httpx.Response(
            200, json={"subject": "u-1", "kind": "key", "roles": [], "org_id": "o-1"}
        )
    )
    client.me()
    sent = route.calls.last.request
    assert sent.headers["User-Agent"] == "my-app/2.0"
    assert sent.headers["X-Trace"] == "t-1"
    assert sent.headers["Authorization"] == f"Bearer {API_KEY}"


def test_omitted_arguments_are_omitted_from_the_body(api, client: MatterWave) -> None:
    route = api.post("/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=completion_body())
    )
    client.chat.completions.create(messages=[{"role": "user", "content": "hi"}])
    # No model: the deployment's default. No stream key: the server's False.
    assert json.loads(route.calls.last.request.content) == {
        "messages": [{"role": "user", "content": "hi"}]
    }


def test_extra_body_reaches_the_wire_for_fields_this_client_predates(
    api, client: MatterWave
) -> None:
    route = api.post("/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=completion_body())
    )
    client.chat.completions.create(
        messages=[{"role": "user", "content": "hi"}], extra_body={"seed": 7}
    )
    assert json.loads(route.calls.last.request.content)["seed"] == 7


def test_unknown_response_fields_are_tolerated(api, client: MatterWave) -> None:
    body = completion_body()
    body["system_fingerprint"] = "fp_1"
    body["choices"][0]["logprobs"] = None
    api.post("/v1/chat/completions").mock(return_value=httpx.Response(200, json=body))
    completion = client.chat.completions.create(messages=[{"role": "user", "content": "hi"}])
    assert completion.content == "Hello there, friend of mine."


def test_tools_reach_the_wire_and_tool_calls_come_back_typed(api, client: MatterWave) -> None:
    body = completion_body()
    body["choices"][0]["message"] = {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": "call_0",
                "type": "function",
                "function": {"name": "weather", "arguments": "{\"city\":\"Oslo\"}"},
            }
        ],
    }
    body["choices"][0]["finish_reason"] = "tool_calls"
    route = api.post("/v1/chat/completions").mock(return_value=httpx.Response(200, json=body))
    tool = {
        "type": "function",
        "function": {"name": "weather", "parameters": {"type": "object"}},
    }
    completion = client.chat.completions.create(
        messages=[{"role": "user", "content": "Weather in Oslo?"}],
        tools=[tool],
        tool_choice="required",
        response_format={"type": "text"},
    )
    sent = json.loads(route.calls.last.request.content)
    assert sent["tools"] == [tool]
    assert sent["tool_choice"] == "required"
    assert sent["response_format"] == {"type": "text"}
    assert completion.content == ""
    assert completion.choices[0].finish_reason == "tool_calls"
    assert [c.function.name for c in completion.tool_calls] == ["weather"]
    assert completion.tool_calls[0].function.arguments == "{\"city\":\"Oslo\"}"
    assert completion.tool_calls[0].id == "call_0"


def test_embeddings_create(api, client: MatterWave) -> None:
    route = api.post("/v1/embeddings").mock(
        return_value=httpx.Response(
            200,
            json={
                "object": "list",
                "data": [
                    {"object": "embedding", "index": 0, "embedding": [0.1, 0.2]},
                    {"object": "embedding", "index": 1, "embedding": [0.3, 0.4]},
                ],
                "model": "embed-small",
                "usage": {"prompt_tokens": 6, "total_tokens": 6},
            },
            headers={"X-Request-Id": REQUEST_ID},
        )
    )
    result = client.embeddings.create(["one", "two"], dimensions=2)
    assert json.loads(route.calls.last.request.content) == {"input": ["one", "two"], "dimensions": 2}
    assert [e.embedding for e in result] == [[0.1, 0.2], [0.3, 0.4]]
    assert result.model == "embed-small"
    assert result.usage.prompt_tokens == 6
    assert result.request_id == REQUEST_ID
    with pytest.raises(ValueError):
        client.embeddings.create([])


def test_models_list_and_retrieve(api, client: MatterWave) -> None:
    api.get("/v1/models").mock(
        return_value=httpx.Response(
            200,
            json={
                "object": "list",
                "data": [
                    {"id": "grt-nano", "object": "model", "created": 0, "owned_by": "matterwave"},
                    {"id": "grt-nano-pro", "object": "model", "created": 1700000000, "owned_by": "matterwave"},
                ],
            },
        )
    )
    api.get("/v1/models/grt-nano").mock(
        return_value=httpx.Response(
            200, json={"id": "grt-nano", "object": "model", "created": 0, "owned_by": "matterwave"}
        )
    )

    models = client.models.list()
    assert [m.id for m in models] == ["grt-nano", "grt-nano-pro"]
    assert len(models) == 2
    assert client.models.retrieve("grt-nano").owned_by == "matterwave"


def test_index_needs_no_key(api) -> None:
    route = api.get("/v1").mock(
        return_value=httpx.Response(
            200,
            json={
                "object": "api",
                "name": "MatterWave",
                "api_version": "v1",
                "release": "0.1.0",
                "compatible_with": "openai",
                "endpoints": ["/v1/chat/completions"],
                "capabilities": {"embeddings": {"enabled": False, "model": None}},
                "stability": {
                    "stable": True,
                    "promise": "additive",
                    "preview_prefix": "/v1/preview/",
                },
                "extensions": {},
                "docs": "/docs",
            },
        )
    )
    client = MatterWave(api_key=None, base_url="https://matterwave.test")
    index = client.index()
    assert "Authorization" not in route.calls.last.request.headers
    assert index.api_version == "v1"
    assert index.stability.stable is True
    assert index.capabilities["embeddings"] == {"enabled": False, "model": None}


def test_a_keyed_call_without_a_key_fails_before_any_request(api, monkeypatch) -> None:
    monkeypatch.delenv("MATTERWAVE_API_KEY", raising=False)
    route = api.get("/v1/models").mock(
        return_value=httpx.Response(200, json={"object": "list", "data": []})
    )
    client = MatterWave(base_url="https://matterwave.test")
    with pytest.raises(MissingAPIKeyError, match="MATTERWAVE_API_KEY"):
        client.models.list()
    assert not route.called


def test_messages_must_not_be_empty(client: MatterWave) -> None:
    with pytest.raises(ValueError, match="at least one message"):
        client.chat.completions.create(messages=[])


def test_model_id_must_be_a_single_path_segment(client: MatterWave) -> None:
    with pytest.raises(ValueError):
        client.models.retrieve("a/b")
