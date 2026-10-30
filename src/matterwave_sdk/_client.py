"""The two clients. `MatterWave` blocks; `AsyncMatterWave` awaits. They share
every decision through `_base` and differ only in how they wait.

    from matterwave_sdk import MatterWave

    client = MatterWave()  # MATTERWAVE_API_KEY and MATTERWAVE_BASE_URL
    completion = client.chat.completions.create(
        messages=[{"role": "user", "content": "Say hello in five words."}],
    )
    print(completion.content)

    for chunk in client.chat.completions.create(messages=..., stream=True):
        print(chunk.content, end="")

A 429 (the per-key window) and any 5xx are retried `max_retries` times with
backoff, honouring `Retry-After` when the server sends it; a 429 whose code
is `insufficient_quota` is not, because waiting does not refill a budget.
Every error carries the `X-Request-Id` the server stamped on the refusal.
"""

from __future__ import annotations

import asyncio
import inspect
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator, Mapping, Sequence
from types import TracebackType
from typing import Any, Literal, overload

import httpx

from . import _base
from ._base import EmbeddingInput, Message, Request, Tool, ToolChoice
from ._mcp import AsyncMcp, Mcp
from ._resources import (
    Admin,
    Announcements,
    AsyncAdmin,
    AsyncAnnouncements,
    AsyncAttachments,
    AsyncBudgetClient,
    AsyncEntitlementsClient,
    AsyncKeys,
    AsyncLibraryClient,
    AsyncNotifications,
    AsyncShares,
    AsyncUsageClient,
    AsyncWebhooks,
    Attachments,
    BudgetClient,
    EntitlementsClient,
    Keys,
    LibraryClient,
    Notifications,
    Shares,
    UsageClient,
    Webhooks,
)
from ._version import __version__
from .errors import (
    APIConnectionError,
    APIError,
    APITimeoutError,
    DeviceAuthError,
    MatterWaveError,
)
from .frames import TERMINAL_FRAMES, ConversationFrame, DeltaFrame, DoneFrame, frame_from_event
from .models import (
    FEEDBACK_REASONS,
    REPORT_KINDS,
    ApiIndex,
    AppModelList,
    ChatCompletion,
    ChatCompletionChunk,
    Conversation,
    ConversationContext,
    ConversationInstructions,
    ConversationList,
    ConversationMessage,
    DeletedConversation,
    DeviceCode,
    DeviceToken,
    EmbeddingList,
    FiledReport,
    Me,
    MessageFeedback,
    MessageFeedbackList,
    Model,
    ModelList,
    OrgInstructions,
    RemovedFeedback,
    SearchResults,
    UserInstructions,
)

USER_AGENT = f"matterwave-python/{__version__}"


class _Config:
    def __init__(
        self,
        api_key: str | None,
        base_url: str | None,
        timeout: float | httpx.Timeout | None,
        max_retries: int,
        default_headers: Mapping[str, str] | None,
    ) -> None:
        if max_retries < 0:
            raise ValueError("max_retries must be 0 or more")
        self.api_key = _base.resolve_api_key(api_key)
        self.base_url = _base.resolve_base_url(base_url)
        self.timeout = _base.DEFAULT_TIMEOUT_S if timeout is None else timeout
        self.max_retries = max_retries
        self.default_headers = dict(default_headers or {})

    def build(self, request: Request, client: httpx.Client | httpx.AsyncClient) -> httpx.Request:
        headers = _base.headers_for(request, self.api_key, USER_AGENT, self.default_headers)
        return client.build_request(
            request.method,
            self.base_url + request.path,
            json=request.json,
            files=dict(request.files) if request.files else None,
            params=dict(request.params) or None,
            headers=headers,
            timeout=request.timeout if request.timeout is not None else self.timeout,
        )


def _connection_error(exc: httpx.HTTPError, request: Request) -> APIConnectionError:
    where = f"{request.method} {request.path}"
    if isinstance(exc, httpx.TimeoutException):
        return APITimeoutError(f"{where} timed out.", cause=exc)
    return APIConnectionError(f"{where} could not reach the API: {exc}", cause=exc)


# --- streams -----------------------------------------------------------------


class Stream:
    """A streamed completion: iterate it for `ChatCompletionChunk`s, or
    `text()` for just the words. Closing it early releases the connection;
    the server records what was sent as a truncated turn when the call
    asked for persistence."""

    def __init__(self, response: httpx.Response) -> None:
        self._response = response
        self.request_id = _base.request_id_of(response)

    def __iter__(self) -> Iterator[ChatCompletionChunk]:
        try:
            for frame in _base.sse_data_frames(self._response.iter_lines()):
                payload = _base.decode_stream_frame(frame, request_id=self.request_id)
                if payload is None:
                    return
                yield ChatCompletionChunk.from_dict(payload, request_id=self.request_id)
        finally:
            self._response.close()

    def text(self) -> Iterator[str]:
        for chunk in self:
            if chunk.content:
                yield chunk.content

    def close(self) -> None:
        self._response.close()

    def __enter__(self) -> Stream:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()


class AsyncStream:
    def __init__(self, response: httpx.Response) -> None:
        self._response = response
        self.request_id = _base.request_id_of(response)

    async def __aiter__(self) -> AsyncIterator[ChatCompletionChunk]:
        try:
            async for frame in _base.async_sse_data_frames(self._response.aiter_lines()):
                payload = _base.decode_stream_frame(frame, request_id=self.request_id)
                if payload is None:
                    return
                yield ChatCompletionChunk.from_dict(payload, request_id=self.request_id)
        finally:
            await self._response.aclose()

    async def text(self) -> AsyncIterator[str]:
        async for chunk in self:
            if chunk.content:
                yield chunk.content

    async def close(self) -> None:
        await self._response.aclose()

    async def __aenter__(self) -> AsyncStream:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.close()


def _dropped(cause: BaseException | None) -> APIConnectionError:
    return APIConnectionError(
        "The conversation stream ended before the turn did, and could not be resumed.",
        cause=cause,
    )


# What a body that stopped arriving raises out of `iter_lines`: the transport
# gave up (a reset, a timeout, a peer that closed mid-message) or the bytes
# it did send would not decode (a compressed body cut short). Either way the
# frames seen so far are good and the rest is what `Last-Event-ID` is for.
# A `StreamError` is not here: it means this client misused the response
# (read it twice, read it after closing), not that the server went away.
DROPPED_BODY = (httpx.TransportError, httpx.DecodingError)


class _FrameReader:
    """What the sync and async conversation streams share: the cursor a
    resume needs and the decoding of each event into its frame."""

    def __init__(self, request: Request, conversation_id: str, request_id: str | None) -> None:
        self.request = request
        self.conversation_id = conversation_id
        self.request_id = request_id
        self.last_event_id: str | None = None
        self.result: ConversationMessage | None = None
        self.consumed = False

    def begin(self) -> None:
        if self.consumed:
            raise MatterWaveError("A stream can be iterated once.")
        self.consumed = True

    def frame(self, event: _base.SSEEvent) -> ConversationFrame:
        if event.id is not None:
            self.last_event_id = event.id
        data = _base.decode_event(event, request_id=self.request_id)
        frame = frame_from_event(event.event, data, event.id)
        if isinstance(frame, DoneFrame):
            self.result = frame.message
        return frame

    def resume(self, cause: BaseException | None, resumed: bool) -> Request:
        """The request that continues the turn, or the error that ends it.
        One resume per stream; a turn that dropped before any frame carried
        an id cannot resume without re-sending, which would bill twice."""
        if resumed or self.last_event_id is None:
            raise _dropped(cause)
        return _base.resume_request(self.request, self.conversation_id, self.last_event_id)


class ConversationStream:
    """One conversation turn as it streams: iterate it for typed frames
    (`matterwave_sdk.frames`), or `text()` for the answer's words.

    The stream ends after `done`, `error` or `moderation_block`. If the body
    drops before then, the turn is picked up once from the last frame seen by
    re-presenting `Last-Event-ID`; the server replays the rest of the same
    generation rather than starting another. `result` is the saved assistant
    message once `done` has arrived."""

    def __init__(
        self, client: MatterWave, request: Request, conversation_id: str, response: httpx.Response
    ) -> None:
        self._client = client
        self._response = response
        self._reader = _FrameReader(request, conversation_id, _base.request_id_of(response))
        self.request_id = self._reader.request_id

    @property
    def last_event_id(self) -> str | None:
        return self._reader.last_event_id

    @property
    def result(self) -> ConversationMessage | None:
        return self._reader.result

    def __iter__(self) -> Iterator[ConversationFrame]:
        self._reader.begin()
        resumed = False
        try:
            while True:
                cause: httpx.HTTPError | None = None
                try:
                    for event in _base.sse_events(self._response.iter_lines()):
                        frame = self._reader.frame(event)
                        yield frame
                        if isinstance(frame, TERMINAL_FRAMES):
                            return
                except DROPPED_BODY as exc:
                    cause = exc
                request = self._reader.resume(cause, resumed)
                resumed = True
                self._response.close()
                time.sleep(_base.backoff_s(0, None))
                self._response = self._client._send(request)
        finally:
            self._response.close()

    def text(self) -> Iterator[str]:
        for frame in self:
            if isinstance(frame, DeltaFrame) and frame.content:
                yield frame.content

    def close(self) -> None:
        self._response.close()

    def __enter__(self) -> ConversationStream:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()


class AsyncConversationStream:
    def __init__(
        self,
        client: AsyncMatterWave,
        request: Request,
        conversation_id: str,
        response: httpx.Response,
    ) -> None:
        self._client = client
        self._response = response
        self._reader = _FrameReader(request, conversation_id, _base.request_id_of(response))
        self.request_id = self._reader.request_id

    @property
    def last_event_id(self) -> str | None:
        return self._reader.last_event_id

    @property
    def result(self) -> ConversationMessage | None:
        return self._reader.result

    async def __aiter__(self) -> AsyncIterator[ConversationFrame]:
        self._reader.begin()
        resumed = False
        try:
            while True:
                cause: httpx.HTTPError | None = None
                try:
                    async for event in _base.async_sse_events(self._response.aiter_lines()):
                        frame = self._reader.frame(event)
                        yield frame
                        if isinstance(frame, TERMINAL_FRAMES):
                            return
                except DROPPED_BODY as exc:
                    cause = exc
                request = self._reader.resume(cause, resumed)
                resumed = True
                await self._response.aclose()
                await asyncio.sleep(_base.backoff_s(0, None))
                self._response = await self._client._send(request)
        finally:
            await self._response.aclose()

    async def text(self) -> AsyncIterator[str]:
        async for frame in self:
            if isinstance(frame, DeltaFrame) and frame.content:
                yield frame.content

    async def close(self) -> None:
        await self._response.aclose()

    async def __aenter__(self) -> AsyncConversationStream:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.close()


# --- the sync client ---------------------------------------------------------


class Completions:
    def __init__(self, client: MatterWave) -> None:
        self._client = client

    @overload
    def create(
        self,
        messages: Sequence[Message],
        *,
        model: str | None = None,
        stream: Literal[False] = False,
        include_usage: bool | None = None,
        max_tokens: int | None = None,
        max_completion_tokens: int | None = None,
        temperature: float | None = None,
        top_p: float | None = None,
        stop: str | Sequence[str] | None = None,
        n: int | None = None,
        user: str | None = None,
        tools: Sequence[Tool] | None = None,
        tool_choice: ToolChoice | None = None,
        response_format: dict[str, Any] | None = None,
        conversation_id: str | None = None,
        extra_body: dict[str, Any] | None = None,
    ) -> ChatCompletion: ...

    @overload
    def create(
        self,
        messages: Sequence[Message],
        *,
        model: str | None = None,
        stream: Literal[True],
        include_usage: bool | None = None,
        max_tokens: int | None = None,
        max_completion_tokens: int | None = None,
        temperature: float | None = None,
        top_p: float | None = None,
        stop: str | Sequence[str] | None = None,
        n: int | None = None,
        user: str | None = None,
        tools: Sequence[Tool] | None = None,
        tool_choice: ToolChoice | None = None,
        response_format: dict[str, Any] | None = None,
        conversation_id: str | None = None,
        extra_body: dict[str, Any] | None = None,
    ) -> Stream: ...

    def create(
        self,
        messages: Sequence[Message],
        *,
        model: str | None = None,
        stream: bool = False,
        include_usage: bool | None = None,
        max_tokens: int | None = None,
        max_completion_tokens: int | None = None,
        temperature: float | None = None,
        top_p: float | None = None,
        stop: str | Sequence[str] | None = None,
        n: int | None = None,
        user: str | None = None,
        tools: Sequence[Tool] | None = None,
        tool_choice: ToolChoice | None = None,
        response_format: dict[str, Any] | None = None,
        conversation_id: str | None = None,
        extra_body: dict[str, Any] | None = None,
    ) -> ChatCompletion | Stream:
        """`POST /v1/chat/completions`. `messages` is the whole prompt; the
        server prefixes nothing. `model=None` is the deployment's default.
        `tools`, `tool_choice` and `response_format` reach the model as
        sent; a tool call comes back in `completion.tool_calls` for the
        caller to run. `conversation_id` records the turn into that
        conversation of the key owner's (stateless otherwise)."""
        body = _base.chat_completion_body(
            messages,
            model=model,
            stream=stream,
            include_usage=include_usage,
            max_tokens=max_tokens,
            max_completion_tokens=max_completion_tokens,
            temperature=temperature,
            top_p=top_p,
            stop=stop,
            n=n,
            user=user,
            tools=tools,
            tool_choice=tool_choice,
            response_format=response_format,
            conversation_id=conversation_id,
            extra_body=extra_body,
        )
        request = _base.chat_completions_request(body)
        if stream:
            return Stream(self._client._send(request))
        response = self._client._send(request)
        return ChatCompletion.from_dict(
            _base.parse_json_object(response), request_id=_base.request_id_of(response)
        )


class Chat:
    def __init__(self, client: MatterWave) -> None:
        self.completions = Completions(client)


class Embeddings:
    def __init__(self, client: MatterWave) -> None:
        self._client = client

    def create(
        self,
        input: EmbeddingInput,
        *,
        model: str | None = None,
        encoding_format: Literal["float", "base64"] | None = None,
        dimensions: int | None = None,
        user: str | None = None,
        extra_body: dict[str, Any] | None = None,
    ) -> EmbeddingList:
        """`POST /v1/embeddings`: one embedding per input. A deployment serves
        one embedding model, so `model` may be omitted; `index().capabilities`
        says whether embeddings are enabled and which model."""
        body = _base.embeddings_body(
            input,
            model=model,
            encoding_format=encoding_format,
            dimensions=dimensions,
            user=user,
            extra_body=extra_body,
        )
        response = self._client._send(_base.embeddings_request(body))
        return EmbeddingList.from_dict(
            _base.parse_json_object(response), request_id=_base.request_id_of(response)
        )


class Models:
    def __init__(self, client: MatterWave) -> None:
        self._client = client

    def list(self) -> ModelList:
        """`GET /v1/models`: what the gateway serves."""
        response = self._client._send(_base.list_models_request())
        return ModelList.from_dict(
            _base.parse_json_object(response), request_id=_base.request_id_of(response)
        )

    def retrieve(self, model_id: str) -> Model:
        """`GET /v1/models/{model_id}`; `NotFoundError` for one not served."""
        response = self._client._send(_base.retrieve_model_request(model_id))
        return Model.from_dict(
            _base.parse_json_object(response), request_id=_base.request_id_of(response)
        )


class Conversations:
    """The app's conversations, as the key's owner sees them in the web app.
    A conversation is a tree: `get` returns the active path, `tree` every
    branch, and `set_active_leaf` moves between them."""

    def __init__(self, client: MatterWave) -> None:
        self._client = client

    def _object(self, request: Request) -> tuple[dict[str, Any], str | None]:
        response = self._client._send(request)
        return _base.parse_json_object(response), _base.request_id_of(response)

    def list(
        self, *, filter: str | None = None, limit: int | None = None, cursor: str | None = None
    ) -> ConversationList:
        """`GET /api/conversations`: one page of the sidebar list. `filter`
        is `default` (unarchived, pinned first), `all`, `pinned` or
        `archived`; `limit` is 1 to 200 (50 when omitted); `cursor` is a
        previous page's `next_cursor`, None on the last page. `pages` walks
        them."""
        body, rid = self._object(_base.list_conversations_request(filter, limit, cursor))
        return ConversationList.from_dict(body, request_id=rid)

    def pages(
        self, *, filter: str | None = None, limit: int | None = None
    ) -> Iterator[ConversationList]:
        """Every page of `list`, first to last, each fetched as the previous
        one is consumed."""
        cursor: str | None = None
        while True:
            page = self.list(filter=filter, limit=limit, cursor=cursor)
            yield page
            cursor = page.next_cursor
            if cursor is None:
                return

    def search(self, q: str, *, limit: int | None = None) -> SearchResults:
        """`GET /api/conversations/search`: full text over the caller's own messages."""
        body, rid = self._object(_base.search_conversations_request(q, limit))
        return SearchResults.from_dict(body, request_id=rid)

    def create(
        self,
        *,
        title: str | None = None,
        model: str | None = None,
        temporary: bool | None = None,
    ) -> Conversation:
        """`POST /api/conversations`. An untitled one is named from its
        first message. `temporary=True` asks the server not to keep the
        conversation: it appears in no list (under any filter) or either
        search, cannot be pinned, shared, forked, filed or tagged -- each
        a 409 saying a temporary conversation is not kept -- and it is
        hard-deleted 24 hours after its last message. The choice is fixed
        at creation; nothing later turns it on or off."""
        body, rid = self._object(_base.create_conversation_request(title, model, temporary))
        return Conversation.from_dict(body, request_id=rid)

    def get(self, conversation_id: str, *, full: bool = False) -> Conversation:
        """The conversation and its active path; `full=True` adds `tree`."""
        body, rid = self._object(_base.get_conversation_request(conversation_id, full))
        return Conversation.from_dict(body, request_id=rid)

    def update(
        self,
        conversation_id: str,
        *,
        title: str | None = None,
        default_model: str | None = _base.UNSET,
    ) -> Conversation:
        """`PATCH /api/conversations/{id}`: rename the conversation, set the
        model its turns use when a turn names none, or both.

        `default_model=None` clears the model back to the deployment's
        default; leaving the keyword out leaves it alone. `title` is the
        person's name for the conversation, 1-200 characters once trimmed.
        """
        body, rid = self._object(
            _base.update_conversation_request(
                conversation_id, title=title, default_model=default_model
            )
        )
        return Conversation.from_dict(body, request_id=rid)

    def tree(self, conversation_id: str) -> Conversation:
        """Every message, branches included, in creation order, as `messages`."""
        body, rid = self._object(_base.conversation_tree_request(conversation_id))
        return Conversation.from_dict(body, request_id=rid)

    def set_active_leaf(self, conversation_id: str, message_id: str) -> Conversation:
        """Show the branch ending at `message_id`; returns the new path."""
        body, rid = self._object(_base.set_active_leaf_request(conversation_id, message_id))
        return Conversation.from_dict(body, request_id=rid)

    def fork(
        self, conversation_id: str, message_id: str, *, title: str | None = None
    ) -> Conversation:
        """Copy the path down to `message_id` into a new conversation.
        `ConflictError` for a temporary conversation: a temporary one is
        not kept, so it cannot be forked."""
        body, rid = self._object(
            _base.fork_conversation_request(conversation_id, message_id, title)
        )
        return Conversation.from_dict(body, request_id=rid)

    def context(self, conversation_id: str) -> ConversationContext:
        """How much of the model's context window the conversation uses."""
        body, rid = self._object(_base.conversation_context_request(conversation_id))
        return ConversationContext.from_dict(body, request_id=rid)

    def compact(self, conversation_id: str) -> ConversationMessage:
        """Fold the earlier messages into a summary now, without a turn.
        Returns the summary message; `ConflictError` when there is nothing
        to fold yet."""
        body, rid = self._object(_base.compact_conversation_request(conversation_id))
        return ConversationMessage.from_dict(body, request_id=rid)

    def delete(self, conversation_id: str) -> DeletedConversation:
        """`DELETE /api/conversations/{id}`: gone from every list, its
        attachments marked for the sweeper. The row itself is purged after
        the platform's 30-day delay."""
        body, rid = self._object(_base.delete_conversation_request(conversation_id))
        return DeletedConversation.from_dict(body, request_id=rid)

    def report(
        self,
        conversation_id: str,
        kind: str,
        *,
        message_id: str | None = None,
        note: str | None = None,
    ) -> FiledReport:
        """`POST /api/conversations/{id}/report`: report the conversation,
        or one message of it (`message_id`), to the reviewers. `kind` is
        `unsafe` (routes to human review) or `unhelpful` (the product
        backlog); `note` is the reporter's own words, up to 2000
        characters, stored and never logged. The report is a legal record,
        not a UX affordance: a 201 means it is stored, and the answer's
        `id` is what support asks for; a 503 means it was not filed."""
        if kind not in REPORT_KINDS:
            raise ValueError(f"kind must be one of {', '.join(REPORT_KINDS)}, got {kind!r}")
        if note is not None and len(note) > 2000:
            raise ValueError("note must be 2000 characters or fewer")
        body, rid = self._object(
            _base.report_conversation_request(conversation_id, kind, message_id, note)
        )
        return FiledReport.from_dict(body, request_id=rid)

    def pin(self, conversation_id: str) -> Conversation:
        """`PUT /api/conversations/{id}/pin`: to the top of the sidebar,
        first in the `pinned` filter. `ConflictError` for a temporary
        conversation: a temporary one is not kept, so it cannot be
        pinned."""
        body, rid = self._object(_base.set_pinned_request(conversation_id, True))
        return Conversation.from_dict(body, request_id=rid)

    def unpin(self, conversation_id: str) -> Conversation:
        """`DELETE /api/conversations/{id}/pin`."""
        body, rid = self._object(_base.set_pinned_request(conversation_id, False))
        return Conversation.from_dict(body, request_id=rid)

    def archive(self, conversation_id: str) -> Conversation:
        """`PUT /api/conversations/{id}/archive`: out of the default list,
        still readable and searchable, under `filter="archived"`."""
        body, rid = self._object(_base.set_archived_request(conversation_id, True))
        return Conversation.from_dict(body, request_id=rid)

    def unarchive(self, conversation_id: str) -> Conversation:
        """`DELETE /api/conversations/{id}/archive`: back to the default list."""
        body, rid = self._object(_base.set_archived_request(conversation_id, False))
        return Conversation.from_dict(body, request_id=rid)

    def update_instructions(
        self, conversation_id: str, text: str | None
    ) -> ConversationInstructions:
        """`PUT /api/conversations/{id}/instructions`: this thread's own
        prompt, which replaces the user's instructions for it (the org's
        still apply). None, or an empty string, clears it. The answer
        carries the levels in force, as `get` reports them."""
        body, rid = self._object(
            _base.conversation_instructions_request(conversation_id, text)
        )
        return ConversationInstructions.from_dict(body, request_id=rid)

    def _stream(self, request: Request, conversation_id: str) -> ConversationStream:
        return ConversationStream(
            self._client, request, conversation_id, self._client._send(request)
        )

    def send(
        self,
        conversation_id: str,
        content: str,
        *,
        model: str | None = None,
        temperature: float | None = None,
        attachment_ids: Sequence[str] | None = None,
        parent_id: str | None = None,
    ) -> ConversationStream:
        """Append a user message and stream the reply. The server builds the
        prompt from the stored history and instructions; `parent_id` sends
        onto that branch instead of the current leaf."""
        request = _base.send_message_request(
            conversation_id,
            content,
            model=model,
            temperature=temperature,
            attachment_ids=attachment_ids,
            parent_id=parent_id,
        )
        return self._stream(request, conversation_id)

    def edit(
        self,
        conversation_id: str,
        message_id: str,
        content: str,
        *,
        model: str | None = None,
        temperature: float | None = None,
    ) -> ConversationStream:
        """Edit a user message as a new branch beside it, and stream the answer."""
        request = _base.edit_message_request(
            conversation_id, message_id, content, model=model, temperature=temperature
        )
        return self._stream(request, conversation_id)

    def regenerate(
        self,
        conversation_id: str,
        message_id: str,
        *,
        model: str | None = None,
        temperature: float | None = None,
    ) -> ConversationStream:
        """Answer an assistant message's turn again, as a sibling of it."""
        request = _base.regenerate_message_request(
            conversation_id, message_id, model=model, temperature=temperature
        )
        return self._stream(request, conversation_id)


class Messages:
    """The caller's own ratings of replies. One rating per person per
    message: a second `set_feedback` replaces the first rather than piling
    up, which is why setting is a PUT."""

    def __init__(self, client: MatterWave) -> None:
        self._client = client

    def _object(self, request: Request) -> tuple[dict[str, Any], str | None]:
        response = self._client._send(request)
        return _base.parse_json_object(response), _base.request_id_of(response)

    def set_feedback(
        self,
        message_id: str,
        rating: int,
        *,
        reasons: Sequence[str] = (),
        comment: str | None = None,
    ) -> MessageFeedback:
        """`PUT /api/messages/{id}/feedback`: rate a reply, up (`1`) or down
        (`-1`), with `reasons` from `FEEDBACK_REASONS` and a free-text
        `comment`. Only an assistant message of the caller's own may be
        rated; anything else is a 404 or a 422."""
        if rating not in (-1, 1):
            raise ValueError(f"rating must be 1 or -1, got {rating!r}")
        unknown = [r for r in reasons if r not in FEEDBACK_REASONS]
        if unknown:
            raise ValueError(
                f"reasons must be from {', '.join(FEEDBACK_REASONS)}, got {', '.join(unknown)}"
            )
        body, rid = self._object(
            _base.set_feedback_request(message_id, rating, reasons, comment)
        )
        return MessageFeedback.from_dict(body, request_id=rid)

    def delete_feedback(self, message_id: str) -> RemovedFeedback:
        """`DELETE /api/messages/{id}/feedback`: withdraw a rating.
        Idempotent -- `none` when there was already nothing to remove."""
        body, rid = self._object(_base.delete_feedback_request(message_id))
        return RemovedFeedback.from_dict(body, request_id=rid)

    def list_feedback(self, conversation_id: str) -> MessageFeedbackList:
        """`GET /api/conversations/{id}/feedback`: the caller's own ratings
        in this conversation, in creation order, so the thumbs can be
        redrawn on reload. Nobody else's rows, and no aggregates."""
        body, rid = self._object(_base.list_feedback_request(conversation_id))
        return MessageFeedbackList.from_dict(body, request_id=rid)


class Instructions:
    """Custom instructions, the three levels the platform keeps. The org
    level is additive policy every member is subject to; the conversation
    level replaces the user's for that thread (`conversations.update_
    instructions` is the same idea, one thread at a time). Reads of the org
    level are admin-only, as are writes."""

    def __init__(self, client: MatterWave) -> None:
        self._client = client

    def _object(self, request: Request) -> tuple[dict[str, Any], str | None]:
        response = self._client._send(request)
        return _base.parse_json_object(response), _base.request_id_of(response)

    def user(self) -> UserInstructions:
        """`GET /api/me/instructions`: what the caller saved about
        themselves. Reading is not gated, so a downgraded plan can still
        see and clear what it saved."""
        body, rid = self._object(_base.user_instructions_get_request())
        return UserInstructions.from_dict(body, request_id=rid)

    def update_user(self, *, about: str = "", style: str = "") -> UserInstructions:
        """`PUT /api/me/instructions`: save both fields. The route replaces
        the pair, so a field left out is sent as empty -- there is no
        "leave it alone".
        """
        body, rid = self._object(_base.user_instructions_request(about, style))
        return UserInstructions.from_dict(body, request_id=rid)

    def org(self) -> OrgInstructions:
        """`GET /api/org/instructions`: the org's policy text.
        `PermissionDeniedError` for anyone but an owner or admin."""
        body, rid = self._object(_base.org_instructions_get_request())
        return OrgInstructions.from_dict(body, request_id=rid)

    def update_org(self, text: str) -> OrgInstructions:
        """`PUT /api/org/instructions`: set (or with "", clear) the policy
        every member's turns are prefixed with. Owner or admin only."""
        body, rid = self._object(_base.org_instructions_request(text))
        return OrgInstructions.from_dict(body, request_id=rid)


def _login_limit(code: DeviceCode, timeout: float | None) -> tuple[float, bool]:
    """How long to keep polling, and whether the caller's timeout (rather
    than the code's own expiry) is what bounds it."""
    if timeout is not None and timeout < code.expires_in:
        return timeout, True
    return float(code.expires_in), False


def _login_expired(timed_out: bool, timeout: float | None) -> MatterWaveError:
    if timed_out:
        return APITimeoutError(f"The device login was not approved within {timeout}s.")
    return DeviceAuthError(
        "The device code expired before it was approved.", status_code=None, code="expired_token"
    )


class DeviceAuth:
    """Sign a person in from a device with no browser of its own (RFC 8628):
    show them a code, they approve it in any browser, and the poll returns
    an API key that acts as them. Nothing is written to disk here."""

    def __init__(self, client: MatterWave) -> None:
        self._client = client

    def start(self, *, client_name: str | None = None) -> DeviceCode:
        """`POST /api/auth/device`: a code to show and the URL to approve it at."""
        response = self._client._send(_base.device_start_request(client_name))
        return DeviceCode.from_dict(
            _base.parse_json_object(response), request_id=_base.request_id_of(response)
        )

    def poll(self, device_code: str) -> DeviceToken:
        """`POST /api/auth/device/token`, once. The token when approved;
        `DeviceAuthError` with the RFC's `code` while it is not."""
        try:
            response = self._client._send(_base.device_token_request(device_code))
        except APIError as err:
            device = _base.device_error_of(err)
            if device is not None:
                raise device from None
            raise
        return DeviceToken.from_dict(
            _base.parse_json_object(response), request_id=_base.request_id_of(response)
        )

    def login(
        self,
        *,
        on_code: Callable[[DeviceCode], object],
        timeout: float | None = None,
        client_name: str | None = None,
    ) -> DeviceToken:
        """Start, hand the code to `on_code` to show, and poll at the
        server's interval (longer after each `slow_down`) until approved.
        Raises `DeviceAuthError` when denied or expired, `APITimeoutError`
        when `timeout` seconds pass first."""
        code = self.start(client_name=client_name)
        on_code(code)
        interval = max(1, code.interval)
        limit, timed_out = _login_limit(code, timeout)
        deadline = time.monotonic() + limit
        while True:
            if time.monotonic() + interval > deadline:
                raise _login_expired(timed_out, timeout)
            time.sleep(interval)
            try:
                return self.poll(code.device_code)
            except DeviceAuthError as err:
                if err.code == "slow_down":
                    interval += _base.SLOW_DOWN_STEP_S
                elif err.code != "authorization_pending":
                    raise


class Auth:
    def __init__(self, client: MatterWave) -> None:
        self.device = DeviceAuth(client)


class MatterWave:
    """The blocking client. `api_key` defaults to `MATTERWAVE_API_KEY`,
    `base_url` to `MATTERWAVE_BASE_URL` (the origin, with or without `/v1`).
    Pass `http_client` to share a connection pool or a mock transport."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: float | httpx.Timeout | None = None,
        max_retries: int = _base.DEFAULT_MAX_RETRIES,
        default_headers: Mapping[str, str] | None = None,
        http_client: httpx.Client | None = None,
    ) -> None:
        self._config = _Config(api_key, base_url, timeout, max_retries, default_headers)
        self._owns_http = http_client is None
        self._http = http_client or httpx.Client()
        self.chat = Chat(self)
        self.models = Models(self)
        self.embeddings = Embeddings(self)
        self.conversations = Conversations(self)
        self.messages = Messages(self)
        self.instructions = Instructions(self)
        self.auth = Auth(self)
        self.keys = Keys(self)
        self.usage = UsageClient(self)
        self.webhooks = Webhooks(self)
        self.attachments = Attachments(self)
        self.library = LibraryClient(self)
        self.shares = Shares(self)
        self.budget = BudgetClient(self)
        self.notifications = Notifications(self)
        self.announcements = Announcements(self)
        self.entitlements = EntitlementsClient(self)
        self.admin = Admin(self)
        self.mcp = Mcp(self)

    @property
    def base_url(self) -> str:
        return self._config.base_url

    def index(self) -> ApiIndex:
        """`GET /v1`: the version, the endpoints and the stability promise. No key needed."""
        response = self._send(_base.index_request())
        return ApiIndex.from_dict(
            _base.parse_json_object(response), request_id=_base.request_id_of(response)
        )

    def me(self) -> Me:
        """`GET /api/me`: who the key (or device-login token) acts as."""
        response = self._send(_base.me_request())
        return Me.from_dict(
            _base.parse_json_object(response), request_id=_base.request_id_of(response)
        )

    def app_models(self) -> AppModelList:
        """`GET /api/models`: the model picker's rows -- each model priced,
        with `included` saying whether the caller's plan covers it and
        `upgrade_to` the plan that does, so a client can show the upgrade
        path instead of hiding the model. `models.list()` is `/v1/models`,
        which carries none of that."""
        response = self._send(_base.app_models_request())
        return AppModelList.from_dict(
            _base.parse_json_object(response), request_id=_base.request_id_of(response)
        )

    def close(self) -> None:
        if self._owns_http:
            self._http.close()

    def __enter__(self) -> MatterWave:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def _send(self, request: Request) -> httpx.Response:
        """One operation, with the retry loop. A streamed response is handed
        back unread; a buffered one is read here so the body is available
        for the retry decision and the error."""
        attempt = 0
        while True:
            http_request = self._config.build(request, self._http)
            try:
                response = self._http.send(http_request, stream=request.stream)
            except httpx.HTTPError as exc:
                if attempt >= self._config.max_retries:
                    raise _connection_error(exc, request) from exc
                time.sleep(_base.backoff_s(attempt, None))
                attempt += 1
                continue
            if response.is_success:
                return response
            body = response.read()
            if attempt < self._config.max_retries and _base.should_retry(response, body):
                response.close()
                time.sleep(_base.backoff_s(attempt, _base.retry_after_of(response)))
                attempt += 1
                continue
            response.close()
            raise _base.error_from_response(response, body)


# --- the async client --------------------------------------------------------


class AsyncCompletions:
    def __init__(self, client: AsyncMatterWave) -> None:
        self._client = client

    @overload
    async def create(
        self,
        messages: Sequence[Message],
        *,
        model: str | None = None,
        stream: Literal[False] = False,
        include_usage: bool | None = None,
        max_tokens: int | None = None,
        max_completion_tokens: int | None = None,
        temperature: float | None = None,
        top_p: float | None = None,
        stop: str | Sequence[str] | None = None,
        n: int | None = None,
        user: str | None = None,
        tools: Sequence[Tool] | None = None,
        tool_choice: ToolChoice | None = None,
        response_format: dict[str, Any] | None = None,
        conversation_id: str | None = None,
        extra_body: dict[str, Any] | None = None,
    ) -> ChatCompletion: ...

    @overload
    async def create(
        self,
        messages: Sequence[Message],
        *,
        model: str | None = None,
        stream: Literal[True],
        include_usage: bool | None = None,
        max_tokens: int | None = None,
        max_completion_tokens: int | None = None,
        temperature: float | None = None,
        top_p: float | None = None,
        stop: str | Sequence[str] | None = None,
        n: int | None = None,
        user: str | None = None,
        tools: Sequence[Tool] | None = None,
        tool_choice: ToolChoice | None = None,
        response_format: dict[str, Any] | None = None,
        conversation_id: str | None = None,
        extra_body: dict[str, Any] | None = None,
    ) -> AsyncStream: ...

    async def create(
        self,
        messages: Sequence[Message],
        *,
        model: str | None = None,
        stream: bool = False,
        include_usage: bool | None = None,
        max_tokens: int | None = None,
        max_completion_tokens: int | None = None,
        temperature: float | None = None,
        top_p: float | None = None,
        stop: str | Sequence[str] | None = None,
        n: int | None = None,
        user: str | None = None,
        tools: Sequence[Tool] | None = None,
        tool_choice: ToolChoice | None = None,
        response_format: dict[str, Any] | None = None,
        conversation_id: str | None = None,
        extra_body: dict[str, Any] | None = None,
    ) -> ChatCompletion | AsyncStream:
        body = _base.chat_completion_body(
            messages,
            model=model,
            stream=stream,
            include_usage=include_usage,
            max_tokens=max_tokens,
            max_completion_tokens=max_completion_tokens,
            temperature=temperature,
            top_p=top_p,
            stop=stop,
            n=n,
            user=user,
            tools=tools,
            tool_choice=tool_choice,
            response_format=response_format,
            conversation_id=conversation_id,
            extra_body=extra_body,
        )
        request = _base.chat_completions_request(body)
        if stream:
            return AsyncStream(await self._client._send(request))
        response = await self._client._send(request)
        return ChatCompletion.from_dict(
            _base.parse_json_object(response), request_id=_base.request_id_of(response)
        )


class AsyncChat:
    def __init__(self, client: AsyncMatterWave) -> None:
        self.completions = AsyncCompletions(client)


class AsyncEmbeddings:
    def __init__(self, client: AsyncMatterWave) -> None:
        self._client = client

    async def create(
        self,
        input: EmbeddingInput,
        *,
        model: str | None = None,
        encoding_format: Literal["float", "base64"] | None = None,
        dimensions: int | None = None,
        user: str | None = None,
        extra_body: dict[str, Any] | None = None,
    ) -> EmbeddingList:
        body = _base.embeddings_body(
            input,
            model=model,
            encoding_format=encoding_format,
            dimensions=dimensions,
            user=user,
            extra_body=extra_body,
        )
        response = await self._client._send(_base.embeddings_request(body))
        return EmbeddingList.from_dict(
            _base.parse_json_object(response), request_id=_base.request_id_of(response)
        )


class AsyncModels:
    def __init__(self, client: AsyncMatterWave) -> None:
        self._client = client

    async def list(self) -> ModelList:
        response = await self._client._send(_base.list_models_request())
        return ModelList.from_dict(
            _base.parse_json_object(response), request_id=_base.request_id_of(response)
        )

    async def retrieve(self, model_id: str) -> Model:
        response = await self._client._send(_base.retrieve_model_request(model_id))
        return Model.from_dict(
            _base.parse_json_object(response), request_id=_base.request_id_of(response)
        )


class AsyncConversations:
    def __init__(self, client: AsyncMatterWave) -> None:
        self._client = client

    async def _object(self, request: Request) -> tuple[dict[str, Any], str | None]:
        response = await self._client._send(request)
        return _base.parse_json_object(response), _base.request_id_of(response)

    async def list(
        self, *, filter: str | None = None, limit: int | None = None, cursor: str | None = None
    ) -> ConversationList:
        body, rid = await self._object(_base.list_conversations_request(filter, limit, cursor))
        return ConversationList.from_dict(body, request_id=rid)

    async def pages(
        self, *, filter: str | None = None, limit: int | None = None
    ) -> AsyncIterator[ConversationList]:
        cursor: str | None = None
        while True:
            page = await self.list(filter=filter, limit=limit, cursor=cursor)
            yield page
            cursor = page.next_cursor
            if cursor is None:
                return

    async def search(self, q: str, *, limit: int | None = None) -> SearchResults:
        body, rid = await self._object(_base.search_conversations_request(q, limit))
        return SearchResults.from_dict(body, request_id=rid)

    async def create(
        self,
        *,
        title: str | None = None,
        model: str | None = None,
        temporary: bool | None = None,
    ) -> Conversation:
        body, rid = await self._object(_base.create_conversation_request(title, model, temporary))
        return Conversation.from_dict(body, request_id=rid)

    async def get(self, conversation_id: str, *, full: bool = False) -> Conversation:
        body, rid = await self._object(_base.get_conversation_request(conversation_id, full))
        return Conversation.from_dict(body, request_id=rid)

    async def update(
        self,
        conversation_id: str,
        *,
        title: str | None = None,
        default_model: str | None = _base.UNSET,
    ) -> Conversation:
        body, rid = await self._object(
            _base.update_conversation_request(
                conversation_id, title=title, default_model=default_model
            )
        )
        return Conversation.from_dict(body, request_id=rid)

    async def tree(self, conversation_id: str) -> Conversation:
        body, rid = await self._object(_base.conversation_tree_request(conversation_id))
        return Conversation.from_dict(body, request_id=rid)

    async def set_active_leaf(self, conversation_id: str, message_id: str) -> Conversation:
        body, rid = await self._object(_base.set_active_leaf_request(conversation_id, message_id))
        return Conversation.from_dict(body, request_id=rid)

    async def fork(
        self, conversation_id: str, message_id: str, *, title: str | None = None
    ) -> Conversation:
        body, rid = await self._object(
            _base.fork_conversation_request(conversation_id, message_id, title)
        )
        return Conversation.from_dict(body, request_id=rid)

    async def context(self, conversation_id: str) -> ConversationContext:
        body, rid = await self._object(_base.conversation_context_request(conversation_id))
        return ConversationContext.from_dict(body, request_id=rid)

    async def compact(self, conversation_id: str) -> ConversationMessage:
        body, rid = await self._object(_base.compact_conversation_request(conversation_id))
        return ConversationMessage.from_dict(body, request_id=rid)

    async def delete(self, conversation_id: str) -> DeletedConversation:
        body, rid = await self._object(_base.delete_conversation_request(conversation_id))
        return DeletedConversation.from_dict(body, request_id=rid)

    async def report(
        self,
        conversation_id: str,
        kind: str,
        *,
        message_id: str | None = None,
        note: str | None = None,
    ) -> FiledReport:
        if kind not in REPORT_KINDS:
            raise ValueError(f"kind must be one of {', '.join(REPORT_KINDS)}, got {kind!r}")
        if note is not None and len(note) > 2000:
            raise ValueError("note must be 2000 characters or fewer")
        body, rid = await self._object(
            _base.report_conversation_request(conversation_id, kind, message_id, note)
        )
        return FiledReport.from_dict(body, request_id=rid)

    async def pin(self, conversation_id: str) -> Conversation:
        body, rid = await self._object(_base.set_pinned_request(conversation_id, True))
        return Conversation.from_dict(body, request_id=rid)

    async def unpin(self, conversation_id: str) -> Conversation:
        body, rid = await self._object(_base.set_pinned_request(conversation_id, False))
        return Conversation.from_dict(body, request_id=rid)

    async def archive(self, conversation_id: str) -> Conversation:
        body, rid = await self._object(_base.set_archived_request(conversation_id, True))
        return Conversation.from_dict(body, request_id=rid)

    async def unarchive(self, conversation_id: str) -> Conversation:
        body, rid = await self._object(_base.set_archived_request(conversation_id, False))
        return Conversation.from_dict(body, request_id=rid)

    async def update_instructions(
        self, conversation_id: str, text: str | None
    ) -> ConversationInstructions:
        body, rid = await self._object(
            _base.conversation_instructions_request(conversation_id, text)
        )
        return ConversationInstructions.from_dict(body, request_id=rid)

    async def _stream(self, request: Request, conversation_id: str) -> AsyncConversationStream:
        response = await self._client._send(request)
        return AsyncConversationStream(self._client, request, conversation_id, response)

    async def send(
        self,
        conversation_id: str,
        content: str,
        *,
        model: str | None = None,
        temperature: float | None = None,
        attachment_ids: Sequence[str] | None = None,
        parent_id: str | None = None,
    ) -> AsyncConversationStream:
        request = _base.send_message_request(
            conversation_id,
            content,
            model=model,
            temperature=temperature,
            attachment_ids=attachment_ids,
            parent_id=parent_id,
        )
        return await self._stream(request, conversation_id)

    async def edit(
        self,
        conversation_id: str,
        message_id: str,
        content: str,
        *,
        model: str | None = None,
        temperature: float | None = None,
    ) -> AsyncConversationStream:
        request = _base.edit_message_request(
            conversation_id, message_id, content, model=model, temperature=temperature
        )
        return await self._stream(request, conversation_id)

    async def regenerate(
        self,
        conversation_id: str,
        message_id: str,
        *,
        model: str | None = None,
        temperature: float | None = None,
    ) -> AsyncConversationStream:
        request = _base.regenerate_message_request(
            conversation_id, message_id, model=model, temperature=temperature
        )
        return await self._stream(request, conversation_id)


class AsyncMessages:
    def __init__(self, client: AsyncMatterWave) -> None:
        self._client = client

    async def _object(self, request: Request) -> tuple[dict[str, Any], str | None]:
        response = await self._client._send(request)
        return _base.parse_json_object(response), _base.request_id_of(response)

    async def set_feedback(
        self,
        message_id: str,
        rating: int,
        *,
        reasons: Sequence[str] = (),
        comment: str | None = None,
    ) -> MessageFeedback:
        if rating not in (-1, 1):
            raise ValueError(f"rating must be 1 or -1, got {rating!r}")
        unknown = [r for r in reasons if r not in FEEDBACK_REASONS]
        if unknown:
            raise ValueError(
                f"reasons must be from {', '.join(FEEDBACK_REASONS)}, got {', '.join(unknown)}"
            )
        body, rid = await self._object(
            _base.set_feedback_request(message_id, rating, reasons, comment)
        )
        return MessageFeedback.from_dict(body, request_id=rid)

    async def delete_feedback(self, message_id: str) -> RemovedFeedback:
        body, rid = await self._object(_base.delete_feedback_request(message_id))
        return RemovedFeedback.from_dict(body, request_id=rid)

    async def list_feedback(self, conversation_id: str) -> MessageFeedbackList:
        body, rid = await self._object(_base.list_feedback_request(conversation_id))
        return MessageFeedbackList.from_dict(body, request_id=rid)


class AsyncInstructions:
    def __init__(self, client: AsyncMatterWave) -> None:
        self._client = client

    async def _object(self, request: Request) -> tuple[dict[str, Any], str | None]:
        response = await self._client._send(request)
        return _base.parse_json_object(response), _base.request_id_of(response)

    async def user(self) -> UserInstructions:
        body, rid = await self._object(_base.user_instructions_get_request())
        return UserInstructions.from_dict(body, request_id=rid)

    async def update_user(self, *, about: str = "", style: str = "") -> UserInstructions:
        body, rid = await self._object(_base.user_instructions_request(about, style))
        return UserInstructions.from_dict(body, request_id=rid)

    async def org(self) -> OrgInstructions:
        body, rid = await self._object(_base.org_instructions_get_request())
        return OrgInstructions.from_dict(body, request_id=rid)

    async def update_org(self, text: str) -> OrgInstructions:
        body, rid = await self._object(_base.org_instructions_request(text))
        return OrgInstructions.from_dict(body, request_id=rid)


class AsyncDeviceAuth:
    def __init__(self, client: AsyncMatterWave) -> None:
        self._client = client

    async def start(self, *, client_name: str | None = None) -> DeviceCode:
        response = await self._client._send(_base.device_start_request(client_name))
        return DeviceCode.from_dict(
            _base.parse_json_object(response), request_id=_base.request_id_of(response)
        )

    async def poll(self, device_code: str) -> DeviceToken:
        try:
            response = await self._client._send(_base.device_token_request(device_code))
        except APIError as err:
            device = _base.device_error_of(err)
            if device is not None:
                raise device from None
            raise
        return DeviceToken.from_dict(
            _base.parse_json_object(response), request_id=_base.request_id_of(response)
        )

    async def login(
        self,
        *,
        on_code: Callable[[DeviceCode], Awaitable[object] | object],
        timeout: float | None = None,
        client_name: str | None = None,
    ) -> DeviceToken:
        """As `DeviceAuth.login`; `on_code` may be a coroutine function."""
        code = await self.start(client_name=client_name)
        shown = on_code(code)
        if inspect.isawaitable(shown):
            await shown
        interval = max(1, code.interval)
        limit, timed_out = _login_limit(code, timeout)
        deadline = time.monotonic() + limit
        while True:
            if time.monotonic() + interval > deadline:
                raise _login_expired(timed_out, timeout)
            await asyncio.sleep(interval)
            try:
                return await self.poll(code.device_code)
            except DeviceAuthError as err:
                if err.code == "slow_down":
                    interval += _base.SLOW_DOWN_STEP_S
                elif err.code != "authorization_pending":
                    raise


class AsyncAuth:
    def __init__(self, client: AsyncMatterWave) -> None:
        self.device = AsyncDeviceAuth(client)


class AsyncMatterWave:
    """The awaitable client; the same surface as `MatterWave`, every
    operation a coroutine and the stream an async iterator."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: float | httpx.Timeout | None = None,
        max_retries: int = _base.DEFAULT_MAX_RETRIES,
        default_headers: Mapping[str, str] | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._config = _Config(api_key, base_url, timeout, max_retries, default_headers)
        self._owns_http = http_client is None
        self._http = http_client or httpx.AsyncClient()
        self.chat = AsyncChat(self)
        self.models = AsyncModels(self)
        self.embeddings = AsyncEmbeddings(self)
        self.conversations = AsyncConversations(self)
        self.messages = AsyncMessages(self)
        self.instructions = AsyncInstructions(self)
        self.auth = AsyncAuth(self)
        self.keys = AsyncKeys(self)
        self.usage = AsyncUsageClient(self)
        self.webhooks = AsyncWebhooks(self)
        self.attachments = AsyncAttachments(self)
        self.library = AsyncLibraryClient(self)
        self.shares = AsyncShares(self)
        self.budget = AsyncBudgetClient(self)
        self.notifications = AsyncNotifications(self)
        self.announcements = AsyncAnnouncements(self)
        self.entitlements = AsyncEntitlementsClient(self)
        self.admin = AsyncAdmin(self)
        self.mcp = AsyncMcp(self)

    @property
    def base_url(self) -> str:
        return self._config.base_url

    async def index(self) -> ApiIndex:
        response = await self._send(_base.index_request())
        return ApiIndex.from_dict(
            _base.parse_json_object(response), request_id=_base.request_id_of(response)
        )

    async def me(self) -> Me:
        response = await self._send(_base.me_request())
        return Me.from_dict(
            _base.parse_json_object(response), request_id=_base.request_id_of(response)
        )

    async def app_models(self) -> AppModelList:
        response = await self._send(_base.app_models_request())
        return AppModelList.from_dict(
            _base.parse_json_object(response), request_id=_base.request_id_of(response)
        )

    async def close(self) -> None:
        if self._owns_http:
            await self._http.aclose()

    async def __aenter__(self) -> AsyncMatterWave:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.close()

    async def _send(self, request: Request) -> httpx.Response:
        attempt = 0
        while True:
            http_request = self._config.build(request, self._http)
            try:
                response = await self._http.send(http_request, stream=request.stream)
            except httpx.HTTPError as exc:
                if attempt >= self._config.max_retries:
                    raise _connection_error(exc, request) from exc
                await asyncio.sleep(_base.backoff_s(attempt, None))
                attempt += 1
                continue
            if response.is_success:
                return response
            body = await response.aread()
            if attempt < self._config.max_retries and _base.should_retry(response, body):
                await response.aclose()
                await asyncio.sleep(_base.backoff_s(attempt, _base.retry_after_of(response)))
                attempt += 1
                continue
            await response.aclose()
            raise _base.error_from_response(response, body)
