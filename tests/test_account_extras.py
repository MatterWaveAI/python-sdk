"""The routes 0.2 added that are neither the conversation tree nor the
settings panes' native clients: the model picker (`app_models`), custom
instructions (user and org level), and reply feedback (`messages`). Each
test answers with the body the route in api/matterwave sends and checks
the request the client made and what it parsed.
"""

from __future__ import annotations

import json

import httpx
import pytest
from conftest import REQUEST_ID

from matterwave_sdk import MatterWave

H = {"X-Request-Id": REQUEST_ID}


def sent(route) -> httpx.Request:
    return route.calls.last.request


def body_of(route) -> dict:
    return json.loads(sent(route).content)


# --- the model picker ------------------------------------------------------------


def test_app_models_reads_the_picker_rows_and_the_default(api, client: MatterWave) -> None:
    route = api.get("/api/models").mock(
        return_value=httpx.Response(
            200,
            json={
                "object": "list",
                "default_model": "grt-nano",
                "data": [
                    {
                        "id": "grt-nano",
                        "label": "GRT Nano",
                        "multiplier": 1,
                        "input_usd_per_m": 0.15,
                        "output_usd_per_m": 0.6,
                        "partner": False,
                        "research_preview": False,
                        "context_window": 128000,
                        "included": True,
                        "upgrade_to": None,
                    },
                    {
                        "id": "grt-max",
                        "label": "GRT Max",
                        "multiplier": 12,
                        "input_usd_per_m": 2.5,
                        "output_usd_per_m": 10.0,
                        "partner": False,
                        "research_preview": False,
                        "context_window": 200000,
                        "included": False,
                        "upgrade_to": "scale",
                    },
                ],
            },
            headers=H,
        )
    )
    listed = client.app_models()
    assert listed.request_id == REQUEST_ID
    assert listed.default_model == "grt-nano"
    assert [m.id for m in listed] == ["grt-nano", "grt-max"]
    nano, paid = listed.data
    assert nano.included and nano.upgrade_to is None and nano.context_window == 128000
    assert not paid.included and paid.upgrade_to == "scale"
    assert paid.input_usd_per_m == 2.5 and paid.multiplier == 12
    assert route.calls.last.request.url.path == "/api/models"


def test_app_models_keeps_unknown_flags_optional(api, client: MatterWave) -> None:
    # A row the gateway annotated with nothing: every optional field reads
    # as None/False rather than crashing on its absence.
    api.get("/api/models").mock(
        return_value=httpx.Response(
            200,
            json={"object": "list", "default_model": None, "data": [{"id": "m", "label": "M"}]},
        )
    )
    model = client.app_models().data[0]
    assert not model.included
    assert model.upgrade_to is None and model.context_window is None
    assert model.supports_vision is None and not model.partner


@pytest.mark.asyncio
async def test_the_async_app_models_sends_the_same_request(
    api, async_client  # noqa: ANN001
) -> None:
    route = api.get("/api/models").mock(
        return_value=httpx.Response(
            200, json={"object": "list", "default_model": "grt-nano", "data": []}, headers=H
        )
    )
    listed = await async_client.app_models()
    assert listed.default_model == "grt-nano" and len(listed) == 0
    assert sent(route).method == "GET"


# --- the user's and the org's instructions ---------------------------------------


def test_user_instructions_read_and_update_whole(api, client: MatterWave) -> None:
    got = api.get("/api/me/instructions").mock(
        return_value=httpx.Response(
            200, json={"about": "I am a Python developer.", "style": "Be concise."}, headers=H
        )
    )
    put = api.put("/api/me/instructions").mock(
        return_value=httpx.Response(
            200, json={"about": "I am a Python developer.", "style": ""}, headers=H
        )
    )
    mine = client.instructions.user()
    assert mine.about.startswith("I am") and mine.style == "Be concise."
    assert mine.request_id == REQUEST_ID
    saved = client.instructions.update_user(about="I am a Python developer.")
    assert saved.style == ""
    # Both fields always go: the route replaces the pair.
    assert body_of(put) == {"about": "I am a Python developer.", "style": ""}
    assert got.calls.last.request.method == "GET" and put.calls.last.request.method == "PUT"


def test_org_instructions_read_and_update(api, client: MatterWave) -> None:
    put = api.put("/api/org/instructions").mock(
        return_value=httpx.Response(
            200, json={"text": "Never quote customer names."}, headers=H
        )
    )
    api.get("/api/org/instructions").mock(
        return_value=httpx.Response(200, json={"text": ""}, headers=H)
    )
    assert client.instructions.org().text == ""
    saved = client.instructions.update_org("Never quote customer names.")
    assert saved.text == "Never quote customer names." and saved.request_id == REQUEST_ID
    assert body_of(put) == {"text": "Never quote customer names."}
    # An empty text clears the org level rather than being refused.
    client.instructions.update_org("")
    assert body_of(put) == {"text": ""}


@pytest.mark.asyncio
async def test_the_async_instructions_match_the_sync_bodies(
    api, async_client  # noqa: ANN001
) -> None:
    api.get("/api/me/instructions").mock(
        return_value=httpx.Response(200, json={"about": "", "style": ""}, headers=H)
    )
    put_user = api.put("/api/me/instructions").mock(
        return_value=httpx.Response(200, json={"about": "a", "style": "s"}, headers=H)
    )
    put_org = api.put("/api/org/instructions").mock(
        return_value=httpx.Response(200, json={"text": "t"}, headers=H)
    )
    api.get("/api/org/instructions").mock(
        return_value=httpx.Response(200, json={"text": "t"}, headers=H)
    )
    assert (await async_client.instructions.update_user(about="a", style="s")).style == "s"
    assert body_of(put_user) == {"about": "a", "style": "s"}
    assert (await async_client.instructions.update_org("t")).text == "t"
    assert body_of(put_org) == {"text": "t"}
    assert (await async_client.instructions.user()).about == ""
    assert (await async_client.instructions.org()).text == "t"


# --- reply feedback ----------------------------------------------------------------


def test_set_feedback_sends_the_rating_and_parses_the_row(api, client: MatterWave) -> None:
    route = api.put("/api/messages/m-asst/feedback").mock(
        return_value=httpx.Response(
            200,
            json={
                "message_id": "m-asst",
                "rating": -1,
                "reasons": ["wrong", "unhelpful"],
                "comment": "Missed the point.",
                "created_at": "2026-10-18T09:00:00+00:00",
                "updated_at": "2026-10-18T09:00:00+00:00",
            },
            headers=H,
        )
    )
    feedback = client.messages.set_feedback(
        "m-asst", -1, reasons=["wrong", "unhelpful"], comment="Missed the point."
    )
    assert feedback.rating == -1 and feedback.reasons == ["wrong", "unhelpful"]
    assert feedback.comment == "Missed the point." and feedback.request_id == REQUEST_ID
    assert body_of(route) == {
        "rating": -1,
        "reasons": ["wrong", "unhelpful"],
        "comment": "Missed the point.",
    }
    assert route.calls.last.request.url.path == "/api/messages/m-asst/feedback"


def test_set_feedback_validates_before_sending(api, client: MatterWave) -> None:
    route = api.put("/api/messages/m-asst/feedback").mock(return_value=httpx.Response(200))
    with pytest.raises(ValueError, match="1 or -1"):
        client.messages.set_feedback("m-asst", 0)
    with pytest.raises(ValueError, match="reasons must be from"):
        client.messages.set_feedback("m-asst", 1, reasons=["vibes"])
    assert not route.called


def test_delete_feedback_reads_removed_or_none(api, client: MatterWave) -> None:
    route = api.delete("/api/messages/m-asst/feedback").mock(
        return_value=httpx.Response(200, json={"status": "removed"}, headers=H)
    )
    assert client.messages.delete_feedback("m-asst").status == "removed"
    api.delete("/api/messages/m-asst/feedback").mock(
        return_value=httpx.Response(200, json={"status": "none"})
    )
    assert client.messages.delete_feedback("m-asst").status == "none"
    assert route.calls.last.request.method == "DELETE"


def test_feedback_refuses_an_id_that_would_change_the_path(client: MatterWave) -> None:
    with pytest.raises(ValueError):
        client.messages.set_feedback("../keys", 1)


CONV = "c0ffee00-0000-4000-8000-000000000001"


def feedback_row(mid: str, rating: int) -> dict:
    return {
        "message_id": mid,
        "rating": rating,
        "reasons": ["wrong"] if rating < 0 else [],
        "comment": None,
        "created_at": "2026-10-18T09:00:00+00:00",
        "updated_at": "2026-10-18T09:00:00+00:00",
    }


def test_list_feedback_reads_the_caller_s_own_rows_in_order(
    api, client: MatterWave
) -> None:
    route = api.get(f"/api/conversations/{CONV}/feedback").mock(
        return_value=httpx.Response(
            200,
            json={"data": [feedback_row("m-1", 1), feedback_row("m-2", -1)]},
            headers=H,
        )
    )
    rows = client.messages.list_feedback(CONV)
    assert rows.request_id == REQUEST_ID
    assert [r.message_id for r in rows] == ["m-1", "m-2"]
    assert rows.data[0].rating == 1 and rows.data[1].rating == -1
    assert len(rows) == 2
    assert route.calls.last.request.method == "GET"
    assert route.calls.last.request.url.path == f"/api/conversations/{CONV}/feedback"


@pytest.mark.asyncio
async def test_the_async_list_feedback_reads_the_same_body(
    api, async_client  # noqa: ANN001
) -> None:
    route = api.get(f"/api/conversations/{CONV}/feedback").mock(
        return_value=httpx.Response(200, json={"data": [feedback_row("m-1", 1)]}, headers=H)
    )
    rows = await async_client.messages.list_feedback(CONV)
    assert [r.message_id for r in rows] == ["m-1"]
    assert route.calls.last.request.method == "GET"


@pytest.mark.asyncio
async def test_the_async_feedback_sends_the_same_body(
    api, async_client  # noqa: ANN001
) -> None:
    route = api.put("/api/messages/m-asst/feedback").mock(
        return_value=httpx.Response(
            200,
            json={
                "message_id": "m-asst",
                "rating": 1,
                "reasons": [],
                "comment": None,
                "created_at": "2026-10-18T09:00:00+00:00",
                "updated_at": "2026-10-18T09:00:00+00:00",
            },
            headers=H,
        )
    )
    feedback = await async_client.messages.set_feedback("m-asst", 1)
    assert feedback.rating == 1 and feedback.comment is None
    assert body_of(route) == {"rating": 1, "reasons": [], "comment": None}
