"""The conversation API: the JSON routes, the named-event stream frame by
frame, the resume after a dropped body, and the 409 a compaction with
nothing to fold answers."""

from __future__ import annotations

import json
import zlib
from collections.abc import AsyncIterator, Iterator

import httpx
import pytest
from conftest import REQUEST_ID

from matterwave_sdk import (
    APIConnectionError,
    AsyncMatterWave,
    ConflictError,
    MatterWave,
    NotFoundError,
)
from matterwave_sdk import frames as f
from matterwave_sdk._base import APP_OPERATIONS, RESUME_PLACEHOLDER, sse_events

CONV = "c0ffee00-0000-4000-8000-000000000001"
GEN = "gen-7f3a"


def sse_frame(event: str, data: dict, event_id: str | None = None) -> str:
    """One frame the way generation.py's `sse_frame` writes it."""
    lines = [f"id: {event_id}"] if event_id is not None else []
    lines += [f"event: {event}", f"data: {json.dumps(data)}"]
    return "\n".join(lines) + "\n\n"


def message(role: str, text: str, mid: str, parent: str | None = None, **extra) -> dict:
    return {
        "id": mid,
        "conversation_id": CONV,
        "role": role,
        "parts": extra.pop("parts", [{"type": "text", "text": text}]),
        "content": text,
        "content_version": 2,
        "created_at": "2026-10-18T09:00:00+00:00",
        "parent_id": parent,
        "model": extra.pop("model", None),
        "truncated": extra.pop("truncated", False),
        "finish_reason": extra.pop("finish_reason", None),
    }


def conversation(**extra) -> dict:
    return {
        "id": CONV,
        "title": "New chat",
        "created_at": "2026-10-18T09:00:00+00:00",
        "updated_at": "2026-10-18T09:00:00+00:00",
        "org_id": "org-1",
        "last_message_at": None,
        "preview": None,
        "message_count": 0,
        "default_model": None,
        "current_leaf_message_id": None,
        "pinned_at": None,
        "archived_at": None,
        "system_prompt": None,
        "folder_id": None,
        **extra,
    }


USER = message("user", "Hi there", "m-user")
ASSISTANT = message("assistant", "Hello, world", "m-asst", "m-user", model="grt-nano")
SUMMARY = message(
    "system",
    "They said hello.",
    "m-sum",
    parts=[
        {
            "type": "summary",
            "text": "They said hello.",
            "covers_from": "m-0",
            "covers_to": "m-9",
            "tokens_before": 9000,
            "tokens_after": 300,
        }
    ],
)

# A whole turn as the API sends one: compaction and context ahead of the user
# message, the title, a reasoning part and a text part, heartbeats between
# frames, the closing context, then `done`.
HEAD = (
    sse_frame(
        "compaction",
        {
            "message": SUMMARY,
            "covers_from": "m-0",
            "covers_to": "m-9",
            "tokens_before": 9000,
            "tokens_after": 300,
        },
    )
    + sse_frame("message", USER)
    + sse_frame("title", {"title": "Hi there"})
    + ": mw-heartbeat\n\n"
    + sse_frame("part_start", {"index": 0, "type": "reasoning"}, f"{GEN}:0")
    + sse_frame("delta", {"reasoning": "greet", "index": 0}, f"{GEN}:1")
    + sse_frame("part_end", {"index": 0}, f"{GEN}:2")
    + sse_frame("part_start", {"index": 1, "type": "text"}, f"{GEN}:3")
    + sse_frame("delta", {"content": "Hello", "index": 1}, f"{GEN}:4")
)
TAIL = (
    sse_frame("delta", {"content": ", world", "index": 1}, f"{GEN}:5")
    + ": mw-heartbeat\n\n"
    + sse_frame("part_end", {"index": 1}, f"{GEN}:6")
    + sse_frame("context", {"prompt_tokens": 320, "completion_tokens": 4, "context_window": 8192})
    + sse_frame("done", ASSISTANT)
)
TURN = HEAD + TAIL


def _sse(body: str | Iterator[bytes] | AsyncIterator[bytes]) -> httpx.Response:
    headers = {"Content-Type": "text/event-stream", "X-Request-Id": REQUEST_ID}
    return httpx.Response(200, headers=headers, content=body)


def _dropping(body: str) -> Iterator[bytes]:
    yield body.encode()
    raise httpx.RemoteProtocolError("peer closed connection without sending complete message body")


async def _adropping(body: str) -> AsyncIterator[bytes]:
    yield body.encode()
    raise httpx.RemoteProtocolError("peer closed connection without sending complete message body")


# --- the parser ---------------------------------------------------------------


def test_the_event_parser_reads_names_ids_comments_and_crlf() -> None:
    raw = ": mw-heartbeat\r\n\r\nid: g:0\r\nevent: delta\r\ndata: {\"a\":\r\ndata: 1}\r\n\r\ndata: x"
    events = list(sse_events(raw.splitlines(keepends=True)))
    assert [(e.event, e.data, e.id) for e in events] == [
        ("delta", '{"a":\n1}', "g:0"),
        ("message", "x", None),
    ]


def test_every_frame_of_a_turn_arrives_typed_and_in_order(api, client: MatterWave) -> None:
    api.post(f"/api/conversations/{CONV}/messages").mock(return_value=_sse(TURN))
    stream = client.conversations.send(CONV, "Hi there", model="grt-nano")
    got = list(stream)
    assert [type(x) for x in got] == [
        f.CompactionFrame,
        f.MessageFrame,
        f.TitleFrame,
        f.PartStartFrame,
        f.DeltaFrame,
        f.PartEndFrame,
        f.PartStartFrame,
        f.DeltaFrame,
        f.DeltaFrame,
        f.PartEndFrame,
        f.ContextFrame,
        f.DoneFrame,
    ]
    compaction = got[0]
    assert isinstance(compaction, f.CompactionFrame)
    assert compaction.tokens_before == 9000 and compaction.tokens_after == 300
    assert compaction.message.role == "system"
    assert compaction.message.summary is not None
    assert compaction.message.summary["covers_to"] == "m-9"
    assert isinstance(got[1], f.MessageFrame) and got[1].message.id == "m-user"
    assert isinstance(got[2], f.TitleFrame) and got[2].title == "Hi there"
    assert isinstance(got[3], f.PartStartFrame) and got[3].type == "reasoning"
    assert isinstance(got[4], f.DeltaFrame) and got[4].reasoning == "greet"
    assert got[4].content is None and got[4].id == f"{GEN}:1"
    context = got[10]
    assert isinstance(context, f.ContextFrame) and context.context_window == 8192
    assert stream.result is not None and stream.result.content == "Hello, world"
    assert stream.last_event_id == f"{GEN}:6"
    assert stream.request_id == REQUEST_ID

    sent = api.calls.last.request
    assert sent.headers["Accept"] == "text/event-stream"
    assert sent.headers["Authorization"] == "Bearer mw_test_key"
    assert json.loads(sent.content) == {"content": "Hi there", "model": "grt-nano"}


def test_text_yields_only_the_answer(api, client: MatterWave) -> None:
    api.post(f"/api/conversations/{CONV}/messages").mock(return_value=_sse(TURN))
    assert "".join(client.conversations.send(CONV, "Hi there").text()) == "Hello, world"


def test_tool_parts_unknown_events_and_notices(api, client: MatterWave) -> None:
    body = (
        sse_frame("message", USER)
        + sse_frame(
            "part_start",
            {"index": 0, "type": "tool_call", "id": "call_0", "name": "web_search", "arguments": "{}"},
            f"{GEN}:0",
        )
        + sse_frame(
            "part_start",
            {"index": 1, "type": "tool_result", "id": "call_0", "output": "none", "is_error": True},
            f"{GEN}:1",
        )
        + sse_frame("notice", {"detail": "Older messages may be cut.", "code": "compaction"})
        + sse_frame("sparkles", {"n": 1})
        + sse_frame("done", ASSISTANT)
    )
    api.post(f"/api/conversations/{CONV}/messages").mock(return_value=_sse(body))
    got = list(client.conversations.send(CONV, "look it up"))
    call, result, notice, unknown = got[1], got[2], got[3], got[4]
    assert isinstance(call, f.PartStartFrame) and call.call_id == "call_0"
    assert call.name == "web_search" and call.arguments == "{}"
    assert isinstance(result, f.PartStartFrame) and result.is_error and result.output == "none"
    # The server's notice is `{detail, code}`, the same keys as an error frame.
    assert isinstance(notice, f.NoticeFrame)
    assert (notice.detail, notice.code) == ("Older messages may be cut.", "compaction")
    assert isinstance(unknown, f.UnknownFrame) and unknown.event == "sparkles"
    assert unknown.data == {"n": 1}


@pytest.mark.parametrize(
    ("event", "data", "cls"),
    [
        ("error", {"detail": "Budget used up.", "code": "budget"}, f.ErrorFrame),
        (
            "moderation_block",
            {"detail": "Not sent.", "code": "moderation", "request_id": "req-9"},
            f.ModerationBlockFrame,
        ),
    ],
)
def test_error_and_moderation_end_the_turn_as_frames(api, client, event, data, cls) -> None:
    # Anything after the terminal frame is not read.
    body = sse_frame(event, data) + sse_frame("done", ASSISTANT)
    api.post(f"/api/conversations/{CONV}/messages").mock(return_value=_sse(body))
    got = list(client.conversations.send(CONV, "hi"))
    assert len(got) == 1 and isinstance(got[0], cls)
    assert got[0].code == data["code"]


# --- resume ---------------------------------------------------------------------


def test_a_dropped_body_resumes_once_from_the_last_event_id(api, client, sleeps) -> None:
    route = api.post(f"/api/conversations/{CONV}/messages").mock(
        side_effect=[_sse(_dropping(HEAD)), _sse(TAIL)]
    )
    stream = client.conversations.send(CONV, "Hi there")
    assert "".join(stream.text()) == "Hello, world"
    assert route.call_count == 2
    resumed = route.calls[1].request
    assert resumed.headers["Last-Event-ID"] == f"{GEN}:4"
    # The same body: the header, not the body, is what the server reads.
    assert json.loads(resumed.content) == {"content": "Hi there"}
    assert stream.result is not None and stream.result.id == "m-asst"
    assert len(sleeps) == 1


def test_a_body_that_ends_cleanly_before_done_also_resumes(api, client, sleeps) -> None:
    route = api.post(f"/api/conversations/{CONV}/messages").mock(
        side_effect=[_sse(HEAD), _sse(TAIL)]
    )
    frames = list(client.conversations.send(CONV, "Hi there"))
    assert isinstance(frames[-1], f.DoneFrame)
    assert route.call_count == 2


def test_an_edit_resumes_through_the_send_route(api, client, sleeps) -> None:
    edit = api.post(f"/api/conversations/{CONV}/messages/m-user/edit").mock(
        return_value=_sse(HEAD)
    )
    resume = api.post(f"/api/conversations/{CONV}/messages").mock(return_value=_sse(TAIL))
    list(client.conversations.edit(CONV, "m-user", "Hi again"))
    assert edit.call_count == 1 and resume.call_count == 1
    assert json.loads(resume.calls[0].request.content) == {"content": "Hi again"}


def test_a_regenerate_resumes_with_the_placeholder_body(api, client, sleeps) -> None:
    api.post(f"/api/conversations/{CONV}/messages/m-asst/regenerate").mock(
        return_value=_sse(HEAD)
    )
    resume = api.post(f"/api/conversations/{CONV}/messages").mock(return_value=_sse(TAIL))
    list(client.conversations.regenerate(CONV, "m-asst", model="grt-nano"))
    assert json.loads(resume.calls[0].request.content) == {
        "content": RESUME_PLACEHOLDER,
        "model": "grt-nano",
    }


def test_only_one_resume_is_attempted(api, client, sleeps) -> None:
    route = api.post(f"/api/conversations/{CONV}/messages").mock(
        side_effect=[_sse(HEAD), _sse(sse_frame("delta", {"content": "!", "index": 1}, f"{GEN}:5"))]
    )
    with pytest.raises(APIConnectionError):
        list(client.conversations.send(CONV, "Hi there"))
    assert route.call_count == 2


def test_a_drop_before_any_event_id_is_not_resumed(api, client, sleeps) -> None:
    # Nothing to resume after; re-sending would bill the prompt twice.
    route = api.post(f"/api/conversations/{CONV}/messages").mock(
        return_value=_sse(sse_frame("message", USER))
    )
    with pytest.raises(APIConnectionError):
        list(client.conversations.send(CONV, "Hi there"))
    assert route.call_count == 1


def _gzip_cut_short(body: str) -> list[bytes]:
    """`body` compressed and flushed so it decodes whole, then a read of
    bytes that are not a deflate stream: what a compressed response looks
    like when the connection dies and a proxy pads or garbles the tail."""
    compressor = zlib.compressobj(wbits=zlib.MAX_WBITS | 16)
    good = compressor.compress(body.encode()) + compressor.flush(zlib.Z_SYNC_FLUSH)
    return [good, b"\xff" * 16]


def _gzipped(chunks: Iterator[bytes] | AsyncIterator[bytes]) -> httpx.Response:
    return httpx.Response(
        200,
        headers={"Content-Type": "text/event-stream", "Content-Encoding": "gzip"},
        content=chunks,
    )


async def _achunks(chunks: list[bytes]) -> AsyncIterator[bytes]:
    for chunk in chunks:
        yield chunk


def test_a_body_that_stops_decoding_resumes_like_a_dropped_one(sleeps) -> None:
    # Through httpx's own transport seam rather than respx, so the gzip
    # decoding -- and the DecodingError it raises mid-body -- is httpx's.
    answers = iter([_gzipped(iter(_gzip_cut_short(HEAD))), _sse(TAIL)])
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return next(answers)

    client = MatterWave(
        api_key="mw_test_key",
        base_url="https://matterwave.test",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    stream = client.conversations.send(CONV, "Hi there")
    assert "".join(stream.text()) == "Hello, world"
    assert len(seen) == 2 and seen[1].headers["Last-Event-ID"] == f"{GEN}:4"
    assert stream.result is not None and stream.result.id == "m-asst"


@pytest.mark.asyncio
async def test_the_async_stream_resumes_after_a_decoding_error(sleeps) -> None:
    answers = iter([_gzipped(_achunks(_gzip_cut_short(HEAD))), _sse(TAIL)])

    async def handler(request: httpx.Request) -> httpx.Response:
        return next(answers)

    client = AsyncMatterWave(
        api_key="mw_test_key",
        base_url="https://matterwave.test",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    stream = await client.conversations.send(CONV, "Hi there")
    assert "".join([t async for t in stream.text()]) == "Hello, world"


def test_a_stream_misused_by_the_caller_is_not_resumed(api, client, sleeps) -> None:
    # Reading a closed response is this client's mistake, not a dropped
    # body; resuming would hide the bug behind a replayed turn.
    route = api.post(f"/api/conversations/{CONV}/messages").mock(return_value=_sse(TURN))
    stream = client.conversations.send(CONV, "Hi there")
    stream.close()
    with pytest.raises(httpx.StreamClosed):
        list(stream)
    assert route.call_count == 1


@pytest.mark.asyncio
async def test_the_async_stream_resumes_the_same_way(api, async_client: AsyncMatterWave, sleeps) -> None:
    route = api.post(f"/api/conversations/{CONV}/messages").mock(
        side_effect=[_sse(_adropping(HEAD)), _sse(TAIL)]
    )
    stream = await async_client.conversations.send(CONV, "Hi there")
    got = [x async for x in stream]
    assert isinstance(got[0], f.CompactionFrame) and isinstance(got[-1], f.DoneFrame)
    assert route.calls[1].request.headers["Last-Event-ID"] == f"{GEN}:4"
    assert stream.result is not None and stream.result.content == "Hello, world"


@pytest.mark.asyncio
async def test_the_async_text_helper(api, async_client: AsyncMatterWave) -> None:
    api.post(f"/api/conversations/{CONV}/messages").mock(return_value=_sse(TURN))
    stream = await async_client.conversations.send(CONV, "Hi there")
    assert "".join([t async for t in stream.text()]) == "Hello, world"


# --- the JSON routes ------------------------------------------------------------


def test_list_search_create_and_get(api, client: MatterWave) -> None:
    listed = api.get("/api/conversations").mock(
        return_value=httpx.Response(200, json={"data": [conversation()], "filter": "pinned"})
    )
    found = api.get("/api/conversations/search").mock(
        return_value=httpx.Response(
            200,
            json={"data": [conversation(snippet="<b>hi</b>", message_id="m-user")], "query": "hi"},
        )
    )
    created = api.post("/api/conversations").mock(
        return_value=httpx.Response(201, json=conversation(title="Plans"))
    )
    got = api.get(f"/api/conversations/{CONV}").mock(
        return_value=httpx.Response(
            200,
            json=conversation(
                messages=[USER, ASSISTANT],
                tree=[USER, ASSISTANT],
                instructions={"user": None},
            ),
        )
    )

    page = client.conversations.list(filter="pinned")
    assert page.filter == "pinned" and [c.id for c in page] == [CONV]
    assert listed.calls.last.request.url.params["filter"] == "pinned"

    hits = client.conversations.search("hi", limit=5)
    assert hits.data[0].snippet == "<b>hi</b>" and hits.data[0].conversation.id == CONV
    assert dict(found.calls.last.request.url.params) == {"q": "hi", "limit": "5"}

    conv = client.conversations.create(title="Plans", model="grt-nano")
    assert conv.title == "Plans"
    assert json.loads(created.calls.last.request.content) == {"title": "Plans", "model": "grt-nano"}

    full = client.conversations.get(CONV, full=True)
    assert [m.id for m in full.messages] == ["m-user", "m-asst"]
    assert full.tree is not None and len(full.tree) == 2
    assert full.messages[1].parent_id == "m-user"
    assert got.calls.last.request.url.params["full"] == "1"


def test_create_can_ask_for_a_temporary_conversation(api, client: MatterWave) -> None:
    created = api.post("/api/conversations").mock(
        return_value=httpx.Response(201, json=conversation(temporary=True))
    )
    temp = client.conversations.create(title="Scratch", temporary=True)
    assert temp.temporary is True
    assert json.loads(created.calls.last.request.content) == {
        "title": "Scratch",
        "temporary": True,
    }
    # False is sent as false, so the caller's explicit choice survives any
    # server default drift; leaving it out sends nothing.
    client.conversations.create(title="Keep", temporary=False)
    assert json.loads(created.calls.last.request.content) == {"title": "Keep", "temporary": False}
    client.conversations.create(title="Quiet")
    assert json.loads(created.calls.last.request.content) == {"title": "Quiet"}


@pytest.mark.asyncio
async def test_the_async_create_can_ask_for_a_temporary_conversation(
    api, async_client: AsyncMatterWave
) -> None:
    created = api.post("/api/conversations").mock(
        return_value=httpx.Response(201, json=conversation(temporary=True))
    )
    temp = await async_client.conversations.create(temporary=True)
    assert temp.temporary is True
    assert json.loads(created.calls.last.request.content) == {"temporary": True}


SECOND = "c0ffee00-0000-4000-8000-000000000002"
THIRD = "c0ffee00-0000-4000-8000-000000000003"


def _three_pages(request: httpx.Request) -> httpx.Response:
    """The fake's list: three pages of one, keyed by the cursor sent."""
    cursor = request.url.params.get("cursor")
    if cursor is None:
        body = {"data": [conversation()], "filter": "default", "next_cursor": "cur-1"}
    elif cursor == "cur-1":
        body = {"data": [conversation(id=SECOND)], "filter": "default", "next_cursor": "cur-2"}
    elif cursor == "cur-2":
        body = {"data": [conversation(id=THIRD)], "filter": "default", "next_cursor": None}
    else:
        return httpx.Response(422, json={"detail": "That cursor is not one this list produced."})
    return httpx.Response(200, json=body)


def test_list_sends_limit_and_cursor_and_pages_walks_three_pages(api, client: MatterWave) -> None:
    listed = api.get("/api/conversations").mock(side_effect=_three_pages)

    first = client.conversations.list(limit=1)
    assert first.next_cursor == "cur-1" and [c.id for c in first] == [CONV]
    assert dict(listed.calls.last.request.url.params) == {"limit": "1"}
    second = client.conversations.list(limit=1, cursor=first.next_cursor)
    assert [c.id for c in second] == [SECOND]
    assert dict(listed.calls.last.request.url.params) == {"limit": "1", "cursor": "cur-1"}

    walked = [c.id for page in client.conversations.pages(limit=1) for c in page]
    assert walked == [CONV, SECOND, THIRD]
    assert [dict(call.request.url.params) for call in list(listed.calls)[-3:]] == [
        {"limit": "1"},
        {"limit": "1", "cursor": "cur-1"},
        {"limit": "1", "cursor": "cur-2"},
    ]


@pytest.mark.asyncio
async def test_the_async_pages_walk_the_same_way(api, async_client: AsyncMatterWave) -> None:
    listed = api.get("/api/conversations").mock(side_effect=_three_pages)
    walked = [c.id async for page in async_client.conversations.pages(filter="all") for c in page]
    assert walked == [CONV, SECOND, THIRD]
    assert listed.call_count == 3
    assert listed.calls.last.request.url.params["filter"] == "all"


def test_tree_active_leaf_fork_and_context(api, client: MatterWave) -> None:
    api.get(f"/api/conversations/{CONV}/tree").mock(
        return_value=httpx.Response(200, json=conversation(messages=[USER, ASSISTANT]))
    )
    leaf = api.put(f"/api/conversations/{CONV}/active-leaf").mock(
        return_value=httpx.Response(
            200, json=conversation(current_leaf_message_id="m-user", messages=[USER])
        )
    )
    fork = api.post(f"/api/conversations/{CONV}/fork").mock(
        return_value=httpx.Response(201, json=conversation(id="c-2", messages=[USER]))
    )
    api.get(f"/api/conversations/{CONV}/context").mock(
        return_value=httpx.Response(
            200,
            json={"model": "grt-nano", "context_window": 8192, "used_tokens": None, "compactable": True, "estimated": True},
        )
    )

    assert len(client.conversations.tree(CONV).messages) == 2
    moved = client.conversations.set_active_leaf(CONV, "m-user")
    assert moved.current_leaf_message_id == "m-user"
    assert json.loads(leaf.calls.last.request.content) == {"message_id": "m-user"}
    assert client.conversations.fork(CONV, "m-user", title="Side").id == "c-2"
    assert json.loads(fork.calls.last.request.content) == {"message_id": "m-user", "title": "Side"}
    context = client.conversations.context(CONV)
    assert context.context_window == 8192 and context.used_tokens is None and context.compactable
    assert context.estimated


def test_update_sets_and_clears_the_default_model(api, client: MatterWave) -> None:
    route = api.patch(f"/api/conversations/{CONV}").mock(
        side_effect=lambda request: httpx.Response(
            200,
            json=conversation(default_model=json.loads(request.content)["default_model"]),
            headers={"X-Request-Id": REQUEST_ID},
        )
    )
    updated = client.conversations.update(CONV, default_model="grt-max")
    assert updated.default_model == "grt-max" and updated.request_id == REQUEST_ID
    assert json.loads(route.calls.last.request.content) == {"default_model": "grt-max"}
    # None clears it: the field is sent as null, never left out, because an
    # absent field is "nothing to update" to the server.
    assert client.conversations.update(CONV, default_model=None).default_model is None
    assert json.loads(route.calls.last.request.content) == {"default_model": None}


@pytest.mark.asyncio
async def test_the_async_update_sends_the_same_body(api, async_client: AsyncMatterWave) -> None:
    route = api.patch(f"/api/conversations/{CONV}").mock(
        return_value=httpx.Response(200, json=conversation(default_model="grt-max"))
    )
    updated = await async_client.conversations.update(CONV, default_model="grt-max")
    assert updated.default_model == "grt-max"
    assert json.loads(route.calls.last.request.content) == {"default_model": "grt-max"}


def test_update_renames_without_touching_the_model(api, client: MatterWave) -> None:
    route = api.patch(f"/api/conversations/{CONV}").mock(
        side_effect=lambda request: httpx.Response(
            200, json=conversation(title=json.loads(request.content)["title"])
        )
    )
    renamed = client.conversations.update(CONV, title="Quarterly planning")
    assert renamed.title == "Quarterly planning"
    # Only the title goes: a `default_model` key, even null, would clear the model.
    assert json.loads(route.calls.last.request.content) == {"title": "Quarterly planning"}

    client.conversations.update(CONV, title="Both", default_model="grt-max")
    assert json.loads(route.calls.last.request.content) == {
        "title": "Both",
        "default_model": "grt-max",
    }


def test_update_refuses_an_empty_call_or_title_before_sending(api, client: MatterWave) -> None:
    route = api.patch(f"/api/conversations/{CONV}").mock(return_value=httpx.Response(200))
    with pytest.raises(ValueError, match="title or a default_model"):
        client.conversations.update(CONV)
    with pytest.raises(ValueError, match="non-empty"):
        client.conversations.update(CONV, title="   ")
    assert not route.called


@pytest.mark.asyncio
async def test_the_async_update_renames_with_the_same_body(
    api, async_client: AsyncMatterWave
) -> None:
    route = api.patch(f"/api/conversations/{CONV}").mock(
        return_value=httpx.Response(200, json=conversation(title="Renamed"))
    )
    renamed = await async_client.conversations.update(CONV, title="Renamed")
    assert renamed.title == "Renamed"
    assert json.loads(route.calls.last.request.content) == {"title": "Renamed"}


def test_compact_returns_the_summary_message_with_the_request_id(api, client: MatterWave) -> None:
    api.post(f"/api/conversations/{CONV}/compact").mock(
        return_value=httpx.Response(200, json=SUMMARY, headers={"X-Request-Id": REQUEST_ID})
    )
    summary = client.conversations.compact(CONV)
    assert summary.role == "system"
    assert summary.summary is not None and summary.summary["tokens_after"] == 300
    # The summary is the whole response, so it carries the id to quote.
    assert summary.request_id == REQUEST_ID


@pytest.mark.asyncio
async def test_the_async_compact_carries_the_request_id(api, async_client: AsyncMatterWave) -> None:
    api.post(f"/api/conversations/{CONV}/compact").mock(
        return_value=httpx.Response(200, json=SUMMARY, headers={"X-Request-Id": REQUEST_ID})
    )
    summary = await async_client.conversations.compact(CONV)
    assert summary.request_id == REQUEST_ID


def test_a_compaction_frame_without_counts_keeps_them_unknown() -> None:
    # A zero would read as "the fold saved nothing"; None says the frame did not say.
    frame = f.frame_from_event("compaction", {"message": SUMMARY, "covers_from": "m-0"})
    assert isinstance(frame, f.CompactionFrame)
    assert frame.tokens_before is None and frame.tokens_after is None
    assert frame.covers_from == "m-0" and frame.covers_to is None


def test_compact_with_nothing_to_fold_is_a_conflict(api, client: MatterWave) -> None:
    api.post(f"/api/conversations/{CONV}/compact").mock(
        return_value=httpx.Response(
            409,
            json={"detail": "Nothing to compact yet.", "request_id": REQUEST_ID},
            headers={"X-Request-Id": REQUEST_ID},
        )
    )
    with pytest.raises(ConflictError) as caught:
        client.conversations.compact(CONV)
    assert caught.value.status_code == 409
    assert caught.value.message == "Nothing to compact yet."
    assert caught.value.request_id == REQUEST_ID


def test_an_app_route_404_reads_the_detail(api, client: MatterWave) -> None:
    api.get(f"/api/conversations/{CONV}").mock(
        return_value=httpx.Response(404, json={"detail": "No such conversation", "request_id": "r-1"})
    )
    with pytest.raises(NotFoundError) as caught:
        client.conversations.get(CONV)
    assert caught.value.message == "No such conversation"
    assert caught.value.request_id == "r-1"


def test_me(api, client: MatterWave) -> None:
    api.get("/api/me").mock(
        return_value=httpx.Response(
            200,
            json={
                "subject": "u-1",
                "kind": "key",
                "email": "a@example.test",
                "name": "Ada",
                "roles": [],
                "org_id": "org-1",
                "org_name": "Acme",
                "org_role": "member",
                "state": "active",
            },
        )
    )
    me = client.me()
    assert me.subject == "u-1" and me.org_name == "Acme" and me.kind == "key"


# --- lifecycle: delete, pin and archive -----------------------------------------


def test_delete_sends_delete_and_reads_the_status(api, client: MatterWave) -> None:
    route = api.delete(f"/api/conversations/{CONV}").mock(
        return_value=httpx.Response(
            200,
            json={"status": "deleted", "attachments_deleted": 2},
            headers={"X-Request-Id": REQUEST_ID},
        )
    )
    deleted = client.conversations.delete(CONV)
    assert route.calls.last.request.method == "DELETE"
    assert deleted.status == "deleted" and deleted.attachments_deleted == 2
    assert deleted.request_id == REQUEST_ID
    # A conversation without attachments keeps the plain shape: no count at all.
    api.delete(f"/api/conversations/{CONV}").mock(
        return_value=httpx.Response(200, json={"status": "deleted"})
    )
    assert client.conversations.delete(CONV).attachments_deleted is None


@pytest.mark.asyncio
async def test_the_async_delete_reads_the_same_body(
    api, async_client: AsyncMatterWave
) -> None:
    api.delete(f"/api/conversations/{CONV}").mock(
        return_value=httpx.Response(200, json={"status": "deleted"})
    )
    deleted = await async_client.conversations.delete(CONV)
    assert deleted.status == "deleted"


def test_pin_unpin_archive_and_unarchive_toggle_by_method(api, client: MatterWave) -> None:
    route = api.put(f"/api/conversations/{CONV}/pin").mock(
        return_value=httpx.Response(
            200,
            json=conversation(pinned_at="2026-10-18T10:00:00+00:00"),
            headers={"X-Request-Id": REQUEST_ID},
        )
    )
    down = api.delete(f"/api/conversations/{CONV}/pin").mock(
        return_value=httpx.Response(200, json=conversation())
    )
    archive = api.put(f"/api/conversations/{CONV}/archive").mock(
        return_value=httpx.Response(
            200, json=conversation(archived_at="2026-10-18T10:00:00+00:00")
        )
    )
    up = api.delete(f"/api/conversations/{CONV}/archive").mock(
        return_value=httpx.Response(200, json=conversation())
    )

    pinned = client.conversations.pin(CONV)
    assert pinned.pinned_at is not None and pinned.request_id == REQUEST_ID
    assert route.calls.last.request.method == "PUT"
    assert client.conversations.unpin(CONV).pinned_at is None
    assert down.calls.last.request.method == "DELETE"
    assert client.conversations.archive(CONV).archived_at is not None
    assert archive.calls.last.request.method == "PUT"
    assert client.conversations.unarchive(CONV).archived_at is None
    assert up.calls.last.request.method == "DELETE"
    # A toggle carries no body: the method is the whole instruction.
    assert route.calls.last.request.content == b""


@pytest.mark.asyncio
async def test_the_async_pin_sends_the_same_verb(
    api, async_client: AsyncMatterWave
) -> None:
    route = api.put(f"/api/conversations/{CONV}/pin").mock(
        return_value=httpx.Response(200, json=conversation(pinned_at="2026-10-18T10:00:00+00:00"))
    )
    pinned = await async_client.conversations.pin(CONV)
    assert pinned.pinned_at is not None
    assert route.calls.last.request.method == "PUT"


def test_a_pin_that_409s_for_a_temporary_conversation(api, client: MatterWave) -> None:
    # The route's own words (store.py's TEMPORARY_CONVERSATION_DETAIL):
    # keeping is exactly what a temporary conversation does not do.
    api.put(f"/api/conversations/{CONV}/pin").mock(
        return_value=httpx.Response(
            409,
            json={
                "detail": "A temporary conversation is not kept, so it cannot be pinned.",
                "request_id": REQUEST_ID,
            },
        )
    )
    with pytest.raises(ConflictError) as caught:
        client.conversations.pin(CONV)
    assert caught.value.status_code == 409
    assert caught.value.message == "A temporary conversation is not kept, so it cannot be pinned."


def test_a_fork_of_a_temporary_conversation_is_the_same_conflict(
    api, client: MatterWave
) -> None:
    api.post(f"/api/conversations/{CONV}/fork").mock(
        return_value=httpx.Response(
            409,
            json={
                "detail": "A temporary conversation is not kept, so it cannot be forked.",
                "request_id": REQUEST_ID,
            },
        )
    )
    with pytest.raises(ConflictError) as caught:
        client.conversations.fork(CONV, "m-user")
    assert caught.value.message == "A temporary conversation is not kept, so it cannot be forked."


# --- instructions -----------------------------------------------------------------


def test_conversation_instructions_are_set_and_cleared(api, client: MatterWave) -> None:
    route = api.put(f"/api/conversations/{CONV}/instructions").mock(
        side_effect=lambda request: httpx.Response(
            200,
            json={
                "conversation_id": CONV,
                "instructions": {
                    "org": "Be brief.",
                    "user": {"about": "", "style": ""},
                    "conversation": json.loads(request.content)["text"] or "",
                    "user_overridden": True,
                    "effective": "Be brief.",
                },
            },
            headers={"X-Request-Id": REQUEST_ID},
        )
    )
    set_ = client.conversations.update_instructions(CONV, "Answer in French.")
    assert set_.conversation_id == CONV
    assert set_.instructions["user_overridden"] is True
    assert set_.request_id == REQUEST_ID
    assert json.loads(route.calls.last.request.content) == {"text": "Answer in French."}
    # None is sent as null: the field always goes, and the server reads it
    # as "clear the thread's prompt".
    client.conversations.update_instructions(CONV, None)
    assert json.loads(route.calls.last.request.content) == {"text": None}


@pytest.mark.asyncio
async def test_the_async_instructions_clear_the_same_way(
    api, async_client: AsyncMatterWave
) -> None:
    route = api.put(f"/api/conversations/{CONV}/instructions").mock(
        return_value=httpx.Response(
            200,
            json={"conversation_id": CONV, "instructions": {"conversation": ""}},
        )
    )
    cleared = await async_client.conversations.update_instructions(CONV, None)
    assert cleared.instructions == {"conversation": ""}
    assert json.loads(route.calls.last.request.content) == {"text": None}


def test_ids_that_would_change_the_path_are_refused(client: MatterWave) -> None:
    with pytest.raises(ValueError):
        client.conversations.get("../me")
    with pytest.raises(ValueError):
        client.conversations.set_active_leaf(CONV, "")
    with pytest.raises(ValueError):
        client.conversations.list(filter="deleted")


def test_every_app_operation_is_a_client_method() -> None:
    client = MatterWave(api_key="k", base_url="https://matterwave.test")
    for dotted in APP_OPERATIONS:
        target: object = client
        for part in dotted.split("."):
            assert hasattr(target, part), f"client has no {dotted}"
            target = getattr(target, part)
        assert callable(target)


def test_a_context_frame_without_usage_keeps_the_counts_unknown() -> None:
    # generation.py sends `prompt_tokens: null` when the upstream reported no
    # usage; reading that as 0 would draw an empty meter as if it were known.
    frame = f.frame_from_event(
        "context", {"prompt_tokens": None, "completion_tokens": None, "context_window": 1024}
    )
    assert isinstance(frame, f.ContextFrame)
    assert frame.prompt_tokens is None and frame.completion_tokens is None
    assert frame.context_window == 1024


def test_done_carries_truncated_and_the_finish_reason() -> None:
    # A round cut by the token limit persists `truncated` with `finish_reason: "length"`.
    frame = f.frame_from_event(
        "done", message("assistant", "Half a sen", "a9", "u9", truncated=True, finish_reason="length")
    )
    assert isinstance(frame, f.DoneFrame)
    assert frame.message.truncated and frame.message.finish_reason == "length"
