"""The conversation stream's frames, typed.

`POST /api/conversations/{id}/messages` (and `edit`, `regenerate`) answer
with named server-sent events, unlike `/v1`'s data-only chunks: each frame
is `event: <name>` and one `data:` line of JSON, with `id:` on the frames a
reconnect can resume after. A turn reads, in order:

    (compaction | notice)?  message  title?  (part_start delta* part_end)*  context?  done

`done` carries the saved assistant message; `error` and `moderation_block`
end a turn instead. An event this client does not know arrives as
`UnknownFrame` rather than being dropped, so a newer server's additions are
visible to a caller that wants them and harmless to one that does not.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .models import ConversationMessage


def _int(value: Any, default: int = 0) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else default


def _opt_int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _opt_str(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def _message(value: Any) -> ConversationMessage:
    return ConversationMessage.from_dict(value if isinstance(value, dict) else {})


@dataclass(frozen=True)
class MessageFrame:
    """The user message this turn appended (or the edited one), as saved."""

    message: ConversationMessage
    id: str | None = None


@dataclass(frozen=True)
class PartStartFrame:
    """A part of the assistant message opens at `index`. `type` is `text`,
    `reasoning`, `tool_call` or `tool_result`; a tool call carries its
    `call_id`, `name` and `arguments`, a result its `output` and `is_error`."""

    index: int
    type: str
    call_id: str | None = None
    name: str | None = None
    arguments: str | None = None
    output: str | None = None
    is_error: bool = False
    id: str | None = None


@dataclass(frozen=True)
class DeltaFrame:
    """Text for the open part at `index`: `content` for an answer, or
    `reasoning` for a reasoning part. One of the two is set."""

    index: int
    content: str | None = None
    reasoning: str | None = None
    id: str | None = None


@dataclass(frozen=True)
class PartEndFrame:
    index: int
    id: str | None = None


@dataclass(frozen=True)
class TitleFrame:
    """The conversation was named from this turn's first line."""

    title: str
    id: str | None = None


@dataclass(frozen=True)
class DoneFrame:
    """The turn ended; `message` is the saved assistant message. Its
    `truncated` is True when the answer was cut short."""

    message: ConversationMessage
    id: str | None = None


@dataclass(frozen=True)
class ErrorFrame:
    """The turn failed after the 200: the upstream died, the budget is spent
    (`code` "budget"), or a resume found nothing to resume. Ends the turn."""

    detail: str
    code: str | None = None
    id: str | None = None


@dataclass(frozen=True)
class ModerationBlockFrame:
    """MatterWave would not send this message to a model. A different fact
    from the model declining to answer, so a different frame. Ends the turn."""

    detail: str
    code: str | None = None
    request_id: str | None = None
    id: str | None = None


@dataclass(frozen=True)
class CompactionFrame:
    """Earlier messages were folded into `message`, a `system` message with
    one `summary` part, before this turn was sent. The token counts are
    None when the frame did not carry them, never a made-up zero."""

    message: ConversationMessage
    covers_from: str | None
    covers_to: str | None
    tokens_before: int | None
    tokens_after: int | None
    id: str | None = None


@dataclass(frozen=True)
class ContextFrame:
    """The real token counts of the turn, for a context meter. Each is None
    when the server does not know it: `context_window` when the model's size
    is unknown, the counts when the upstream reported no usage."""

    prompt_tokens: int | None
    completion_tokens: int | None
    context_window: int | None
    id: str | None = None


@dataclass(frozen=True)
class NoticeFrame:
    """Something the person should be told that is not part of the answer:
    `detail` is the sentence to show, `code` the machine reason (`compaction`
    when the history could not be summarised)."""

    detail: str
    code: str | None = None
    id: str | None = None


@dataclass(frozen=True)
class UnknownFrame:
    """An event this client predates, kept as sent."""

    event: str
    data: Any = field(default=None)
    id: str | None = None


ConversationFrame = (
    MessageFrame
    | PartStartFrame
    | DeltaFrame
    | PartEndFrame
    | TitleFrame
    | DoneFrame
    | ErrorFrame
    | ModerationBlockFrame
    | CompactionFrame
    | ContextFrame
    | NoticeFrame
    | UnknownFrame
)

# The frames after which the server sends nothing more for this turn.
TERMINAL_FRAMES = (DoneFrame, ErrorFrame, ModerationBlockFrame)

# Every event this client types, by its wire name. The contract test holds
# this to the `x-sse-events` of docs/developers/openapi.json, so a frame the
# server adds and a frame typed here have to land together.
FRAMES_BY_EVENT: dict[str, type[ConversationFrame]] = {
    "message": MessageFrame,
    "part_start": PartStartFrame,
    "delta": DeltaFrame,
    "part_end": PartEndFrame,
    "title": TitleFrame,
    "done": DoneFrame,
    "error": ErrorFrame,
    "moderation_block": ModerationBlockFrame,
    "compaction": CompactionFrame,
    "context": ContextFrame,
    "notice": NoticeFrame,
}


def frame_from_event(event: str, data: Any, event_id: str | None = None) -> ConversationFrame:
    """The typed frame for one event. `data` is the decoded JSON; a known
    event whose data is not an object is read as an empty one."""
    d: dict[str, Any] = data if isinstance(data, dict) else {}
    if event == "message":
        return MessageFrame(message=_message(d), id=event_id)
    if event == "part_start":
        return PartStartFrame(
            index=_int(d.get("index")),
            type=str(d.get("type") or ""),
            call_id=_opt_str(d.get("id")),
            name=_opt_str(d.get("name")),
            arguments=_opt_str(d.get("arguments")),
            output=_opt_str(d.get("output")),
            is_error=d.get("is_error") is True,
            id=event_id,
        )
    if event == "delta":
        return DeltaFrame(
            index=_int(d.get("index")),
            content=_opt_str(d.get("content")),
            reasoning=_opt_str(d.get("reasoning")),
            id=event_id,
        )
    if event == "part_end":
        return PartEndFrame(index=_int(d.get("index")), id=event_id)
    if event == "title":
        return TitleFrame(title=str(d.get("title") or ""), id=event_id)
    if event == "done":
        return DoneFrame(message=_message(d), id=event_id)
    if event == "error":
        return ErrorFrame(
            detail=str(d.get("detail") or "The stream ended with an error."),
            code=_opt_str(d.get("code")),
            id=event_id,
        )
    if event == "moderation_block":
        return ModerationBlockFrame(
            detail=str(d.get("detail") or ""),
            code=_opt_str(d.get("code")),
            request_id=_opt_str(d.get("request_id")),
            id=event_id,
        )
    if event == "compaction":
        return CompactionFrame(
            message=_message(d.get("message")),
            covers_from=_opt_str(d.get("covers_from")),
            covers_to=_opt_str(d.get("covers_to")),
            tokens_before=_opt_int(d.get("tokens_before")),
            tokens_after=_opt_int(d.get("tokens_after")),
            id=event_id,
        )
    if event == "context":
        return ContextFrame(
            prompt_tokens=_opt_int(d.get("prompt_tokens")),
            completion_tokens=_opt_int(d.get("completion_tokens")),
            context_window=_opt_int(d.get("context_window")),
            id=event_id,
        )
    if event == "notice":
        return NoticeFrame(
            detail=str(d.get("detail") or ""), code=_opt_str(d.get("code")), id=event_id
        )
    return UnknownFrame(event=event, data=data, id=event_id)
