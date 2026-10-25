"""The client and the exported OpenAPI document describe the same API.

docs/developers/openapi.json is generated from the app by
scripts/export_openapi.py. Every operation this client exposes must be in
it, with the same method and path, and every operation in it must be
exposed here, so neither side can grow without the other noticing. The
request parameters of `chat.completions.create` are checked against the
request schema the same way.
"""

from __future__ import annotations

import dataclasses
import inspect
import json
from pathlib import Path

import pytest

from matterwave_sdk import APP_OPERATIONS, OPERATIONS, MatterWave
from matterwave_sdk.frames import FRAMES_BY_EVENT, TERMINAL_FRAMES, UnknownFrame, frame_from_event

OPENAPI = Path(__file__).resolve().parents[3] / "docs" / "developers" / "openapi.json"

# How the client spells the request fields it renames. `metadata` is the
# OpenAI-reserved bag the conversation id rides in; `stream_options` is a
# nested object with one flag.
RENAMED = {"metadata": "conversation_id", "stream_options": "include_usage"}

CONVERSATION_STREAM = APP_OPERATIONS["conversations.send"][1]
# A `part_start`'s `id` is the tool call's, and the frame carries the SSE
# `id:` under that name, so the call's is spelled `call_id`.
RENAMED_FRAME_FIELDS = {("part_start", "id"): "call_id"}
MESSAGE_SCHEMA = "#/components/schemas/ConversationMessage"


@pytest.fixture(scope="module")
def document() -> dict:
    if not OPENAPI.exists():
        pytest.fail(f"{OPENAPI} is missing; run `make openapi` from the repo root")
    return json.loads(OPENAPI.read_text(encoding="utf-8"))


def _exported_operations(document: dict) -> dict[str, tuple[str, str]]:
    found: dict[str, tuple[str, str]] = {}
    for path, item in document["paths"].items():
        for method, op in item.items():
            found[op["operationId"]] = (method.upper(), path)
    return found


def test_every_client_operation_is_in_the_export_and_vice_versa(document: dict) -> None:
    exported = _exported_operations(document)
    exposed = {op["operation_id"]: (op["method"], op["path"]) for op in OPERATIONS.values()}
    assert exposed == exported


def test_the_app_routes_match_the_export_where_it_names_them(document: dict) -> None:
    """The conversation, `/api/me` and device-login routes are the app's,
    not the `/v1` contract, so the export does not carry them yet. Where it
    does name one of their paths, the method must agree; until it names any,
    there is nothing to compare and the check says so rather than passing."""
    paths = document["paths"]
    named = {key: (m, p) for key, (m, p) in APP_OPERATIONS.items() if p in paths}
    if not named:
        pytest.skip("openapi.json names none of the /api routes yet")
    for key, (method, path) in named.items():
        assert method.lower() in paths[path], f"{key}: {method} {path} is not in the export"


def test_the_client_methods_named_in_operations_exist() -> None:
    client = MatterWave(api_key="k", base_url="https://matterwave.test")
    for dotted in OPERATIONS:
        target = client
        for part in dotted.split("."):
            assert hasattr(target, part), f"client has no {dotted}"
            target = getattr(target, part)
        assert callable(target)


def test_create_accepts_every_request_field_the_schema_names(document: dict) -> None:
    schema = document["components"]["schemas"]["ChatCompletionRequest"]
    expected = {RENAMED.get(name, name) for name in schema["properties"]}
    create = inspect.signature(
        MatterWave(api_key="k", base_url="https://matterwave.test").chat.completions.create
    )
    accepted = set(create.parameters) - {"extra_body"}
    assert expected <= accepted, f"missing: {expected - accepted}"
    assert accepted - expected == set(), f"not in the schema: {accepted - expected}"


def test_embeddings_create_accepts_every_request_field_the_schema_names(document: dict) -> None:
    schema = document["components"]["schemas"]["EmbeddingsRequest"]
    expected = set(schema["properties"])
    create = inspect.signature(
        MatterWave(api_key="k", base_url="https://matterwave.test").embeddings.create
    )
    accepted = set(create.parameters) - {"extra_body"}
    assert accepted == expected, f"schema {expected} vs client {accepted}"


def _documented_events(document: dict) -> dict[str, dict]:
    return document["x-sse-events"][CONVERSATION_STREAM]["events"]


def test_the_stream_events_this_client_types_are_exactly_the_documented_ones(
    document: dict,
) -> None:
    events = _documented_events(document)
    assert set(FRAMES_BY_EVENT) == set(events)
    for name, cls in FRAMES_BY_EVENT.items():
        assert isinstance(frame_from_event(name, {}), cls), name
    assert isinstance(frame_from_event("not-an-event", {}), UnknownFrame)


def test_the_terminal_frames_are_the_documented_terminal_events(document: dict) -> None:
    terminal = {name for name, event in _documented_events(document).items() if event["terminal"]}
    assert {name for name, cls in FRAMES_BY_EVENT.items() if cls in TERMINAL_FRAMES} == terminal


def test_each_frame_carries_exactly_the_fields_its_event_schema_names(document: dict) -> None:
    schemas = document["components"]["schemas"]
    for name, event in _documented_events(document).items():
        ref = event["data"]["$ref"]
        if ref == MESSAGE_SCHEMA:
            # The data is the message itself; the frame wraps it as `message`.
            expected = {"message"}
        else:
            properties = schemas[ref.rsplit("/", 1)[1]]["properties"]
            expected = {RENAMED_FRAME_FIELDS.get((name, p), p) for p in properties}
        fields = {f.name for f in dataclasses.fields(FRAMES_BY_EVENT[name])} - {"id"}
        assert fields == expected, f"{name}: frame {fields} vs schema {expected}"


def test_the_v1_stream_is_documented_as_chunks_or_an_error(document: dict) -> None:
    v1 = document["x-sse-events"][OPERATIONS["chat.completions.create"]["path"]]
    assert list(v1["events"]) == ["message"]
    refs = {alt["$ref"].rsplit("/", 1)[1] for alt in v1["events"]["message"]["data"]["oneOf"]}
    assert refs == {"ChatCompletionChunk", "ErrorEnvelope"}


def test_the_security_scheme_is_a_bearer_and_only_the_index_is_open(document: dict) -> None:
    schemes = document["components"]["securitySchemes"]
    assert schemes["apiKey"]["scheme"] == "bearer"
    for operation_id, (method, path) in _exported_operations(document).items():
        op = document["paths"][path][method.lower()]
        if operation_id == "openai_index":
            assert op["security"] == []
        else:
            assert len(op["security"]) == 1
            assert list(op["security"][0]) == ["apiKey"]
            assert len(op["security"][0]["apiKey"]) == 1
