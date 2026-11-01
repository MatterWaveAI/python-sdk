"""The org's presets and connections against the fake API, and a
conversation on a preset. Each test answers with the body the route sends
(routes_presets.py, routes_connectors.py, routes_slack.py, routes_usage.py)
and checks the request the client made and what it parsed out of the
answer. Credentials go out on create and update and are never read back."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest
from conftest import BASE_URL, REQUEST_ID

from matterwave_sdk import (
    APP_OPERATIONS,
    AsyncMatterWave,
    ConflictError,
    Connection,
    MatterWave,
    PermissionDeniedError,
    ServerError,
)

H = {"X-Request-Id": REQUEST_ID}
PRESET_ID = "0193a8f2-0000-7000-8000-00000000p001"
CONNECTION_ID = "0193a8f2-0000-7000-8000-00000000c001"
ROUTE_ID = "0193a8f2-0000-7000-8000-00000000r001"
IDENTITY_ID = "0193a8f2-0000-7000-8000-00000000i001"
CONV = "c0ffee00-0000-4000-8000-000000000001"
WHEN = "2026-11-01T09:00:00+00:00"
SECRET = "xoxb-never-to-be-seen-again"
DOCUMENT = json.loads((Path(__file__).parent / "openapi-full.json").read_text(encoding="utf-8"))
NAMESPACES = ("presets", "connections")


def sent(route) -> httpx.Request:
    return route.calls.last.request


def body_of(route) -> dict:
    return json.loads(sent(route).content)


def person() -> MatterWave:
    return MatterWave(access_token="person-token", base_url=BASE_URL, max_retries=0)


def preset_body(**extra) -> dict:
    return {
        "id": PRESET_ID,
        "name": "Support",
        "description": "Answers in the support channel",
        "model": "grt-nano",
        "instructions": "Be brief.",
        "tools": None,
        "welcome_message": None,
        "created_by": "u-1",
        "created_at": WHEN,
        "updated_at": WHEN,
        "deleted_at": None,
        **extra,
    }


def listing_body() -> dict:
    return {
        "data": [preset_body()],
        "tools": [{"name": "web_search", "description": "Search the web."}],
        "limits": {
            "max_presets": 50,
            "name_max_len": 80,
            "description_max_len": 500,
            "instructions_max_len": 4000,
            "welcome_max_len": 2000,
        },
        "can_manage": True,
    }


def connection_body(**extra) -> dict:
    return {
        "id": CONNECTION_ID,
        "connector": "slack",
        "display_name": "Acme Slack",
        "status": "active",
        "ingress_url": f"https://app.example.test/api/hooks/slack/tok-{CONNECTION_ID[-4:]}",
        "config": {"notify_events": ["budget.exhausted"]},
        "external_workspace_id": "T0001",
        "rate_limit_per_minute": 60,
        "installed_by": "u-1",
        "last_event_at": None,
        "last_error": None,
        "created_at": WHEN,
        "updated_at": WHEN,
        **extra,
    }


def route_body(**extra) -> dict:
    return {
        "id": ROUTE_ID,
        "connection_id": CONNECTION_ID,
        "external_channel_id": "C0001",
        "channel_label": "#support",
        "preset_id": PRESET_ID,
        "preset_name": "Support",
        "paying_subject": "u-1",
        "enabled": True,
        "created_by": "u-1",
        "created_at": WHEN,
        "updated_at": WHEN,
        **extra,
    }


def identity_body(**extra) -> dict:
    return {
        "id": IDENTITY_ID,
        "connection_id": CONNECTION_ID,
        "external_user_id": "U0001",
        "display_name": "Ada",
        "email": "ada@example.test",
        "subject": None,
        "status": "unmapped",
        "mapped_by": "auto",
        "created_at": WHEN,
        "updated_at": WHEN,
        **extra,
    }


def event_body(event_id: str, outcome: str = "answered") -> dict:
    return {
        "id": event_id,
        "connection_id": CONNECTION_ID,
        "external_event_id": f"Ev{event_id}",
        "received_at": WHEN,
        "kind": "mention",
        "outcome": outcome,
        "external_user_id": "U0001",
        "external_channel_id": "C0001",
        "conversation_id": CONV,
        "paying_subject": "u-1",
        "detail": None,
        "updated_at": WHEN,
    }


# --- the route table -----------------------------------------------------------


def test_every_operation_names_a_method_on_both_clients() -> None:
    names = [name for name in APP_OPERATIONS if name.split(".")[0] in NAMESPACES]
    assert len(names) == 23
    for client in (
        MatterWave(api_key="k", base_url=BASE_URL),
        AsyncMatterWave(api_key="k", base_url=BASE_URL),
    ):
        for dotted in names:
            target: object = client
            for part in dotted.split("."):
                assert hasattr(target, part), f"{type(client).__name__} has no {dotted}"
                target = getattr(target, part)
            assert callable(target)


# --- presets -------------------------------------------------------------------


def test_presets_list_is_the_listing_with_tools_limits_and_can_manage(api, client) -> None:
    route = api.get("/api/org/presets").respond(200, json=listing_body(), headers=H)
    listing = client.presets.list()
    assert sent(route).headers["authorization"].startswith("Bearer ")
    assert [p.name for p in listing] == ["Support"]
    assert len(listing) == 1
    assert listing.data[0].tools is None
    assert listing.tools[0].name == "web_search"
    assert listing.limits.max_presets == 50
    assert listing.limits.welcome_max_len == 2000
    assert listing.can_manage is True
    assert listing.request_id == REQUEST_ID


def test_presets_get_answers_for_a_deleted_one(api, client) -> None:
    api.get(f"/api/org/presets/{PRESET_ID}").respond(
        200, json=preset_body(deleted_at=WHEN, tools=[]), headers=H
    )
    preset = client.presets.get(PRESET_ID)
    assert preset.deleted_at == WHEN
    assert preset.tools == []


def test_presets_create_sends_only_what_was_passed(api, client) -> None:
    route = api.post("/api/org/presets").respond(201, json=preset_body(tools=[]), headers=H)
    preset = client.presets.create("Support", "grt-nano", instructions="Be brief.", tools=[])
    assert body_of(route) == {
        "name": "Support",
        "model": "grt-nano",
        "instructions": "Be brief.",
        "tools": [],
    }
    assert preset.id == PRESET_ID


def test_presets_update_sends_null_for_tools_and_welcome_only_when_passed(api, client) -> None:
    route = api.patch(f"/api/org/presets/{PRESET_ID}").respond(200, json=preset_body(), headers=H)
    client.presets.update(PRESET_ID, tools=None, welcome_message=None)
    assert body_of(route) == {"tools": None, "welcome_message": None}
    client.presets.update(PRESET_ID, name="Helpdesk")
    assert body_of(route) == {"name": "Helpdesk"}


def test_presets_update_with_nothing_is_refused_before_the_request(api, client) -> None:
    with pytest.raises(ValueError, match="update needs one of name"):
        client.presets.update(PRESET_ID)
    with pytest.raises(ValueError, match="name must not be blank"):
        client.presets.create(" ", "grt-nano")


def test_presets_a_taken_name_is_a_conflict(api, client) -> None:
    api.post("/api/org/presets").respond(
        409, json={"detail": "This organisation already has a preset named 'Support'."}, headers=H
    )
    with pytest.raises(ConflictError, match="already has a preset"):
        client.presets.create("support", "grt-nano")


def test_presets_delete_is_a_204(api, client) -> None:
    route = api.delete(f"/api/org/presets/{PRESET_ID}").respond(204)
    assert client.presets.delete(PRESET_ID) is None
    assert route.called


# --- a conversation on a preset ------------------------------------------------


def conversation_body(**extra) -> dict:
    return {
        "id": CONV,
        "title": "New chat",
        "created_at": WHEN,
        "updated_at": WHEN,
        "preset_id": PRESET_ID,
        "preset": {"id": PRESET_ID, "name": "Support"},
        "source": None,
        **extra,
    }


def test_a_conversation_is_created_on_a_preset(api, client) -> None:
    route = api.post("/api/conversations").respond(201, json=conversation_body(), headers=H)
    conversation = client.conversations.create(preset_id=PRESET_ID)
    assert body_of(route) == {"preset_id": PRESET_ID}
    assert conversation.preset is not None
    assert (conversation.preset_id, conversation.preset.name) == (PRESET_ID, "Support")
    client.conversations.create(title="Plain")
    assert body_of(route) == {"title": "Plain"}


def test_a_conversation_is_put_on_and_taken_off_a_preset(api, client) -> None:
    route = api.patch(f"/api/conversations/{CONV}").respond(
        200, json=conversation_body(preset_id=None, preset=None), headers=H
    )
    off = client.conversations.update(CONV, preset_id=None)
    assert body_of(route) == {"preset_id": None}
    assert off.preset is None
    client.conversations.update(CONV, preset_id=PRESET_ID)
    assert body_of(route) == {"preset_id": PRESET_ID}
    client.conversations.update(CONV, title="Renamed")
    assert body_of(route) == {"title": "Renamed"}
    with pytest.raises(ValueError, match="or a preset_id"):
        client.conversations.update(CONV)


@pytest.mark.asyncio
async def test_the_async_client_lists_presets_and_creates_on_one(api, async_client) -> None:
    api.get("/api/org/presets").respond(200, json=listing_body(), headers=H)
    route = api.post("/api/conversations").respond(201, json=conversation_body(), headers=H)
    listing = await async_client.presets.list()
    conversation = await async_client.conversations.create(preset_id=listing.data[0].id)
    assert body_of(route) == {"preset_id": PRESET_ID}
    assert conversation.preset_id == PRESET_ID


# --- connections ---------------------------------------------------------------


def test_connections_list_carries_the_vocabularies(api) -> None:
    route = api.get("/api/org/connections").respond(
        200,
        json={
            "data": [connection_body()],
            "connectors": ["slack"],
            "notifiable_events": ["budget.exhausted"],
            "outcomes": ["accepted", "answered", "duplicate"],
            "identity_statuses": ["mapped", "unmapped", "guest", "blocked"],
        },
        headers=H,
    )
    listed = person().connections.list()
    assert sent(route).headers["authorization"] == "Bearer person-token"
    assert [c.display_name for c in listed] == ["Acme Slack"]
    assert listed.connectors == ["slack"]
    assert listed.outcomes[1] == "answered"
    assert listed.identity_statuses[-1] == "blocked"
    assert listed.data[0].config == {"notify_events": ["budget.exhausted"]}


def test_connections_create_pending_sends_no_credentials(api) -> None:
    route = api.post("/api/org/connections").respond(
        201, json=connection_body(status="pending", external_workspace_id=None), headers=H
    )
    connection = person().connections.create("slack", "Acme Slack")
    assert body_of(route) == {"connector": "slack", "display_name": "Acme Slack"}
    assert connection.status == "pending"


def test_credentials_go_in_and_never_come_out(api) -> None:
    # The answers carry them here, as a server that leaked them would: they
    # still do not reach the model.
    created = api.post("/api/org/connections").respond(
        201, json=connection_body(credentials={"bot_token": SECRET}), headers=H
    )
    updated = api.patch(f"/api/org/connections/{CONNECTION_ID}").respond(
        200, json=connection_body(credentials={"bot_token": SECRET}), headers=H
    )
    client = person()
    credentials = {"bot_token": SECRET, "signing_secret": "s3cr3t"}
    connection = client.connections.create(
        "slack", "Acme Slack", credentials=credentials, rate_limit_per_minute=60
    )
    assert body_of(created)["credentials"] == credentials
    again = client.connections.update(CONNECTION_ID, credentials=credentials)
    assert body_of(updated) == {"credentials": credentials}
    for answer in (connection, again):
        assert not hasattr(answer, "credentials")
        assert SECRET not in repr(answer)
    assert "credentials" not in {f for f in Connection.__dataclass_fields__}


def test_connections_update_status_rate_limit_and_config(api) -> None:
    route = api.patch(f"/api/org/connections/{CONNECTION_ID}").respond(
        200, json=connection_body(status="disabled", rate_limit_per_minute=10), headers=H
    )
    connection = person().connections.update(
        CONNECTION_ID, status="disabled", rate_limit_per_minute=10, config={"notify_events": []}
    )
    assert body_of(route) == {
        "status": "disabled",
        "rate_limit_per_minute": 10,
        "config": {"notify_events": []},
    }
    assert connection.status == "disabled"


def test_connections_refuse_what_the_route_would_before_the_request() -> None:
    client = person()
    with pytest.raises(ValueError, match="status must be one of active, disabled"):
        client.connections.update(CONNECTION_ID, status="revoked")
    with pytest.raises(ValueError, match="rate_limit_per_minute must be 1 to 10000"):
        client.connections.create("slack", "x", rate_limit_per_minute=0)
    with pytest.raises(ValueError, match="credentials must name at least one field"):
        client.connections.create("slack", "x", credentials={})
    with pytest.raises(ValueError, match="update needs one of display_name"):
        client.connections.update(CONNECTION_ID)


def test_connections_get_delete_and_rotate_url(api) -> None:
    api.get(f"/api/org/connections/{CONNECTION_ID}").respond(200, json=connection_body(), headers=H)
    delete = api.delete(f"/api/org/connections/{CONNECTION_ID}").respond(204)
    rotate = api.post(f"/api/org/connections/{CONNECTION_ID}/rotate-url").respond(
        200,
        json=connection_body(ingress_url="https://app.example.test/api/hooks/slack/new"),
        headers=H,
    )
    client = person()
    assert client.connections.get(CONNECTION_ID).rate_limit_per_minute == 60
    assert client.connections.rotate_url(CONNECTION_ID).ingress_url.endswith("/slack/new")
    assert sent(rotate).content == b""
    client.connections.delete(CONNECTION_ID)
    assert delete.called


def test_connections_slack_manifest_names_the_connection_in_the_query(api) -> None:
    route = api.get("/api/org/connections/slack/manifest").respond(
        200,
        json={
            "connection_id": CONNECTION_ID,
            "status": "pending",
            "request_url": "https://app.example.test/api/hooks/slack/tok",
            "publicly_reachable": True,
            "manifest": {"display_information": {"name": "MatterWave"}},
            "create_url": "https://api.slack.com/apps?new_app=1&manifest_json=...",
        },
        headers=H,
    )
    manifest = person().connections.slack_manifest(CONNECTION_ID)
    assert sent(route).url.params["connection_id"] == CONNECTION_ID
    assert manifest.publicly_reachable is True
    assert manifest.manifest["display_information"]["name"] == "MatterWave"


def test_a_key_is_refused_the_connection_routes(api, client) -> None:
    api.get("/api/org/connections").respond(
        403, json={"detail": "Membership is managed by a person, not an API key."}, headers=H
    )
    with pytest.raises(PermissionDeniedError, match="managed by a person"):
        client.connections.list()


def test_a_deployment_without_connectors_is_a_server_error_naming_the_setting(api) -> None:
    detail = "Connectors are not enabled on this deployment; set MW_CONNECTOR_SECRET_KEY."
    api.get("/api/org/connections").respond(503, json={"detail": detail}, headers=H)
    with pytest.raises(ServerError) as caught:
        person().connections.list()
    assert caught.value.status_code == 503
    assert "MW_CONNECTOR_SECRET_KEY" in str(caught.value)
    assert caught.value.request_id == REQUEST_ID


# --- routes --------------------------------------------------------------------


def test_routes_crud(api) -> None:
    base = f"/api/org/connections/{CONNECTION_ID}/routes"
    listed = api.get(base).respond(200, json={"data": [route_body()]}, headers=H)
    created = api.post(base).respond(201, json=route_body(), headers=H)
    updated = api.patch(f"{base}/{ROUTE_ID}").respond(
        200, json=route_body(enabled=False, preset_name=None), headers=H
    )
    deleted = api.delete(f"{base}/{ROUTE_ID}").respond(204)
    client = person()
    assert [r.channel_label for r in client.connections.routes.list(CONNECTION_ID)] == ["#support"]
    assert listed.called
    client.connections.routes.create(
        CONNECTION_ID, "C0001", PRESET_ID, "u-1", channel_label="#support"
    )
    assert body_of(created) == {
        "external_channel_id": "C0001",
        "preset_id": PRESET_ID,
        "paying_subject": "u-1",
        "channel_label": "#support",
    }
    route = client.connections.routes.update(CONNECTION_ID, ROUTE_ID, enabled=False)
    assert body_of(updated) == {"enabled": False}
    assert (route.enabled, route.preset_name) == (False, None)
    client.connections.routes.delete(CONNECTION_ID, ROUTE_ID)
    assert deleted.called
    with pytest.raises(ValueError, match="update needs one of channel_label"):
        client.connections.routes.update(CONNECTION_ID, ROUTE_ID)


# --- identities ----------------------------------------------------------------


def test_identities_list_filters_and_each_action_sends_its_body(api) -> None:
    listed = api.get(f"/api/org/connections/{CONNECTION_ID}/identities").respond(
        200, json={"data": [identity_body()]}, headers=H
    )
    patch = api.patch(f"/api/org/connections/{CONNECTION_ID}/identities/{IDENTITY_ID}").respond(
        200, json=identity_body(subject="u-2", status="mapped", mapped_by="manual"), headers=H
    )
    identities = person().connections.identities
    assert identities.list(CONNECTION_ID, status="unmapped").data[0].email == "ada@example.test"
    assert sent(listed).url.params["status"] == "unmapped"
    mapped = identities.map(CONNECTION_ID, IDENTITY_ID, "u-2")
    assert body_of(patch) == {"action": "map", "subject": "u-2"}
    assert (mapped.subject, mapped.mapped_by) == ("u-2", "manual")
    for action in ("unmap", "block", "unblock"):
        getattr(identities, action)(CONNECTION_ID, IDENTITY_ID)
        assert body_of(patch) == {"action": action}


def test_identities_for_member_span_connections(api) -> None:
    route = api.get("/api/org/connector-identities").respond(
        200,
        json={"data": [identity_body(connector="slack", connection_name="Acme Slack")]},
        headers=H,
    )
    rows = person().connections.identities.for_member("u-2")
    assert sent(route).url.params["subject"] == "u-2"
    assert (rows.data[0].connector, rows.data[0].connection_name) == ("slack", "Acme Slack")


# --- the event log -------------------------------------------------------------


def test_events_filter_by_outcome_and_since(api) -> None:
    route = api.get(f"/api/org/connections/{CONNECTION_ID}/events").respond(
        200, json={"data": [event_body("e-1", "rate_limited")], "next_cursor": None}, headers=H
    )
    page = person().connections.events.list(
        CONNECTION_ID,
        outcome="rate_limited",
        since=datetime(2026, 10, 1, tzinfo=timezone.utc),
        limit=10,
    )
    params = sent(route).url.params
    assert (params["outcome"], params["since"], params["limit"]) == (
        "rate_limited",
        "2026-10-01T00:00:00+00:00",
        "10",
    )
    assert page.data[0].outcome == "rate_limited"
    assert page.next_cursor is None


def test_events_items_walk_the_cursor(api) -> None:
    route = api.get(f"/api/org/connections/{CONNECTION_ID}/events")
    route.side_effect = [
        httpx.Response(200, json={"data": [event_body("e-2")], "next_cursor": "cur-1"}),
        httpx.Response(200, json={"data": [event_body("e-1")], "next_cursor": None}),
    ]
    ids = [e.id for e in person().connections.events.items(CONNECTION_ID, outcome="answered")]
    assert ids == ["e-2", "e-1"]
    assert route.calls[1].request.url.params["cursor"] == "cur-1"
    assert route.calls[1].request.url.params["outcome"] == "answered"


def test_events_refuse_a_naive_since_and_a_limit_out_of_range() -> None:
    events = person().connections.events
    with pytest.raises(ValueError, match="since must carry a timezone"):
        events.list(CONNECTION_ID, since=datetime(2026, 10, 1))
    with pytest.raises(ValueError, match="limit must be 1 to 200"):
        events.list(CONNECTION_ID, limit=201)


@pytest.mark.asyncio
async def test_the_async_client_walks_the_event_log(api) -> None:
    route = api.get(f"/api/org/connections/{CONNECTION_ID}/events")
    route.side_effect = [
        httpx.Response(200, json={"data": [event_body("e-2")], "next_cursor": "cur-1"}),
        httpx.Response(200, json={"data": [event_body("e-1")], "next_cursor": None}),
    ]
    client = AsyncMatterWave(access_token="person-token", base_url=BASE_URL)
    ids = [e.id async for e in client.connections.events.items(CONNECTION_ID)]
    assert ids == ["e-2", "e-1"]


# --- usage ---------------------------------------------------------------------


def test_org_usage_carries_by_connection(api, client) -> None:
    api.get("/api/org/usage").respond(
        200,
        json={
            "period": "2026-11",
            "period_start": "2026-11-01",
            "period_end": "2026-12-01",
            "unit": "weighted_tokens",
            "scope": "org",
            "data": [],
            "by_connection": [
                {
                    "connection_id": CONNECTION_ID,
                    "connector": "slack",
                    "display_name": "Acme Slack",
                    "weighted_tokens": 1200,
                    "cost_micros": 3400,
                    "turns": 3,
                    "tokens": 900,
                    "last_active": WHEN,
                },
                {
                    "connection_id": "gone",
                    "connector": None,
                    "display_name": None,
                    "weighted_tokens": 10,
                    "cost_micros": 1,
                    "turns": 1,
                    "tokens": 8,
                    "last_active": None,
                },
            ],
            "totals": {"weighted_tokens": 0, "cost_micros": 0, "turns": 0, "tokens": 0},
        },
        headers=H,
    )
    usage = client.usage.org(period="2026-11")
    assert [row.connector for row in usage.by_connection] == ["slack", None]
    assert usage.by_connection[0].cost_micros == 3400


# --- the contract --------------------------------------------------------------


def _schema(name: str) -> set[str]:
    return set(DOCUMENT["components"]["schemas"][name]["properties"])


def _query(path: str, method: str = "get") -> set[str]:
    parameters = DOCUMENT["paths"][path][method].get("parameters", [])
    return {p["name"] for p in parameters if p["in"] == "query"}


def _keywords(method) -> set[str]:
    import inspect

    return {
        name
        for name, p in inspect.signature(method).parameters.items()
        if p.kind is not inspect.Parameter.VAR_KEYWORD
    }


def test_a_conversation_names_its_preset_in_the_documents_bodies() -> None:
    client = MatterWave(api_key="k", base_url=BASE_URL)
    assert "preset_id" in _schema("NewConversation")
    assert "preset_id" in _schema("UpdateConversation")
    assert "preset_id" in _keywords(client.conversations.create)
    assert "preset_id" in _keywords(client.conversations.update)


@pytest.mark.parametrize(
    ("dotted", "schema", "ids"),
    [
        ("presets.create", "CreatePreset", set()),
        ("presets.update", "UpdatePreset", {"preset_id"}),
        ("connections.create", "CreateConnection", set()),
        ("connections.update", "UpdateConnection", {"connection_id"}),
        ("connections.routes.create", "CreateRoute", {"connection_id"}),
        ("connections.routes.update", "UpdateRoute", {"connection_id", "route_id"}),
    ],
)
def test_each_write_takes_exactly_the_fields_its_schema_names(dotted, schema, ids) -> None:
    for client in (
        MatterWave(api_key="k", base_url=BASE_URL),
        AsyncMatterWave(api_key="k", base_url=BASE_URL),
    ):
        target: object = client
        for part in dotted.split("."):
            target = getattr(target, part)
        assert _keywords(target) - ids == _schema(schema), dotted


def test_the_identity_actions_are_the_ones_the_schema_names() -> None:
    actions = set(
        DOCUMENT["components"]["schemas"]["UpdateIdentity"]["properties"]["action"]["enum"]
    )
    identities = MatterWave(api_key="k", base_url=BASE_URL).connections.identities
    assert actions == {"map", "unmap", "block", "unblock"}
    assert all(callable(getattr(identities, action)) for action in actions)


def test_the_reads_take_the_query_parameters_the_document_names() -> None:
    client = MatterWave(api_key="k", base_url=BASE_URL)
    events = "/api/org/connections/{connection_id}/events"
    identities = "/api/org/connections/{connection_id}/identities"
    assert _query(events) == _keywords(client.connections.events.list) - {"connection_id"}
    assert _query(identities) == _keywords(client.connections.identities.list) - {"connection_id"}
    assert _query("/api/org/connections/slack/manifest") == {"connection_id"}
    assert _query("/api/org/connector-identities") == {"subject"}
