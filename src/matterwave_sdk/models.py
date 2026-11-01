"""The response types, as plain dataclasses.

Each is built from the JSON the API sends and keeps only the fields the
contract names. Unknown fields are dropped rather than refused: the `/v1`
promise is that additions ship without notice and clients tolerate them,
and a client that broke on a new field would be breaking that promise from
its side.

`request_id` on each top-level object is the `X-Request-Id` the response
carried. It is not in the wire body; it is here so the id is at hand when a
successful call needs to be found in the server's logs.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from ._base import Message


def _int(value: Any, default: int = 0) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else default


def _str(value: Any, default: str = "") -> str:
    return value if isinstance(value, str) else default


@dataclass(frozen=True)
class Usage:
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Usage:
        return cls(
            prompt_tokens=_int(data.get("prompt_tokens")),
            completion_tokens=_int(data.get("completion_tokens")),
            total_tokens=_int(data.get("total_tokens")),
        )


@dataclass(frozen=True)
class FunctionCall:
    name: str
    arguments: str

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FunctionCall:
        return cls(name=_str(data.get("name")), arguments=_str(data.get("arguments")))


@dataclass(frozen=True)
class ToolCall:
    """A call the model asked the caller to make. Run it, then send the result
    back as a `tool` message carrying this `id` as `tool_call_id`."""

    id: str
    function: FunctionCall
    type: str = "function"

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ToolCall:
        function = data.get("function")
        return cls(
            id=_str(data.get("id")),
            type=_str(data.get("type"), "function"),
            function=FunctionCall.from_dict(function if isinstance(function, dict) else {}),
        )

    def to_dict(self) -> dict[str, Any]:
        """The call as the wire spells it, for echoing back in the assistant
        message that precedes its `tool` answer."""
        return {
            "id": self.id,
            "type": self.type,
            "function": {"name": self.function.name, "arguments": self.function.arguments},
        }


@dataclass(frozen=True)
class AssistantMessage:
    """`content` is None on a pure tool-call turn, as OpenAI shapes it."""

    role: str
    content: str | None
    tool_calls: list[ToolCall] | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AssistantMessage:
        content = data.get("content")
        calls = data.get("tool_calls")
        return cls(
            role=_str(data.get("role"), "assistant"),
            content=content if isinstance(content, str) else None,
            tool_calls=(
                [ToolCall.from_dict(c) for c in calls if isinstance(c, dict)]
                if isinstance(calls, list)
                else None
            ),
        )

    def to_message(self) -> Message:
        """This message as the next request's prompt takes it: append it to
        `messages`, then one `tool` message per call (`tool_message`), and
        send the lot to continue the loop. `tool_calls` is left out when
        there are none, as OpenAI shapes a plain answer."""
        message: Message = {"role": "assistant", "content": self.content}
        if self.tool_calls:
            message["tool_calls"] = [call.to_dict() for call in self.tool_calls]
        return message


def tool_message(tool_call_id: str, content: str) -> Message:
    """The `tool` message answering one call: its `id` as `tool_call_id`
    and the result as text. A failed call is answered too, with the error
    as `content`, so the model sees why rather than a gap."""
    if not tool_call_id:
        raise ValueError("tool_call_id must be the id of the call this answers")
    return {"role": "tool", "tool_call_id": tool_call_id, "content": content}


@dataclass(frozen=True)
class Choice:
    index: int
    message: AssistantMessage
    finish_reason: str | None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Choice:
        message = data.get("message")
        return cls(
            index=_int(data.get("index")),
            message=AssistantMessage.from_dict(message if isinstance(message, dict) else {}),
            finish_reason=data.get("finish_reason"),
        )


@dataclass(frozen=True)
class ChatCompletion:
    id: str
    created: int
    model: str
    choices: list[Choice]
    usage: Usage
    object: str = "chat.completion"
    request_id: str | None = field(default=None, compare=False)

    @property
    def content(self) -> str:
        """The first choice's text ("" for a pure tool-call turn); the API serves one choice."""
        return (self.choices[0].message.content or "") if self.choices else ""

    @property
    def tool_calls(self) -> list[ToolCall]:
        """The calls the model asked for, or an empty list."""
        return (self.choices[0].message.tool_calls or []) if self.choices else []

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, request_id: str | None = None) -> ChatCompletion:
        usage = data.get("usage")
        return cls(
            id=_str(data.get("id")),
            object=_str(data.get("object"), "chat.completion"),
            created=_int(data.get("created")),
            model=_str(data.get("model")),
            choices=[Choice.from_dict(c) for c in data.get("choices", []) if isinstance(c, dict)],
            usage=Usage.from_dict(usage if isinstance(usage, dict) else {}),
            request_id=request_id,
        )


@dataclass(frozen=True)
class FunctionCallFragment:
    name: str | None = None
    arguments: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FunctionCallFragment:
        name = data.get("name")
        arguments = data.get("arguments")
        return cls(
            name=name if isinstance(name, str) else None,
            arguments=arguments if isinstance(arguments, str) else None,
        )


@dataclass(frozen=True)
class ToolCallFragment:
    """One streamed piece of a tool call: the first for an `index` carries
    `id` and the name, the rest carry `arguments` to concatenate in order."""

    index: int
    function: FunctionCallFragment
    id: str | None = None
    type: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ToolCallFragment:
        function = data.get("function")
        call_id = data.get("id")
        call_type = data.get("type")
        return cls(
            index=_int(data.get("index")),
            id=call_id if isinstance(call_id, str) else None,
            type=call_type if isinstance(call_type, str) else None,
            function=FunctionCallFragment.from_dict(function if isinstance(function, dict) else {}),
        )


@dataclass(frozen=True)
class ChunkDelta:
    role: str | None = None
    content: str | None = None
    tool_calls: list[ToolCallFragment] | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ChunkDelta:
        role = data.get("role")
        content = data.get("content")
        calls = data.get("tool_calls")
        return cls(
            role=role if isinstance(role, str) else None,
            content=content if isinstance(content, str) else None,
            tool_calls=(
                [ToolCallFragment.from_dict(c) for c in calls if isinstance(c, dict)]
                if isinstance(calls, list)
                else None
            ),
        )


@dataclass(frozen=True)
class ChunkChoice:
    index: int
    delta: ChunkDelta
    finish_reason: str | None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ChunkChoice:
        delta = data.get("delta")
        return cls(
            index=_int(data.get("index")),
            delta=ChunkDelta.from_dict(delta if isinstance(delta, dict) else {}),
            finish_reason=data.get("finish_reason"),
        )


@dataclass(frozen=True)
class ChatCompletionChunk:
    """One `data:` frame of a streamed completion. The usage-only frame the
    API sends with `stream_options.include_usage` has no choices and a
    `usage`; every other frame has one choice and `usage` None."""

    id: str
    created: int
    model: str
    choices: list[ChunkChoice]
    usage: Usage | None = None
    object: str = "chat.completion.chunk"
    request_id: str | None = field(default=None, compare=False)

    @property
    def content(self) -> str:
        """The text this frame adds, or "" for the role, stop and usage frames."""
        if not self.choices:
            return ""
        return self.choices[0].delta.content or ""

    @classmethod
    def from_dict(
        cls, data: dict[str, Any], *, request_id: str | None = None
    ) -> ChatCompletionChunk:
        usage = data.get("usage")
        return cls(
            id=_str(data.get("id")),
            object=_str(data.get("object"), "chat.completion.chunk"),
            created=_int(data.get("created")),
            model=_str(data.get("model")),
            choices=[
                ChunkChoice.from_dict(c) for c in data.get("choices", []) if isinstance(c, dict)
            ],
            usage=Usage.from_dict(usage) if isinstance(usage, dict) else None,
            request_id=request_id,
        )


class ChatCompletionAccumulator:
    """Folds a stream's chunks into the message they spell out: the text
    joined, and each tool call put back together from its fragments. The
    first fragment for an `index` carries the call's `id` and name, the
    rest carry pieces of `arguments` to join in order; calls at different
    indexes may interleave.

        acc = ChatCompletionAccumulator()
        for chunk in stream:
            acc.add(chunk)          # render chunk.content as it arrives
        message = acc.message()     # content and complete tool_calls

    `Stream.final_message()` runs that loop."""

    def __init__(self) -> None:
        self._text: list[str] = []
        self._calls: dict[int, _PendingCall] = {}
        self.finish_reason: str | None = None
        self.usage: Usage | None = None
        self.model: str | None = None
        self.id: str | None = None

    def add(self, chunk: ChatCompletionChunk) -> None:
        if chunk.usage is not None:
            self.usage = chunk.usage
        if chunk.model:
            self.model = chunk.model
        if chunk.id:
            self.id = chunk.id
        if not chunk.choices:
            return
        choice = chunk.choices[0]
        if choice.finish_reason is not None:
            self.finish_reason = choice.finish_reason
        delta = choice.delta
        if delta.content:
            self._text.append(delta.content)
        for fragment in delta.tool_calls or []:
            call = self._calls.setdefault(fragment.index, _PendingCall())
            if fragment.id:
                call.id = fragment.id
            if fragment.type:
                call.type = fragment.type
            if fragment.function.name:
                call.name += fragment.function.name
            if fragment.function.arguments:
                call.arguments.append(fragment.function.arguments)

    @property
    def content(self) -> str:
        return "".join(self._text)

    @property
    def tool_calls(self) -> list[ToolCall]:
        """The calls so far, in index order, each with its arguments joined."""
        return [
            ToolCall(
                id=call.id,
                type=call.type,
                function=FunctionCall(name=call.name, arguments="".join(call.arguments)),
            )
            for _, call in sorted(self._calls.items())
        ]

    def message(self) -> AssistantMessage:
        """The assistant message the stream spelled out; `content` is None
        on a pure tool-call turn, as the non-streamed answer has it."""
        calls = self.tool_calls
        text = self.content
        return AssistantMessage(
            role="assistant",
            content=text if text or not calls else None,
            tool_calls=calls or None,
        )


@dataclass
class _PendingCall:
    id: str = ""
    type: str = "function"
    name: str = ""
    arguments: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Model:
    id: str
    created: int
    owned_by: str
    object: str = "model"
    request_id: str | None = field(default=None, compare=False)

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, request_id: str | None = None) -> Model:
        return cls(
            id=_str(data.get("id")),
            object=_str(data.get("object"), "model"),
            created=_int(data.get("created")),
            owned_by=_str(data.get("owned_by")),
            request_id=request_id,
        )


@dataclass(frozen=True)
class ModelList:
    data: list[Model]
    object: str = "list"
    request_id: str | None = field(default=None, compare=False)

    def __iter__(self) -> Any:
        return iter(self.data)

    def __len__(self) -> int:
        return len(self.data)

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, request_id: str | None = None) -> ModelList:
        return cls(
            object=_str(data.get("object"), "list"),
            data=[Model.from_dict(m) for m in data.get("data", []) if isinstance(m, dict)],
            request_id=request_id,
        )


@dataclass(frozen=True)
class Embedding:
    index: int
    # Floats, or a base64 string when the request asked for `encoding_format: base64`.
    embedding: list[float] | str
    object: str = "embedding"

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Embedding:
        vector = data.get("embedding")
        return cls(
            index=_int(data.get("index")),
            object=_str(data.get("object"), "embedding"),
            embedding=(
                vector
                if isinstance(vector, str)
                else [float(v) for v in vector if isinstance(v, (int, float))]
                if isinstance(vector, list)
                else []
            ),
        )


@dataclass(frozen=True)
class EmbeddingUsage:
    prompt_tokens: int
    total_tokens: int

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EmbeddingUsage:
        return cls(
            prompt_tokens=_int(data.get("prompt_tokens")),
            total_tokens=_int(data.get("total_tokens")),
        )


@dataclass(frozen=True)
class EmbeddingList:
    """`POST /v1/embeddings`: one embedding per input, in input order."""

    data: list[Embedding]
    model: str
    usage: EmbeddingUsage
    object: str = "list"
    request_id: str | None = field(default=None, compare=False)

    def __iter__(self) -> Any:
        return iter(self.data)

    def __len__(self) -> int:
        return len(self.data)

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, request_id: str | None = None) -> EmbeddingList:
        usage = data.get("usage")
        return cls(
            object=_str(data.get("object"), "list"),
            data=[Embedding.from_dict(e) for e in data.get("data", []) if isinstance(e, dict)],
            model=_str(data.get("model")),
            usage=EmbeddingUsage.from_dict(usage if isinstance(usage, dict) else {}),
            request_id=request_id,
        )


@dataclass(frozen=True)
class Stability:
    stable: bool
    promise: str
    preview_prefix: str

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Stability:
        return cls(
            stable=bool(data.get("stable", False)),
            promise=_str(data.get("promise")),
            preview_prefix=_str(data.get("preview_prefix")),
        )


@dataclass(frozen=True)
class ApiIndex:
    """`GET /v1`: what the API is and what it promises. Needs no key."""

    name: str
    api_version: str
    release: str
    compatible_with: str
    endpoints: list[str]
    # What the deployment can do (`chat_completions`, `embeddings`, ...);
    # the server owns the keys, so they are kept as sent.
    capabilities: dict[str, Any]
    stability: Stability
    extensions: dict[str, str]
    docs: str
    object: str = "api"
    request_id: str | None = field(default=None, compare=False)

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, request_id: str | None = None) -> ApiIndex:
        stability = data.get("stability")
        extensions = data.get("extensions")
        capabilities = data.get("capabilities")
        return cls(
            object=_str(data.get("object"), "api"),
            name=_str(data.get("name")),
            api_version=_str(data.get("api_version")),
            release=_str(data.get("release")),
            compatible_with=_str(data.get("compatible_with")),
            endpoints=[e for e in data.get("endpoints", []) if isinstance(e, str)],
            capabilities=dict(capabilities) if isinstance(capabilities, dict) else {},
            stability=Stability.from_dict(stability if isinstance(stability, dict) else {}),
            extensions={
                str(k): str(v) for k, v in (extensions if isinstance(extensions, dict) else {}).items()
            },
            docs=_str(data.get("docs")),
            request_id=request_id,
        )


# --- the conversation API (`/api`) -------------------------------------------
#
# The routes the web app itself uses, open to an API key as its owner. Their
# bodies are richer than these types and grow with the app; the fields below
# are the ones a client renders, and the rest are dropped as above.


def _opt_str(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def _opt_int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _dicts(value: Any) -> list[dict[str, Any]]:
    return [dict(v) for v in value if isinstance(v, dict)] if isinstance(value, list) else []


@dataclass(frozen=True)
class ConversationMessage:
    """One message of a conversation's tree. `parts` is the ordered list the
    renderer maps to components (text, reasoning, tool_call, tool_result,
    image, document, summary); `content` is the same message flattened to
    text. `parent_id` links the tree; siblings are branches. On an assistant
    message `truncated` says the answer was cut short and `finish_reason`
    why the model stopped (`length` when the token limit cut it)."""

    id: str
    conversation_id: str
    role: str
    parts: list[dict[str, Any]]
    content: str
    created_at: str
    parent_id: str | None = None
    model: str | None = None
    truncated: bool = False
    finish_reason: str | None = None
    # Set when the message was the whole response (`compact`); None on a
    # message inside a conversation or a frame, whose response owns the id.
    request_id: str | None = field(default=None, compare=False)

    @property
    def summary(self) -> dict[str, Any] | None:
        """The summary part when this is a compaction's summary message:
        `text`, `covers_from`, `covers_to`, `tokens_before`, `tokens_after`."""
        for part in self.parts:
            if part.get("type") == "summary":
                return part
        return None

    @classmethod
    def from_dict(
        cls, data: dict[str, Any], *, request_id: str | None = None
    ) -> ConversationMessage:
        return cls(
            id=_str(data.get("id")),
            conversation_id=_str(data.get("conversation_id")),
            role=_str(data.get("role")),
            parts=_dicts(data.get("parts")),
            content=_str(data.get("content")),
            created_at=_str(data.get("created_at")),
            parent_id=_opt_str(data.get("parent_id")),
            model=_opt_str(data.get("model")),
            truncated=data.get("truncated") is True,
            finish_reason=_opt_str(data.get("finish_reason")),
            request_id=request_id,
        )


@dataclass(frozen=True)
class Conversation:
    """A conversation. `messages` is the active path when the route returned
    one (`get`, `set_active_leaf`, `fork`) or every message for `tree`, and
    empty for `list` and `create`; `tree` is filled by `get(..., full=True)`."""

    id: str
    title: str
    created_at: str
    updated_at: str
    org_id: str | None = None
    last_message_at: str | None = None
    preview: str | None = None
    message_count: int | None = None
    default_model: str | None = None
    current_leaf_message_id: str | None = None
    pinned_at: str | None = None
    archived_at: str | None = None
    folder_id: str | None = None
    # A temporary conversation is in no list or search, cannot be pinned,
    # shared, forked, filed or tagged, and is hard-deleted a day after its
    # last message. Fixed at creation.
    temporary: bool = False
    messages: list[ConversationMessage] = field(default_factory=list)
    tree: list[ConversationMessage] | None = None
    # The instruction levels in force, as `get` reports them; the server owns the shape.
    instructions: dict[str, Any] | None = None
    request_id: str | None = field(default=None, compare=False)

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, request_id: str | None = None) -> Conversation:
        tree = data.get("tree")
        instructions = data.get("instructions")
        return cls(
            id=_str(data.get("id")),
            title=_str(data.get("title")),
            created_at=_str(data.get("created_at")),
            updated_at=_str(data.get("updated_at")),
            org_id=_opt_str(data.get("org_id")),
            last_message_at=_opt_str(data.get("last_message_at")),
            preview=_opt_str(data.get("preview")),
            message_count=_opt_int(data.get("message_count")),
            default_model=_opt_str(data.get("default_model")),
            current_leaf_message_id=_opt_str(data.get("current_leaf_message_id")),
            pinned_at=_opt_str(data.get("pinned_at")),
            archived_at=_opt_str(data.get("archived_at")),
            folder_id=_opt_str(data.get("folder_id")),
            temporary=data.get("temporary") is True,
            messages=[ConversationMessage.from_dict(m) for m in _dicts(data.get("messages"))],
            tree=(
                [ConversationMessage.from_dict(m) for m in _dicts(tree)]
                if isinstance(tree, list)
                else None
            ),
            instructions=dict(instructions) if isinstance(instructions, dict) else None,
            request_id=request_id,
        )


@dataclass(frozen=True)
class ConversationList:
    """`GET /api/conversations`: one page of the sidebar list for one archive
    slice. `next_cursor` resumes after it and is None on the last page."""

    data: list[Conversation]
    filter: str
    next_cursor: str | None = None
    request_id: str | None = field(default=None, compare=False)

    def __iter__(self) -> Any:
        return iter(self.data)

    def __len__(self) -> int:
        return len(self.data)

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, request_id: str | None = None) -> ConversationList:
        return cls(
            data=[Conversation.from_dict(c) for c in _dicts(data.get("data"))],
            filter=_str(data.get("filter"), "default"),
            next_cursor=_opt_str(data.get("next_cursor")),
            request_id=request_id,
        )


@dataclass(frozen=True)
class SearchHit:
    """One conversation a search matched, the headline around the match,
    and the message it was found in (None for a title match)."""

    conversation: Conversation
    snippet: str
    message_id: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SearchHit:
        return cls(
            conversation=Conversation.from_dict(data),
            snippet=_str(data.get("snippet")),
            message_id=_opt_str(data.get("message_id")),
        )


@dataclass(frozen=True)
class SearchResults:
    """`GET /api/conversations/search`, and `GET /api/search` (`library.search`),
    which pages: `next_cursor` fetches the page after this one and is None on
    the last, or where the route does not page."""

    data: list[SearchHit]
    query: str
    next_cursor: str | None = None
    request_id: str | None = field(default=None, compare=False)

    def __iter__(self) -> Any:
        return iter(self.data)

    def __len__(self) -> int:
        return len(self.data)

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, request_id: str | None = None) -> SearchResults:
        return cls(
            data=[SearchHit.from_dict(h) for h in _dicts(data.get("data"))],
            query=_str(data.get("query")),
            next_cursor=_opt_str(data.get("next_cursor")),
            request_id=request_id,
        )


@dataclass(frozen=True)
class ConversationContext:
    """`GET /api/conversations/{id}/context`: how full the model's window is.
    `context_window` is None when the model's size is unknown, `used_tokens`
    until a turn has been counted; `estimated` is True when `used_tokens` is
    the latest summary's estimate rather than a real count; `compactable`
    says whether a compaction has anything to fold."""

    model: str
    context_window: int | None
    used_tokens: int | None
    compactable: bool
    estimated: bool = False
    request_id: str | None = field(default=None, compare=False)

    @classmethod
    def from_dict(
        cls, data: dict[str, Any], *, request_id: str | None = None
    ) -> ConversationContext:
        return cls(
            model=_str(data.get("model")),
            context_window=_opt_int(data.get("context_window")),
            used_tokens=_opt_int(data.get("used_tokens")),
            compactable=data.get("compactable") is True,
            estimated=data.get("estimated") is True,
            request_id=request_id,
        )


@dataclass(frozen=True)
class Me:
    """`GET /api/me`: who the bearer acts as. A key acts as its owner."""

    subject: str
    kind: str
    email: str | None
    name: str | None
    roles: list[str]
    org_id: str | None
    org_name: str | None
    org_role: str | None
    state: str | None = None
    # The org's "local agent tools allowed" setting: whether a client may
    # offer its agent mode, whose tools run on the user's machine. None
    # when the server did not say.
    local_agent_tools: bool | None = None
    request_id: str | None = field(default=None, compare=False)

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, request_id: str | None = None) -> Me:
        roles = data.get("roles")
        agent_tools = data.get("local_agent_tools")
        return cls(
            subject=_str(data.get("subject")),
            kind=_str(data.get("kind")),
            email=_opt_str(data.get("email")),
            name=_opt_str(data.get("name")),
            roles=[r for r in roles if isinstance(r, str)] if isinstance(roles, list) else [],
            org_id=_opt_str(data.get("org_id")),
            org_name=_opt_str(data.get("org_name")),
            org_role=_opt_str(data.get("org_role")),
            state=_opt_str(data.get("state")),
            local_agent_tools=agent_tools if isinstance(agent_tools, bool) else None,
            request_id=request_id,
        )


# --- device login ------------------------------------------------------------


@dataclass(frozen=True)
class DeviceCode:
    """What to show the person: open `verification_uri` and type `user_code`,
    or open `verification_uri_complete`, which carries the code. The
    `device_code` is the client's half and is never shown."""

    device_code: str
    user_code: str
    verification_uri: str
    verification_uri_complete: str
    expires_in: int
    interval: int
    request_id: str | None = field(default=None, compare=False)

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, request_id: str | None = None) -> DeviceCode:
        verification_uri = _str(data.get("verification_uri"))
        return cls(
            device_code=_str(data.get("device_code")),
            user_code=_str(data.get("user_code")),
            verification_uri=verification_uri,
            verification_uri_complete=_str(
                data.get("verification_uri_complete"), verification_uri
            ),
            expires_in=_int(data.get("expires_in"), 600),
            # RFC 8628 section 3.2: five seconds when the server names no interval.
            interval=_int(data.get("interval"), 5),
            request_id=request_id,
        )


@dataclass(frozen=True)
class DeviceToken:
    """An approved device login. `access_token` is an ordinary API key: pass
    it as `api_key=` to a new client. Storing it is the caller's job."""

    access_token: str = field(repr=False)
    token_type: str
    scopes: list[str]
    request_id: str | None = field(default=None, compare=False)

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, request_id: str | None = None) -> DeviceToken:
        scopes = data.get("scopes")
        if isinstance(scopes, str):
            scopes = scopes.split()
        return cls(
            access_token=_str(data.get("access_token")),
            token_type=_str(data.get("token_type"), "Bearer"),
            scopes=[s for s in scopes if isinstance(s, str)] if isinstance(scopes, list) else [],
            request_id=request_id,
        )


# --- the native API ----------------------------------------------------------
#
# Keys, usage, webhooks, attachments, the library and shares: the routes the
# web app's settings panes use. As above, the fields are the ones the routes
# send today and anything new is dropped rather than refused.


def _strs(value: Any) -> list[str]:
    return [v for v in value if isinstance(v, str)] if isinstance(value, list) else []


def _opt_strs(value: Any) -> list[str] | None:
    return _strs(value) if isinstance(value, list) else None


def _float(value: Any) -> float:
    if isinstance(value, bool):
        return 0.0
    return float(value) if isinstance(value, (int, float)) else 0.0


def _dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


@dataclass(frozen=True)
class ApiKey:
    """One API key as `GET /api/keys` lists it: never the secret. `key` is
    set only on the answer to `create` and `rotate`, the one time the API
    sends it. After a rotation the old secret keeps working until
    `previous_valid_until`."""

    id: str
    prefix: str
    name: str
    kind: str
    scopes: list[str]
    created_at: str
    expires_at: str | None = None
    revoked_at: str | None = None
    last_used_at: str | None = None
    rotated_at: str | None = None
    previous_prefix: str | None = None
    previous_valid_until: str | None = None
    allowed_cidrs: list[str] | None = None
    rpm_limit: int | None = None
    key: str | None = field(default=None, repr=False)
    request_id: str | None = field(default=None, compare=False)

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, request_id: str | None = None) -> ApiKey:
        return cls(
            id=_str(data.get("id")),
            prefix=_str(data.get("prefix")),
            name=_str(data.get("name")),
            kind=_str(data.get("kind")),
            scopes=_strs(data.get("scopes")),
            created_at=_str(data.get("created_at")),
            expires_at=_opt_str(data.get("expires_at")),
            revoked_at=_opt_str(data.get("revoked_at")),
            last_used_at=_opt_str(data.get("last_used_at")),
            rotated_at=_opt_str(data.get("rotated_at")),
            previous_prefix=_opt_str(data.get("previous_prefix")),
            previous_valid_until=_opt_str(data.get("previous_valid_until")),
            allowed_cidrs=_opt_strs(data.get("allowed_cidrs")),
            rpm_limit=_opt_int(data.get("rpm_limit")),
            key=_opt_str(data.get("key")),
            request_id=request_id,
        )


@dataclass(frozen=True)
class ApiKeyList:
    """`GET /api/keys`: the org's keys, revoked ones included. A key without
    `keys:manage` is shown its own row only."""

    data: list[ApiKey]
    request_id: str | None = field(default=None, compare=False)

    def __iter__(self) -> Any:
        return iter(self.data)

    def __len__(self) -> int:
        return len(self.data)

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, request_id: str | None = None) -> ApiKeyList:
        return cls(
            data=[ApiKey.from_dict(k) for k in _dicts(data.get("data"))], request_id=request_id
        )


# --- usage -------------------------------------------------------------------


@dataclass(frozen=True)
class ModelUsage:
    """One model's share of the period, priced at today's label and multiplier."""

    model: str
    label: str
    multiplier: float
    prompt_tokens: int
    completion_tokens: int
    tokens: int
    weighted_tokens: int
    cost_micros: int
    overage_micros: int
    requests: int
    compaction_weighted_tokens: int = 0
    compaction_requests: int = 0

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ModelUsage:
        return cls(
            model=_str(data.get("model")),
            label=_str(data.get("label")),
            multiplier=_float(data.get("multiplier")),
            prompt_tokens=_int(data.get("prompt_tokens")),
            completion_tokens=_int(data.get("completion_tokens")),
            tokens=_int(data.get("tokens")),
            weighted_tokens=_int(data.get("weighted_tokens")),
            cost_micros=_int(data.get("cost_micros")),
            overage_micros=_int(data.get("overage_micros")),
            requests=_int(data.get("requests")),
            compaction_weighted_tokens=_int(data.get("compaction_weighted_tokens")),
            compaction_requests=_int(data.get("compaction_requests")),
        )


@dataclass(frozen=True)
class DailyUsage:
    """One day of the period's history."""

    day: str
    weighted_tokens: int
    tokens: int
    cost_micros: int
    overage_micros: int
    requests: int
    compaction_weighted_tokens: int = 0
    compaction_requests: int = 0

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DailyUsage:
        return cls(
            day=_str(data.get("day")),
            weighted_tokens=_int(data.get("weighted_tokens")),
            tokens=_int(data.get("tokens")),
            cost_micros=_int(data.get("cost_micros")),
            overage_micros=_int(data.get("overage_micros")),
            requests=_int(data.get("requests")),
            compaction_weighted_tokens=_int(data.get("compaction_weighted_tokens")),
            compaction_requests=_int(data.get("compaction_requests")),
        )


@dataclass(frozen=True)
class UsageBalance:
    """`GET /api/me/usage`: the org's balance this period. `used`, `included`
    and `remaining` are weighted tokens; `warn`, `in_overage` and
    `exhausted` never overlap. `plan`, `seats`, `overage` and `compaction`
    are kept as the server shapes them."""

    unit: str
    used: int
    included: int
    remaining: int
    percent: float
    warn: bool
    in_overage: bool
    exhausted: bool
    period_start: str
    resets_at: str
    plan: dict[str, Any]
    overage: dict[str, Any]
    seats: dict[str, Any] | None = None
    compaction: dict[str, Any] = field(default_factory=dict)
    models: list[ModelUsage] = field(default_factory=list)
    history: list[DailyUsage] = field(default_factory=list)
    request_id: str | None = field(default=None, compare=False)

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, request_id: str | None = None) -> UsageBalance:
        seats = data.get("seats")
        return cls(
            unit=_str(data.get("unit"), "tokens"),
            used=_int(data.get("used")),
            included=_int(data.get("included")),
            remaining=_int(data.get("remaining")),
            percent=_float(data.get("percent")),
            warn=data.get("warn") is True,
            in_overage=data.get("in_overage") is True,
            exhausted=data.get("exhausted") is True,
            period_start=_str(data.get("period_start")),
            resets_at=_str(data.get("resets_at")),
            plan=_dict(data.get("plan")),
            overage=_dict(data.get("overage")),
            seats=dict(seats) if isinstance(seats, dict) else None,
            compaction=_dict(data.get("compaction")),
            models=[ModelUsage.from_dict(m) for m in _dicts(data.get("models"))],
            history=[DailyUsage.from_dict(d) for d in _dicts(data.get("history"))],
            request_id=request_id,
        )


@dataclass(frozen=True)
class MemberUsage:
    """One member's month. `subject` is `unattributed` for usage no member
    owns, so the rows still sum to the org's total."""

    subject: str
    email: str | None
    name: str | None
    weighted_tokens: int
    tokens: int
    cost_micros: int
    turns: int
    last_active: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MemberUsage:
        return cls(
            subject=_str(data.get("subject")),
            email=_opt_str(data.get("email")),
            name=_opt_str(data.get("name")),
            weighted_tokens=_int(data.get("weighted_tokens")),
            tokens=_int(data.get("tokens")),
            cost_micros=_int(data.get("cost_micros")),
            turns=_int(data.get("turns")),
            last_active=_opt_str(data.get("last_active")),
        )


@dataclass(frozen=True)
class UsageTotals:
    weighted_tokens: int
    cost_micros: int
    turns: int
    tokens: int

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> UsageTotals:
        return cls(
            weighted_tokens=_int(data.get("weighted_tokens")),
            cost_micros=_int(data.get("cost_micros")),
            turns=_int(data.get("turns")),
            tokens=_int(data.get("tokens")),
        )


@dataclass(frozen=True)
class OrgUsage:
    """`GET /api/org/usage`: one calendar month per member. `scope` is `org`
    for an owner or admin (every member) and `self` for anyone else (their
    own row); `totals` are over the rows returned."""

    period: str
    period_start: str
    period_end: str
    unit: str
    scope: str
    data: list[MemberUsage]
    totals: UsageTotals
    request_id: str | None = field(default=None, compare=False)

    def __iter__(self) -> Any:
        return iter(self.data)

    def __len__(self) -> int:
        return len(self.data)

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, request_id: str | None = None) -> OrgUsage:
        return cls(
            period=_str(data.get("period")),
            period_start=_str(data.get("period_start")),
            period_end=_str(data.get("period_end")),
            unit=_str(data.get("unit"), "tokens"),
            scope=_str(data.get("scope")),
            data=[MemberUsage.from_dict(r) for r in _dicts(data.get("data"))],
            totals=UsageTotals.from_dict(_dict(data.get("totals"))),
            request_id=request_id,
        )


# --- the audit trail -----------------------------------------------------------


@dataclass(frozen=True)
class AuditEvent:
    """One row of the org's trail (`GET /api/org/audit`): who changed what,
    and when. `actor` is the subject the writer had in hand -- a system
    actor's looks like `stripe:evt_...` -- and `actor_email` is filled in
    where that subject has a users row, None otherwise. `detail` is the
    writer's structured context, kept as sent."""

    id: str
    occurred_at: str
    actor: str
    actor_email: str | None
    action: str
    target: str | None
    detail: dict[str, Any]

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AuditEvent:
        return cls(
            id=_str(data.get("id")),
            occurred_at=_str(data.get("occurred_at")),
            actor=_str(data.get("actor")),
            actor_email=_opt_str(data.get("actor_email")),
            action=_str(data.get("action")),
            target=_opt_str(data.get("target")),
            detail=_dict(data.get("detail")),
        )


@dataclass(frozen=True)
class AuditPage:
    """`GET /api/org/audit`: one page of the trail, newest first.
    `next_cursor` fetches the page after this one and is None on the last;
    `actions` lists every action the trail holds, so a client's filter can
    offer what exists rather than a list it maintains itself."""

    data: list[AuditEvent]
    actions: list[str]
    next_cursor: str | None = None
    request_id: str | None = field(default=None, compare=False)

    def __iter__(self) -> Any:
        return iter(self.data)

    def __len__(self) -> int:
        return len(self.data)

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, request_id: str | None = None) -> AuditPage:
        return cls(
            data=[AuditEvent.from_dict(r) for r in _dicts(data.get("data"))],
            actions=_strs(data.get("actions")),
            next_cursor=_opt_str(data.get("next_cursor")),
            request_id=request_id,
        )


# --- webhooks ----------------------------------------------------------------


@dataclass(frozen=True)
class WebhookEndpoint:
    """One endpoint. `secret` and `verification` (the receiver-side check,
    by language, with the secret filled in) are set only on the answer to
    `create` and `rotate_secret`, the one time the API sends them."""

    id: str
    url: str
    description: str
    events: list[str]
    enabled: bool
    failure_count: int
    created_at: str
    updated_at: str
    disabled_at: str | None = None
    secret: str | None = field(default=None, repr=False)
    verification: dict[str, str] | None = field(default=None, repr=False)
    request_id: str | None = field(default=None, compare=False)

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, request_id: str | None = None) -> WebhookEndpoint:
        verification = data.get("verification")
        return cls(
            id=_str(data.get("id")),
            url=_str(data.get("url")),
            description=_str(data.get("description")),
            events=_strs(data.get("events")),
            enabled=data.get("enabled") is True,
            failure_count=_int(data.get("failure_count")),
            created_at=_str(data.get("created_at")),
            updated_at=_str(data.get("updated_at")),
            disabled_at=_opt_str(data.get("disabled_at")),
            secret=_opt_str(data.get("secret")),
            verification=(
                {k: v for k, v in verification.items() if isinstance(v, str)}
                if isinstance(verification, dict)
                else None
            ),
            request_id=request_id,
        )


@dataclass(frozen=True)
class WebhookEndpointList:
    """`GET /api/org/webhooks`: the endpoints, and `events`, every event
    type an endpoint may subscribe to."""

    data: list[WebhookEndpoint]
    events: list[str]
    request_id: str | None = field(default=None, compare=False)

    def __iter__(self) -> Any:
        return iter(self.data)

    def __len__(self) -> int:
        return len(self.data)

    @classmethod
    def from_dict(
        cls, data: dict[str, Any], *, request_id: str | None = None
    ) -> WebhookEndpointList:
        return cls(
            data=[WebhookEndpoint.from_dict(e) for e in _dicts(data.get("data"))],
            events=_strs(data.get("events")),
            request_id=request_id,
        )


@dataclass(frozen=True)
class WebhookDelivery:
    """One event bound for one endpoint and how sending it went. `status`
    is `pending`, `sending`, `delivered` or `failed`."""

    id: str
    endpoint_id: str
    event: str
    event_id: str
    payload: dict[str, Any]
    attempt: int
    status: str
    created_at: str
    response_code: int | None = None
    response_ms: int | None = None
    response_excerpt: str | None = None
    next_attempt_at: str | None = None
    delivered_at: str | None = None
    last_error: str | None = None
    request_id: str | None = field(default=None, compare=False)

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, request_id: str | None = None) -> WebhookDelivery:
        return cls(
            id=_str(data.get("id")),
            endpoint_id=_str(data.get("endpoint_id")),
            event=_str(data.get("event")),
            event_id=_str(data.get("event_id")),
            payload=_dict(data.get("payload")),
            attempt=_int(data.get("attempt")),
            status=_str(data.get("status")),
            created_at=_str(data.get("created_at")),
            response_code=_opt_int(data.get("response_code")),
            response_ms=_opt_int(data.get("response_ms")),
            response_excerpt=_opt_str(data.get("response_excerpt")),
            next_attempt_at=_opt_str(data.get("next_attempt_at")),
            delivered_at=_opt_str(data.get("delivered_at")),
            last_error=_opt_str(data.get("last_error")),
            request_id=request_id,
        )


@dataclass(frozen=True)
class WebhookDeliveryList:
    """`GET /api/org/webhooks/{id}/deliveries`, filtered by `status` when one was asked for."""

    data: list[WebhookDelivery]
    status: str | None = None
    request_id: str | None = field(default=None, compare=False)

    def __iter__(self) -> Any:
        return iter(self.data)

    def __len__(self) -> int:
        return len(self.data)

    @classmethod
    def from_dict(
        cls, data: dict[str, Any], *, request_id: str | None = None
    ) -> WebhookDeliveryList:
        return cls(
            data=[WebhookDelivery.from_dict(d) for d in _dicts(data.get("data"))],
            status=_opt_str(data.get("status")),
            request_id=request_id,
        )


# --- attachments -------------------------------------------------------------


@dataclass(frozen=True)
class Attachment:
    """An uploaded file. `content_type` is the one the server read from the
    bytes, not the one sent. `download_url` is a signed path that expires
    in minutes and works only with this client's key: pass it to
    `attachments.download`."""

    id: str
    filename: str
    content_type: str
    size_bytes: int
    scan_status: str
    created_at: str
    message_id: str | None = None
    extract_truncated: bool = False
    download_url: str | None = None
    request_id: str | None = field(default=None, compare=False)

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, request_id: str | None = None) -> Attachment:
        return cls(
            id=_str(data.get("id")),
            filename=_str(data.get("filename")),
            content_type=_str(data.get("content_type")),
            size_bytes=_int(data.get("size_bytes")),
            scan_status=_str(data.get("scan_status")),
            created_at=_str(data.get("created_at")),
            message_id=_opt_str(data.get("message_id")),
            extract_truncated=data.get("extract_truncated") is True,
            download_url=_opt_str(data.get("download_url")),
            request_id=request_id,
        )


@dataclass(frozen=True)
class AttachmentLimits:
    """`GET /api/attachments/limits/me`: what the caller may still upload."""

    max_upload_bytes: int
    quota_bytes: int
    quota_used_bytes: int
    quota_remaining_bytes: int
    request_id: str | None = field(default=None, compare=False)

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, request_id: str | None = None) -> AttachmentLimits:
        return cls(
            max_upload_bytes=_int(data.get("max_upload_bytes")),
            quota_bytes=_int(data.get("quota_bytes")),
            quota_used_bytes=_int(data.get("quota_used_bytes")),
            quota_remaining_bytes=_int(data.get("quota_remaining_bytes")),
            request_id=request_id,
        )


# --- library -----------------------------------------------------------------


@dataclass(frozen=True)
class Folder:
    """A sidebar folder; `position` is the order its owner arranged."""

    id: str
    name: str
    position: int
    created_at: str
    updated_at: str
    request_id: str | None = field(default=None, compare=False)

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, request_id: str | None = None) -> Folder:
        return cls(
            id=_str(data.get("id")),
            name=_str(data.get("name")),
            position=_int(data.get("position")),
            created_at=_str(data.get("created_at")),
            updated_at=_str(data.get("updated_at")),
            request_id=request_id,
        )


@dataclass(frozen=True)
class Tag:
    id: str
    name: str
    created_at: str
    request_id: str | None = field(default=None, compare=False)

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, request_id: str | None = None) -> Tag:
        return cls(
            id=_str(data.get("id")),
            name=_str(data.get("name")),
            created_at=_str(data.get("created_at")),
            request_id=request_id,
        )


@dataclass(frozen=True)
class Library:
    """`GET /api/library`: the caller's folders and tags, and `assignments`,
    conversation id to the ids of the tags it carries."""

    folders: list[Folder]
    tags: list[Tag]
    assignments: dict[str, list[str]]
    request_id: str | None = field(default=None, compare=False)

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, request_id: str | None = None) -> Library:
        assignments = data.get("assignments")
        return cls(
            folders=[Folder.from_dict(f) for f in _dicts(data.get("folders"))],
            tags=[Tag.from_dict(t) for t in _dicts(data.get("tags"))],
            assignments=(
                {k: _strs(v) for k, v in assignments.items() if isinstance(k, str)}
                if isinstance(assignments, dict)
                else {}
            ),
            request_id=request_id,
        )


@dataclass(frozen=True)
class ConversationTags:
    """The answer to `library.set_tags`: the conversation's tags now."""

    conversation_id: str
    tag_ids: list[str]
    request_id: str | None = field(default=None, compare=False)

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, request_id: str | None = None) -> ConversationTags:
        return cls(
            conversation_id=_str(data.get("conversation_id")),
            tag_ids=_strs(data.get("tag_ids")),
            request_id=request_id,
        )


# --- shares ------------------------------------------------------------------


@dataclass(frozen=True)
class Share:
    """A share link as its maker sees it: never the token. `status` is
    `active`, `expired` or `revoked`."""

    id: str
    conversation_id: str
    title: str
    message_count: int
    created_at: str
    view_count: int
    status: str
    expires_at: str | None = None
    revoked_at: str | None = None
    request_id: str | None = field(default=None, compare=False)

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, request_id: str | None = None) -> Share:
        return cls(
            id=_str(data.get("id")),
            conversation_id=_str(data.get("conversation_id")),
            title=_str(data.get("title")),
            message_count=_int(data.get("message_count")),
            created_at=_str(data.get("created_at")),
            view_count=_int(data.get("view_count")),
            status=_str(data.get("status")),
            expires_at=_opt_str(data.get("expires_at")),
            revoked_at=_opt_str(data.get("revoked_at")),
            request_id=request_id,
        )


@dataclass(frozen=True)
class ShareList:
    data: list[Share]
    request_id: str | None = field(default=None, compare=False)

    def __iter__(self) -> Any:
        return iter(self.data)

    def __len__(self) -> int:
        return len(self.data)

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, request_id: str | None = None) -> ShareList:
        return cls(
            data=[Share.from_dict(s) for s in _dicts(data.get("data"))], request_id=request_id
        )


@dataclass(frozen=True)
class CreatedShare:
    """A new link. `token` is in this answer and nowhere else; `path` is the
    web app's route for it, to be prefixed with the app's origin."""

    share: Share
    token: str = field(repr=False)
    path: str = field(repr=False)
    request_id: str | None = field(default=None, compare=False)

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, request_id: str | None = None) -> CreatedShare:
        return cls(
            share=Share.from_dict(_dict(data.get("share"))),
            token=_str(data.get("token")),
            path=_str(data.get("path")),
            request_id=request_id,
        )


@dataclass(frozen=True)
class SharedConversation:
    """`GET /api/share/{token}`: the snapshot anyone holding the link reads.
    `messages` are as the server snapshotted them; `org_name` is set only
    where the org opted in."""

    title: str
    shared_at: str | None
    messages: list[dict[str, Any]]
    org_name: str | None = None
    request_id: str | None = field(default=None, compare=False)

    @classmethod
    def from_dict(
        cls, data: dict[str, Any], *, request_id: str | None = None
    ) -> SharedConversation:
        return cls(
            title=_str(data.get("title")),
            shared_at=_opt_str(data.get("shared_at")),
            messages=_dicts(data.get("messages")),
            org_name=_opt_str(data.get("org_name")),
            request_id=request_id,
        )


# --- the app routes this client grew in 0.2 -----------------------------------
#
# Lifecycle toggles, custom instructions, reply feedback, the model picker
# and the budget pane. As above, the fields are the ones the routes send
# today and anything new is dropped rather than refused.


@dataclass(frozen=True)
class DeletedConversation:
    """The answer to `conversations.delete`. The row goes after a 30-day
    delay; `attachments_deleted` counts the uploads it took with it, absent
    when there were none."""

    status: str
    attachments_deleted: int | None = None
    request_id: str | None = field(default=None, compare=False)

    @classmethod
    def from_dict(
        cls, data: dict[str, Any], *, request_id: str | None = None
    ) -> DeletedConversation:
        return cls(
            status=_str(data.get("status")),
            attachments_deleted=_opt_int(data.get("attachments_deleted")),
            request_id=request_id,
        )


@dataclass(frozen=True)
class UserInstructions:
    """`GET` and `PUT /api/me/instructions`: what the assistant should know
    about the person, and how they want answers shaped. The route replaces
    the pair, so a field left out of `update_user` is sent as empty."""

    about: str = ""
    style: str = ""
    request_id: str | None = field(default=None, compare=False)

    @classmethod
    def from_dict(
        cls, data: dict[str, Any], *, request_id: str | None = None
    ) -> UserInstructions:
        return cls(
            about=_str(data.get("about")),
            style=_str(data.get("style")),
            request_id=request_id,
        )


@dataclass(frozen=True)
class OrgInstructions:
    """`GET` and `PUT /api/org/instructions`: the policy text an org admin
    sets for every member. Reading it is admin-only, as is writing it."""

    text: str = ""
    request_id: str | None = field(default=None, compare=False)

    @classmethod
    def from_dict(
        cls, data: dict[str, Any], *, request_id: str | None = None
    ) -> OrgInstructions:
        return cls(text=_str(data.get("text")), request_id=request_id)


@dataclass(frozen=True)
class ConversationInstructions:
    """The answer to `conversations.update_instructions`: the conversation's
    id and the levels now in force for it (`org`, `user`, `conversation`,
    `user_overridden`, `effective`), exactly as `conversations.get` reports
    them. The server owns the shape, so the dict is kept as sent."""

    conversation_id: str
    instructions: dict[str, Any]
    request_id: str | None = field(default=None, compare=False)

    @classmethod
    def from_dict(
        cls, data: dict[str, Any], *, request_id: str | None = None
    ) -> ConversationInstructions:
        instructions = data.get("instructions")
        return cls(
            conversation_id=_str(data.get("conversation_id")),
            instructions=dict(instructions) if isinstance(instructions, dict) else {},
            request_id=request_id,
        )


FEEDBACK_REASONS = ("wrong", "unhelpful", "incomplete", "too_long", "off_topic", "unsafe")
"""The fixed vocabulary a rating's `reasons` may draw on; the free text is
`comment`. The server refuses the rest."""


@dataclass(frozen=True)
class MessageFeedback:
    """The answer to `messages.set_feedback`: the caller's current opinion of
    one reply. A second rating replaces the first rather than piling up."""

    message_id: str
    rating: int
    reasons: list[str]
    comment: str | None
    created_at: str
    updated_at: str
    request_id: str | None = field(default=None, compare=False)

    @classmethod
    def from_dict(
        cls, data: dict[str, Any], *, request_id: str | None = None
    ) -> MessageFeedback:
        return cls(
            message_id=_str(data.get("message_id")),
            rating=_int(data.get("rating")),
            reasons=_strs(data.get("reasons")),
            comment=_opt_str(data.get("comment")),
            created_at=_str(data.get("created_at")),
            updated_at=_str(data.get("updated_at")),
            request_id=request_id,
        )


@dataclass(frozen=True)
class RemovedFeedback:
    """The answer to `messages.delete_feedback`: `removed` when there was a
    rating to take off, `none` when there was already nothing there."""

    status: str
    request_id: str | None = field(default=None, compare=False)

    @classmethod
    def from_dict(
        cls, data: dict[str, Any], *, request_id: str | None = None
    ) -> RemovedFeedback:
        return cls(status=_str(data.get("status")), request_id=request_id)


@dataclass(frozen=True)
class MessageFeedbackList:
    """`GET /api/conversations/{id}/feedback`: the caller's own ratings in
    one conversation, in creation order, so a client can redraw its thumbs
    on reload. Nobody else's rows."""

    data: list[MessageFeedback]
    request_id: str | None = field(default=None, compare=False)

    def __iter__(self) -> Any:
        return iter(self.data)

    def __len__(self) -> int:
        return len(self.data)

    @classmethod
    def from_dict(
        cls, data: dict[str, Any], *, request_id: str | None = None
    ) -> MessageFeedbackList:
        return cls(
            data=[MessageFeedback.from_dict(r) for r in _dicts(data.get("data"))],
            request_id=request_id,
        )


@dataclass(frozen=True)
class AppModel:
    """One row of the model picker (`GET /api/models`): the model, what it
    costs, and the caller's plan's relationship to it. `included` is False
    for a model the plan does not cover -- it stays listed so the picker can
    show the upgrade, and a turn on it is refused server-side -- and
    `upgrade_to` names the plan that has it. `context_window` is None when
    the gateway did not say, rather than a guess."""

    id: str
    label: str
    included: bool
    upgrade_to: str | None = None
    partner: bool = False
    research_preview: bool = False
    supports_vision: bool | None = None
    # Whether the model can call tools, so a client can grey out its agent
    # mode for one that cannot. None when the server did not say.
    supports_tools: bool | None = None
    context_window: int | None = None
    multiplier: int = 0
    input_usd_per_m: float = 0.0
    output_usd_per_m: float = 0.0

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AppModel:
        vision = data.get("supports_vision")
        tools = data.get("supports_tools")
        return cls(
            id=_str(data.get("id")),
            label=_str(data.get("label")),
            included=data.get("included") is True,
            upgrade_to=_opt_str(data.get("upgrade_to")),
            partner=data.get("partner") is True,
            research_preview=data.get("research_preview") is True,
            supports_vision=vision if isinstance(vision, bool) else None,
            supports_tools=tools if isinstance(tools, bool) else None,
            context_window=_opt_int(data.get("context_window")),
            multiplier=_int(data.get("multiplier")),
            input_usd_per_m=_float(data.get("input_usd_per_m")),
            output_usd_per_m=_float(data.get("output_usd_per_m")),
        )


@dataclass(frozen=True)
class AppModelList:
    """`GET /api/models`: the picker's rows, narrowed to what the caller's
    org switched on, with `default_model` to preselect."""

    data: list[AppModel]
    default_model: str | None = None
    object: str = "list"
    request_id: str | None = field(default=None, compare=False)

    def __iter__(self) -> Any:
        return iter(self.data)

    def __len__(self) -> int:
        return len(self.data)

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, request_id: str | None = None) -> AppModelList:
        return cls(
            data=[AppModel.from_dict(m) for m in _dicts(data.get("data"))],
            default_model=_opt_str(data.get("default_model")),
            object=_str(data.get("object"), "list"),
            request_id=request_id,
        )


@dataclass(frozen=True)
class Budget:
    """`GET /api/budget`: the org's period as of now. `included_tokens` and
    `tokens_used` are weighted tokens; the money fields are micro-dollars at
    list price, `overage_cap_micros` the org's own cap or the plan's default.
    The `member_*` fields are the caller's own cap and spend against it."""

    period_start: str
    included_tokens: int
    tokens_used: int
    remaining_tokens: int
    overage_enabled: bool
    overage_cap_micros: int
    overage_micros: int
    member_default_cap_micros: int | None = None
    member_cap: int | None = None
    member_spent: int | None = None
    member_exhausted: bool | None = None
    request_id: str | None = field(default=None, compare=False)

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, request_id: str | None = None) -> Budget:
        return cls(
            period_start=_str(data.get("period_start")),
            included_tokens=_int(data.get("included_tokens")),
            tokens_used=_int(data.get("tokens_used")),
            remaining_tokens=_int(data.get("remaining_tokens")),
            overage_enabled=data.get("overage_enabled") is True,
            overage_cap_micros=_int(data.get("overage_cap_micros")),
            overage_micros=_int(data.get("overage_micros")),
            member_default_cap_micros=_opt_int(data.get("member_default_cap_micros")),
            member_cap=_opt_int(data.get("member_cap")),
            member_spent=_opt_int(data.get("member_spent")),
            member_exhausted=(
                data.get("member_exhausted") if isinstance(data.get("member_exhausted"), bool) else None
            ),
            request_id=request_id,
        )


@dataclass(frozen=True)
class MemberBudget:
    """One member's month: the cap in force (`own` says it is the member's
    row rather than the org's default; None is unlimited) and the spend the
    usage report shows beside them. `email`, `name`, `role` and `state` are
    filled in only where the route answers rows for the whole org."""

    subject: str
    period_start: str
    cap_micros: int | None
    own: bool
    spent_micros: int
    exhausted: bool = False
    email: str | None = None
    name: str | None = None
    role: str | None = None
    state: str | None = None
    request_id: str | None = field(default=None, compare=False)

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, request_id: str | None = None) -> MemberBudget:
        return cls(
            subject=_str(data.get("subject")),
            period_start=_str(data.get("period_start")),
            cap_micros=_opt_int(data.get("cap_micros")),
            own=data.get("own") is True,
            spent_micros=_int(data.get("spent_micros")),
            exhausted=data.get("exhausted") is True,
            email=_opt_str(data.get("email")),
            name=_opt_str(data.get("name")),
            role=_opt_str(data.get("role")),
            state=_opt_str(data.get("state")),
            request_id=request_id,
        )


@dataclass(frozen=True)
class MemberBudgetList:
    """`GET /api/budget/members`, and the answer to
    `budget.set_member_default_cap`: every member with the cap in force and
    the month's spend beside them, in the members pane's order."""

    period_start: str
    member_default_cap_micros: int | None
    data: list[MemberBudget]
    request_id: str | None = field(default=None, compare=False)

    def __iter__(self) -> Any:
        return iter(self.data)

    def __len__(self) -> int:
        return len(self.data)

    @classmethod
    def from_dict(
        cls, data: dict[str, Any], *, request_id: str | None = None
    ) -> MemberBudgetList:
        return cls(
            period_start=_str(data.get("period_start")),
            member_default_cap_micros=_opt_int(data.get("member_default_cap_micros")),
            data=[MemberBudget.from_dict(r) for r in _dicts(data.get("data"))],
            request_id=request_id,
        )


# --- the bell (0.3) -----------------------------------------------------------
#
# Notifications, announcements and entitlements: the shell's bell and banner
# and the plan facts behind them. As above, the fields are the ones the
# routes send today and anything new is dropped rather than refused.


REPORT_KINDS = ("unsafe", "unhelpful")
"""The two kinds a report may name: `unsafe` routes to human review,
`unhelpful` to the product backlog (which is where most reports, refusal
complaints included, belong). The server refuses the rest."""


@dataclass(frozen=True)
class FiledReport:
    """The answer to `conversations.report`: the report's `id`, to quote to
    support, and the `kind` it was filed as. A 201 means it is stored; a
    503 means the platform could not store it, and it was not filed."""

    id: str
    kind: str
    request_id: str | None = field(default=None, compare=False)

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, request_id: str | None = None) -> FiledReport:
        return cls(
            id=_str(data.get("id")),
            kind=_str(data.get("kind")),
            request_id=request_id,
        )


@dataclass(frozen=True)
class Notification:
    """One row of the bell (`GET /api/me/notifications`): a title, an
    optional body, and `link`, the in-app path or URL the click opens.
    `read_at` is when the person saw it, None while it is unread.
    `unread_count` is set only on the answer to `notifications.read`,
    beside the row it marked."""

    id: str
    kind: str
    title: str
    body: str = ""
    link: str | None = None
    created_at: str = ""
    read_at: str | None = None
    unread_count: int | None = None
    request_id: str | None = field(default=None, compare=False)

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, request_id: str | None = None) -> Notification:
        return cls(
            id=_str(data.get("id")),
            kind=_str(data.get("kind")),
            title=_str(data.get("title")),
            body=_str(data.get("body")),
            link=_opt_str(data.get("link")),
            created_at=_str(data.get("created_at")),
            read_at=_opt_str(data.get("read_at")),
            unread_count=_opt_int(data.get("unread_count")),
            request_id=request_id,
        )


@dataclass(frozen=True)
class NotificationList:
    """`GET /api/me/notifications`: the caller's rows, newest first, with
    the unread count the bell shows. `unread=True` lists only the unread
    ones; the count is the same either way."""

    data: list[Notification]
    unread_count: int
    request_id: str | None = field(default=None, compare=False)

    def __iter__(self) -> Any:
        return iter(self.data)

    def __len__(self) -> int:
        return len(self.data)

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, request_id: str | None = None) -> NotificationList:
        return cls(
            data=[Notification.from_dict(n) for n in _dicts(data.get("data"))],
            unread_count=_int(data.get("unread_count")),
            request_id=request_id,
        )


@dataclass(frozen=True)
class NotificationsReadAll:
    """The answer to `notifications.read_all`: how many unread rows were
    marked read, and the bell's count now -- zero, whatever it was."""

    read: int
    unread_count: int
    request_id: str | None = field(default=None, compare=False)

    @classmethod
    def from_dict(
        cls, data: dict[str, Any], *, request_id: str | None = None
    ) -> NotificationsReadAll:
        return cls(
            read=_int(data.get("read")),
            unread_count=_int(data.get("unread_count")),
            request_id=request_id,
        )


@dataclass(frozen=True)
class Announcement:
    """One row of the banner (`GET /api/announcements`): an operator's
    message with a severity (`critical`, `warning` or `info`) and its
    window. Only the active ones -- inside their window now -- are listed,
    most severe first. Critical ones are never dismissible."""

    id: str
    message: str
    severity: str
    link: str | None
    starts_at: str
    ends_at: str | None
    dismissible: bool
    request_id: str | None = field(default=None, compare=False)

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, request_id: str | None = None) -> Announcement:
        return cls(
            id=_str(data.get("id")),
            message=_str(data.get("message")),
            severity=_str(data.get("severity")),
            link=_opt_str(data.get("link")),
            starts_at=_str(data.get("starts_at")),
            ends_at=_opt_str(data.get("ends_at")),
            dismissible=data.get("dismissible") is True,
            request_id=request_id,
        )


@dataclass(frozen=True)
class AnnouncementList:
    """`GET /api/announcements`: the active announcements, most severe first."""

    data: list[Announcement]
    request_id: str | None = field(default=None, compare=False)

    def __iter__(self) -> Any:
        return iter(self.data)

    def __len__(self) -> int:
        return len(self.data)

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, request_id: str | None = None) -> AnnouncementList:
        return cls(
            data=[Announcement.from_dict(a) for a in _dicts(data.get("data"))],
            request_id=request_id,
        )


@dataclass(frozen=True)
class AdminAnnouncement:
    """One announcement as the operator's tab sees it (`GET
    /api/admin/announcements`): every column -- who wrote it and who last
    changed it, window edges included -- plus `state`, where the window
    stands now: `active`, `scheduled` or `ended`. The banner everyone
    signed in reads is `Announcement`, the public fields only."""

    id: str
    message: str
    severity: str
    link: str | None
    starts_at: str
    ends_at: str | None
    dismissible: bool
    created_by: str
    created_at: str
    updated_by: str
    updated_at: str
    state: str
    request_id: str | None = field(default=None, compare=False)

    @classmethod
    def from_dict(
        cls, data: dict[str, Any], *, request_id: str | None = None
    ) -> AdminAnnouncement:
        return cls(
            id=_str(data.get("id")),
            message=_str(data.get("message")),
            severity=_str(data.get("severity")),
            link=_opt_str(data.get("link")),
            starts_at=_str(data.get("starts_at")),
            ends_at=_opt_str(data.get("ends_at")),
            dismissible=data.get("dismissible") is True,
            created_by=_str(data.get("created_by")),
            created_at=_str(data.get("created_at")),
            updated_by=_str(data.get("updated_by")),
            updated_at=_str(data.get("updated_at")),
            state=_str(data.get("state")),
            request_id=request_id,
        )


@dataclass(frozen=True)
class AdminAnnouncementList:
    """`GET /api/admin/announcements`: every announcement, newest first."""

    data: list[AdminAnnouncement]
    request_id: str | None = field(default=None, compare=False)

    def __iter__(self) -> Any:
        return iter(self.data)

    def __len__(self) -> int:
        return len(self.data)

    @classmethod
    def from_dict(
        cls, data: dict[str, Any], *, request_id: str | None = None
    ) -> AdminAnnouncementList:
        return cls(
            data=[AdminAnnouncement.from_dict(a) for a in _dicts(data.get("data"))],
            request_id=request_id,
        )


@dataclass(frozen=True)
class EntitlementLimits:
    """The `limits` of `GET /api/entitlements`, with the fields the plan
    resolver computes. Every `None` limit is unlimited, not zero; the
    convention is the caller's to apply, exactly as the route leaves it.
    `features` is the plan's feature flags, kept as sent."""

    included_tokens: int
    included_tokens_per_seat: int | None
    overage_enabled: bool
    budget_duration: str
    tpm_limit: int | None
    rpm_limit: int | None
    max_parallel_requests: int | None
    max_conversations: int | None
    max_seats: int | None
    allowed_models: list[str] | None
    features: dict[str, Any]

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EntitlementLimits:
        return cls(
            included_tokens=_int(data.get("included_tokens")),
            included_tokens_per_seat=_opt_int(data.get("included_tokens_per_seat")),
            overage_enabled=data.get("overage_enabled") is True,
            budget_duration=_str(data.get("budget_duration")),
            tpm_limit=_opt_int(data.get("tpm_limit")),
            rpm_limit=_opt_int(data.get("rpm_limit")),
            max_parallel_requests=_opt_int(data.get("max_parallel_requests")),
            max_conversations=_opt_int(data.get("max_conversations")),
            max_seats=_opt_int(data.get("max_seats")),
            allowed_models=_opt_strs(data.get("allowed_models")),
            features=_dict(data.get("features")),
        )


@dataclass(frozen=True)
class Entitlements:
    """`GET /api/entitlements`: what the caller's org is allowed to do --
    the plan, its prices and allowance, the seat counts, and, for an org a
    downgrade left over its conversation cap, `writes_blocked` with
    `read_only_reason` saying so (reads never stop; new conversations do).
    Any member may read it, like `/api/budget`."""

    org_id: str
    plan: str
    plan_name: str
    status: str
    seats: int
    per_seat: bool
    billed_seats: int
    price_cents_monthly: int
    price_cents_annual: int
    implicit: bool
    billing_interval: str
    current_period_end: str | None
    cancel_at_period_end: bool
    trial_end: str | None
    default_overage_cap_micros: int
    limits: EntitlementLimits
    over_capacity: int
    writes_blocked: bool
    read_only_reason: str | None
    dunning_stage: str
    request_id: str | None = field(default=None, compare=False)

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, request_id: str | None = None) -> Entitlements:
        return cls(
            org_id=_str(data.get("org_id")),
            plan=_str(data.get("plan")),
            plan_name=_str(data.get("plan_name")),
            status=_str(data.get("status"), "active"),
            seats=_int(data.get("seats"), 1),
            per_seat=data.get("per_seat") is True,
            billed_seats=_int(data.get("billed_seats"), 1),
            price_cents_monthly=_int(data.get("price_cents_monthly")),
            price_cents_annual=_int(data.get("price_cents_annual")),
            implicit=data.get("implicit") is True,
            billing_interval=_str(data.get("billing_interval"), "monthly"),
            current_period_end=_opt_str(data.get("current_period_end")),
            cancel_at_period_end=data.get("cancel_at_period_end") is True,
            trial_end=_opt_str(data.get("trial_end")),
            default_overage_cap_micros=_int(data.get("default_overage_cap_micros")),
            limits=EntitlementLimits.from_dict(_dict(data.get("limits"))),
            over_capacity=_int(data.get("over_capacity")),
            writes_blocked=data.get("writes_blocked") is True,
            read_only_reason=_opt_str(data.get("read_only_reason")),
            dunning_stage=_str(data.get("dunning_stage"), "clear"),
            request_id=request_id,
        )


# --- vision -------------------------------------------------------------------
#
# Which models accept images, mirrored from the web app's vision.ts, which
# reads the same `GET /api/models` rows. The tri-state is the API's
# `supports_vision: true | false | null`, where null means the gateway did
# not say.

VisionVerdict = Literal["yes", "no", "unknown"]


def vision_of(models: Sequence[Any] | None, model_id: str | None) -> VisionVerdict:
    """What the model list says about `model_id`'s image input, as one of
    `"yes"`, `"no"` or `"unknown"`.

    Gate BEFORE the upload: the point of the check is that the person learns
    their screenshot will not be sent while they are still choosing the
    file, not after they have waited out a 5 MB upload and a spinner. The
    server's own 422 ("The model ... does not accept image input") is the
    authority and is not replaced by this -- it simply arrives too late to
    be good UX.

    `"no"` is the only refusing verdict. `model_id=None` (the picker's
    "Default") is `"unknown"`, because which model that resolves to is the
    server's business; a model absent from the list is `"unknown"`, not
    `"no"`, because the picker and the gateway can disagree transiently and
    that must not become a refusal; and `supports_vision=None` is
    `"unknown"`, because a bare llama.cpp or vLLM build serves a model list
    with nothing but `id` and treating silence as "no" would make
    attachments dead there. The cost of a wrong "yes" is the upstream error
    the caller would have got anyway; the cost of a wrong "no" is a capable
    model nobody can use.

    `models` is `app_models().data`, or the raw `/api/models` `data` rows as
    dicts; anything with `id` and `supports_vision` attributes works.
    """
    if model_id is None or models is None:
        return "unknown"
    for entry in models:
        if isinstance(entry, dict):
            found = entry.get("id") == model_id
            supports = entry.get("supports_vision")
        else:
            found = getattr(entry, "id", None) == model_id
            supports = getattr(entry, "supports_vision", None)
        if not found:
            continue
        if supports is True:
            return "yes"
        if supports is False:
            return "no"
        return "unknown"
    return "unknown"


# --- the account families (0.5) ------------------------------------------------
#
# Account self-service, export/import jobs, members, org management, billing,
# passkeys, MFA, account switching, speech and edge pairing: the remaining
# routes the web app's settings panes speak. As above, the fields are the
# ones the routes send today and anything new is dropped rather than refused.


ORG_ROLES = ("owner", "admin", "member", "viewer")
"""The four roles a membership may carry. Ownership moves only between
owners, and nobody demotes or removes the last owner."""


@dataclass(frozen=True)
class SessionInfo:
    """One live sign-in (`GET /api/me/sessions`). `current` flags the
    session that made the request; `user_agent` and `ip` are what the sign-in
    travelled with, so a device the person no longer trusts is findable."""

    id: str
    current: bool
    created_at: str
    last_seen_at: str
    user_agent: str
    ip: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SessionInfo:
        ip = data.get("ip")
        return cls(
            id=_str(data.get("id")),
            current=data.get("current") is True,
            created_at=_str(data.get("created_at")),
            last_seen_at=_str(data.get("last_seen_at")),
            user_agent=_str(data.get("user_agent")),
            ip=ip if isinstance(ip, str) else None,
        )


@dataclass(frozen=True)
class SessionList:
    """`GET /api/me/sessions`: the caller's live sessions. `current_id` is
    None for an API caller with no cookie, and then nothing is flagged."""

    current_id: str | None
    data: list[SessionInfo]
    request_id: str | None = field(default=None, compare=False)

    def __iter__(self) -> Any:
        return iter(self.data)

    def __len__(self) -> int:
        return len(self.data)

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, request_id: str | None = None) -> SessionList:
        return cls(
            current_id=_opt_str(data.get("current_id")),
            data=[SessionInfo.from_dict(s) for s in _dicts(data.get("sessions"))],
            request_id=request_id,
        )


@dataclass(frozen=True)
class SessionRevoked:
    """The answer to `account.sessions.revoke`. `current` is True when the
    revoked row was the session that made the request -- "sign out"."""

    status: str
    current: bool
    request_id: str | None = field(default=None, compare=False)

    @classmethod
    def from_dict(
        cls, data: dict[str, Any], *, request_id: str | None = None
    ) -> SessionRevoked:
        return cls(
            status=_str(data.get("status")),
            current=data.get("current") is True,
            request_id=request_id,
        )


@dataclass(frozen=True)
class SessionsLoggedOut:
    """The answer to `account.sessions.logout_others`: how many other live
    sessions were ended. The one making the request keeps going."""

    revoked: int
    request_id: str | None = field(default=None, compare=False)

    @classmethod
    def from_dict(
        cls, data: dict[str, Any], *, request_id: str | None = None
    ) -> SessionsLoggedOut:
        return cls(revoked=_int(data.get("revoked")), request_id=request_id)


@dataclass(frozen=True)
class EmailPreferences:
    """The mail toggles. `billing_receipts` is transactional mail: the
    server sends it as True in every read and refuses a write that tries to
    turn it off."""

    product_updates: bool = False
    usage_alerts: bool = True
    billing_receipts: bool = True

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EmailPreferences:
        return cls(
            product_updates=data.get("product_updates") is True,
            usage_alerts=data.get("usage_alerts") is not False,
            billing_receipts=data.get("billing_receipts") is not False,
        )


@dataclass(frozen=True)
class NotificationPreferences:
    budget_warn: bool = True

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> NotificationPreferences:
        return cls(budget_warn=data.get("budget_warn") is not False)


@dataclass(frozen=True)
class Preferences:
    """`GET /api/me/preferences`: the person's mail toggles and locale. The
    locale is a BCP-47 tag (None when unset); the server validates the whole
    shape on a write, so an unknown locale is its 422."""

    email: EmailPreferences = field(default_factory=EmailPreferences)
    notifications: NotificationPreferences = field(default_factory=NotificationPreferences)
    locale: str | None = None
    request_id: str | None = field(default=None, compare=False)

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, request_id: str | None = None) -> Preferences:
        email = data.get("email")
        notifications = data.get("notifications")
        return cls(
            email=EmailPreferences.from_dict(email if isinstance(email, dict) else {}),
            notifications=(
                NotificationPreferences.from_dict(notifications)
                if isinstance(notifications, dict)
                else NotificationPreferences()
            ),
            locale=_opt_str(data.get("locale")),
            request_id=request_id,
        )


@dataclass(frozen=True)
class NameUpdated:
    """The answer to `account.rename`."""

    name: str
    request_id: str | None = field(default=None, compare=False)

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, request_id: str | None = None) -> NameUpdated:
        return cls(name=_str(data.get("name")), request_id=request_id)


@dataclass(frozen=True)
class TermsAccepted:
    """The answer to `account.accept_terms`: the version now on record and
    when it was accepted. `terms_current` is always True on a 200."""

    terms_version: str
    terms_accepted_at: str | None
    terms_current: bool
    request_id: str | None = field(default=None, compare=False)

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, request_id: str | None = None) -> TermsAccepted:
        return cls(
            terms_version=_str(data.get("terms_version")),
            terms_accepted_at=_opt_str(data.get("terms_accepted_at")),
            terms_current=data.get("terms_current") is True,
            request_id=request_id,
        )


@dataclass(frozen=True)
class PasswordChanged:
    """The answer to `account.change_password`: every other session was
    ended, and `revoked` counts them."""

    status: str
    revoked: int
    request_id: str | None = field(default=None, compare=False)

    @classmethod
    def from_dict(
        cls, data: dict[str, Any], *, request_id: str | None = None
    ) -> PasswordChanged:
        return cls(
            status=_str(data.get("status"), "changed"),
            revoked=_int(data.get("revoked")),
            request_id=request_id,
        )


@dataclass(frozen=True)
class EmailChangeStarted:
    """The answer to `account.email.start`: a confirmation code was mailed
    to `new_email`, which signs in nothing until `email.verify` takes it
    back. `mail_sent` is False when the relay refused; the code step can
    still be retried with `email.resend`."""

    status: str
    new_email: str
    mail_sent: bool | None
    request_id: str | None = field(default=None, compare=False)

    @classmethod
    def from_dict(
        cls, data: dict[str, Any], *, request_id: str | None = None
    ) -> EmailChangeStarted:
        mail_sent = data.get("mail_sent")
        return cls(
            status=_str(data.get("status")),
            new_email=_str(data.get("new_email")),
            mail_sent=mail_sent if isinstance(mail_sent, bool) else None,
            request_id=request_id,
        )


@dataclass(frozen=True)
class EmailChangeState:
    """`GET /api/me/email`: the sign-in address and any change still waiting
    for its code, so a client can come back to the code step after a reload."""

    email: str | None
    pending_email: str | None
    request_id: str | None = field(default=None, compare=False)

    @classmethod
    def from_dict(
        cls, data: dict[str, Any], *, request_id: str | None = None
    ) -> EmailChangeState:
        return cls(
            email=_opt_str(data.get("email")),
            pending_email=_opt_str(data.get("pending_email")),
            request_id=request_id,
        )


@dataclass(frozen=True)
class EmailChanged:
    """The answer to `account.email.verify`: the new address is the login,
    every other session was ended, and the old address was told. The
    requesting session keeps going."""

    status: str
    email: str
    revoked: int
    notice_sent: bool | None
    request_id: str | None = field(default=None, compare=False)

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, request_id: str | None = None) -> EmailChanged:
        notice_sent = data.get("notice_sent")
        return cls(
            status=_str(data.get("status"), "changed"),
            email=_str(data.get("email")),
            revoked=_int(data.get("revoked")),
            notice_sent=notice_sent if isinstance(notice_sent, bool) else None,
            request_id=request_id,
        )


@dataclass(frozen=True)
class EmailChangeCancelled:
    """The answer to `account.email.cancel`: the mailed code stops working
    and the address stays what it was. Harmless when nothing was pending."""

    status: str
    email: str
    request_id: str | None = field(default=None, compare=False)

    @classmethod
    def from_dict(
        cls, data: dict[str, Any], *, request_id: str | None = None
    ) -> EmailChangeCancelled:
        return cls(
            status=_str(data.get("status"), "cancelled"),
            email=_str(data.get("email")),
            request_id=request_id,
        )


@dataclass(frozen=True)
class OnboardingCompleted:
    """The answer to `account.onboarding_complete`. Idempotent: a second
    call keeps the first `completed_at`."""

    onboarding_completed: bool
    completed_at: str
    request_id: str | None = field(default=None, compare=False)

    @classmethod
    def from_dict(
        cls, data: dict[str, Any], *, request_id: str | None = None
    ) -> OnboardingCompleted:
        return cls(
            onboarding_completed=data.get("onboarding_completed") is True,
            completed_at=_str(data.get("completed_at")),
            request_id=request_id,
        )


@dataclass(frozen=True)
class DeletedAccount:
    """The answer to `account.delete`: what the deletion took with it.
    Conversations and attachments are soft-deleted -- rows now, bytes on the
    sweeper's next pass within `retention_days`. `identity_removed` is False
    when the data is gone but the login is not, which the caller must say
    rather than smooth over."""

    status: str
    conversations_deleted: int
    attachments_marked_deleted: int
    api_keys_revoked: int
    custom_instructions_deleted: bool
    retention_days: int
    identity_removed: bool
    deletion_request_id: str | None = None
    request_id: str | None = field(default=None, compare=False)

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, request_id: str | None = None) -> DeletedAccount:
        identity = data.get("identity_removed")
        return cls(
            status=_str(data.get("status"), "deleted"),
            conversations_deleted=_int(data.get("conversations_deleted")),
            attachments_marked_deleted=_int(data.get("attachments_marked_deleted")),
            api_keys_revoked=_int(data.get("api_keys_revoked")),
            custom_instructions_deleted=data.get("custom_instructions_deleted") is True,
            retention_days=_int(data.get("retention_days"), 30),
            identity_removed=identity is True if isinstance(identity, bool) else False,
            deletion_request_id=_opt_str(data.get("deletion_request_id")),
            request_id=request_id,
        )


# --- export and import jobs ----------------------------------------------------


@dataclass(frozen=True)
class ExportJob:
    """One account export (`POST` then `GET /api/me/export/{id}`). It is
    built by the platform's worker; `download_url` appears with the status
    `ready` and is a signed path that lasts until `expires_at`. The bytes
    are only reachable by the owner: pass the job (or its URL) to
    `exports.download`, which sends the caller's own credentials too."""

    id: str
    status: str
    created_at: str
    size_bytes: int | None = None
    completed_at: str | None = None
    expires_at: str | None = None
    download_url: str | None = field(default=None, repr=False)
    request_id: str | None = field(default=None, compare=False)

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, request_id: str | None = None) -> ExportJob:
        return cls(
            id=_str(data.get("id")),
            status=_str(data.get("status")),
            created_at=_str(data.get("created_at")),
            size_bytes=_opt_int(data.get("size_bytes")),
            completed_at=_opt_str(data.get("completed_at")),
            expires_at=_opt_str(data.get("expires_at")),
            download_url=_opt_str(data.get("download_url")),
            request_id=request_id,
        )


@dataclass(frozen=True)
class ImportJob:
    """One ChatGPT/Claude history import, from start to `completed` (or
    `failed`, with `error`). The counts say what the worker found and what
    it made; `branches_dropped` counts message branches the target's
    linear model cannot hold."""

    id: str
    status: str
    source: str
    filename: str
    size_bytes: int
    conversations_total: int
    conversations_done: int
    conversations_created: int
    conversations_existing: int
    conversations_skipped: int
    messages_created: int
    attachments_skipped: int
    branches_dropped: int
    created_at: str
    error: str | None = None
    started_at: str | None = None
    completed_at: str | None = None
    request_id: str | None = field(default=None, compare=False)

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, request_id: str | None = None) -> ImportJob:
        return cls(
            id=_str(data.get("id")),
            status=_str(data.get("status")),
            source=_str(data.get("source")),
            filename=_str(data.get("filename")),
            size_bytes=_int(data.get("size_bytes")),
            conversations_total=_int(data.get("conversations_total")),
            conversations_done=_int(data.get("conversations_done")),
            conversations_created=_int(data.get("conversations_created")),
            conversations_existing=_int(data.get("conversations_existing")),
            conversations_skipped=_int(data.get("conversations_skipped")),
            messages_created=_int(data.get("messages_created")),
            attachments_skipped=_int(data.get("attachments_skipped")),
            branches_dropped=_int(data.get("branches_dropped")),
            created_at=_str(data.get("created_at")),
            error=_opt_str(data.get("error")),
            started_at=_opt_str(data.get("started_at")),
            completed_at=_opt_str(data.get("completed_at")),
            request_id=request_id,
        )


@dataclass(frozen=True)
class ImportJobList:
    """`GET /api/me/imports`: the caller's recent imports, newest first."""

    data: list[ImportJob]
    request_id: str | None = field(default=None, compare=False)

    def __iter__(self) -> Any:
        return iter(self.data)

    def __len__(self) -> int:
        return len(self.data)

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, request_id: str | None = None) -> ImportJobList:
        return cls(
            data=[ImportJob.from_dict(r) for r in _dicts(data.get("data"))],
            request_id=request_id,
        )


# --- members, invites, invite links, join ----------------------------------------


@dataclass(frozen=True)
class Membership:
    """One person's place in the org, as the members pane lists it. `state`
    is `active`, `suspended` or `unverified`; `last_seen_at` is None for
    someone who has never signed in."""

    subject: str
    email: str
    role: str
    state: str
    created_at: str
    name: str | None = None
    last_seen_at: str | None = None
    request_id: str | None = field(default=None, compare=False)

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, request_id: str | None = None) -> Membership:
        return cls(
            subject=_str(data.get("subject")),
            email=_str(data.get("email")),
            role=_str(data.get("role")),
            state=_str(data.get("state"), "active"),
            created_at=_str(data.get("created_at")),
            name=_opt_str(data.get("name")),
            last_seen_at=_opt_str(data.get("last_seen_at")),
            request_id=request_id,
        )


@dataclass(frozen=True)
class Seats:
    """The plan's room against what is taken: members plus pending invites.
    `max` is None with no cap; `billed` is the seats the org pays for on a
    plan sold per seat (its members, without the invites)."""

    used: int
    max: int | None = None
    billed: int | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Seats:
        return cls(
            used=_int(data.get("used")),
            max=_opt_int(data.get("max")),
            billed=_opt_int(data.get("billed")),
        )


@dataclass(frozen=True)
class MemberList:
    """`GET /api/org/members`: every member with their role, `role` being
    the caller's own (the token's claims say nothing about the org), and
    `seats` the plan's room."""

    data: list[Membership]
    role: str
    seats: Seats
    request_id: str | None = field(default=None, compare=False)

    def __iter__(self) -> Any:
        return iter(self.data)

    def __len__(self) -> int:
        return len(self.data)

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, request_id: str | None = None) -> MemberList:
        seats = data.get("seats")
        return cls(
            data=[Membership.from_dict(m) for m in _dicts(data.get("data"))],
            role=_str(data.get("role")),
            seats=Seats.from_dict(seats if isinstance(seats, dict) else {}),
            request_id=request_id,
        )


@dataclass(frozen=True)
class Invite:
    """A mailed invitation, as the invites pane lists it. Never the token:
    the plaintext went out in the mail and nowhere else. `status` is
    `pending`, `accepted`, `expired` or `revoked`."""

    id: str
    email: str
    role: str
    invited_by: str
    created_at: str
    expires_at: str
    status: str
    request_id: str | None = field(default=None, compare=False)

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, request_id: str | None = None) -> Invite:
        return cls(
            id=_str(data.get("id")),
            email=_str(data.get("email")),
            role=_str(data.get("role")),
            invited_by=_str(data.get("invited_by")),
            created_at=_str(data.get("created_at")),
            expires_at=_str(data.get("expires_at")),
            status=_str(data.get("status")),
            request_id=request_id,
        )


@dataclass(frozen=True)
class InviteList:
    """`GET /api/org/invites`: the org's invitations."""

    data: list[Invite]
    request_id: str | None = field(default=None, compare=False)

    def __iter__(self) -> Any:
        return iter(self.data)

    def __len__(self) -> int:
        return len(self.data)

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, request_id: str | None = None) -> InviteList:
        return cls(
            data=[Invite.from_dict(i) for i in _dicts(data.get("data"))],
            request_id=request_id,
        )


@dataclass(frozen=True)
class SentInvite:
    """The answer to `invites.create` and `invites.resend`: the invite plus
    whether its mail went. A relay failure does not undo the row -- `mail`
    is `"failed"` with `detail` saying so, and a resend is the recovery."""

    id: str
    email: str
    role: str
    invited_by: str
    created_at: str
    expires_at: str
    status: str
    mail: str | None = None
    detail: str | None = None
    request_id: str | None = field(default=None, compare=False)

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, request_id: str | None = None) -> SentInvite:
        return cls(
            id=_str(data.get("id")),
            email=_str(data.get("email")),
            role=_str(data.get("role")),
            invited_by=_str(data.get("invited_by")),
            created_at=_str(data.get("created_at")),
            expires_at=_str(data.get("expires_at")),
            status=_str(data.get("status")),
            mail=_opt_str(data.get("mail")),
            detail=_opt_str(data.get("detail")),
            request_id=request_id,
        )


@dataclass(frozen=True)
class InvitePreview:
    """What an anonymous lookup may know about a mailed invite: enough to
    say "join Acme as a member" and pre-fill the address, nothing about who
    else is in the org."""

    org_name: str
    email: str
    role: str
    expires_at: str
    status: str
    request_id: str | None = field(default=None, compare=False)

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, request_id: str | None = None) -> InvitePreview:
        return cls(
            org_name=_str(data.get("org_name")),
            email=_str(data.get("email")),
            role=_str(data.get("role")),
            expires_at=_str(data.get("expires_at")),
            status=_str(data.get("status")),
            request_id=request_id,
        )


@dataclass(frozen=True)
class InviteLink:
    """A join link, as the pane lists it. Never the token: only its hash is
    kept, so the plaintext exists in the response that made the link and
    wherever its owner pasted it. `status` is `active`, `used_up`,
    `expired` or `revoked`."""

    id: str
    role: str
    max_uses: int
    uses: int
    created_by: str
    created_at: str
    expires_at: str
    status: str
    request_id: str | None = field(default=None, compare=False)

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, request_id: str | None = None) -> InviteLink:
        return cls(
            id=_str(data.get("id")),
            role=_str(data.get("role")),
            max_uses=_int(data.get("max_uses")),
            uses=_int(data.get("uses")),
            created_by=_str(data.get("created_by")),
            created_at=_str(data.get("created_at")),
            expires_at=_str(data.get("expires_at")),
            status=_str(data.get("status")),
            request_id=request_id,
        )


@dataclass(frozen=True)
class InviteLinkList:
    """`GET /api/org/invite-links`: the links not revoked, with their uses."""

    data: list[InviteLink]
    request_id: str | None = field(default=None, compare=False)

    def __iter__(self) -> Any:
        return iter(self.data)

    def __len__(self) -> int:
        return len(self.data)

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, request_id: str | None = None) -> InviteLinkList:
        return cls(
            data=[InviteLink.from_dict(link) for link in _dicts(data.get("data"))],
            request_id=request_id,
        )


@dataclass(frozen=True)
class CreatedInviteLink:
    """A new link. `token` and `url` are in this answer and nowhere else --
    a lost link is revoked and replaced, never shown again."""

    link: InviteLink
    token: str = field(repr=False)
    url: str = field(repr=False)
    request_id: str | None = field(default=None, compare=False)

    @classmethod
    def from_dict(
        cls, data: dict[str, Any], *, request_id: str | None = None
    ) -> CreatedInviteLink:
        return cls(
            link=InviteLink.from_dict(data),
            token=_str(data.get("token")),
            url=_str(data.get("url")),
            request_id=request_id,
        )


@dataclass(frozen=True)
class LinkPreview:
    """What an anonymous lookup may know about a join link: whose org,
    which role, and whether the link still works -- not how many have used
    it."""

    org_name: str
    role: str
    expires_at: str
    status: str
    request_id: str | None = field(default=None, compare=False)

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, request_id: str | None = None) -> LinkPreview:
        return cls(
            org_name=_str(data.get("org_name")),
            role=_str(data.get("role")),
            expires_at=_str(data.get("expires_at")),
            status=_str(data.get("status")),
            request_id=request_id,
        )


# --- org management ---------------------------------------------------------------


@dataclass(frozen=True)
class OrgInfo:
    """`GET /api/org`: the organisation and the caller's place in it. Any
    member may read it."""

    id: str
    name: str
    created_at: str
    role: str
    request_id: str | None = field(default=None, compare=False)

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, request_id: str | None = None) -> OrgInfo:
        return cls(
            id=_str(data.get("id")),
            name=_str(data.get("name")),
            created_at=_str(data.get("created_at")),
            role=_str(data.get("role")),
            request_id=request_id,
        )


@dataclass(frozen=True)
class OrgClosed:
    """The answer to `org.close`: the closure plus what each side effect
    reported, kept honest rather than assumed. A None count means that step
    could not be confirmed; `subscription` names what happened to the Stripe
    subscription; `identity_removed` is False when the IdP still holds the
    org. A repeat by the owner keeps the first closure and re-runs the side
    effects, which is how a Stripe or IdP failure is retried."""

    status: str
    closed_at: str
    already_closed: bool
    conversations_deleted: int
    retention_days: int
    members_mailed: int
    members_not_mailed: int
    api_keys_revoked: int | None = None
    invites_revoked: int | None = None
    invite_links_revoked: int | None = None
    subscription: str | None = None
    identity_removed: bool | None = None
    request_id: str | None = field(default=None, compare=False)

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, request_id: str | None = None) -> OrgClosed:
        identity = data.get("identity_removed")
        return cls(
            status=_str(data.get("status"), "closed"),
            closed_at=_str(data.get("closed_at")),
            already_closed=data.get("already_closed") is True,
            conversations_deleted=_int(data.get("conversations_deleted")),
            retention_days=_int(data.get("retention_days"), 30),
            members_mailed=_int(data.get("members_mailed")),
            members_not_mailed=_int(data.get("members_not_mailed")),
            api_keys_revoked=_opt_int(data.get("api_keys_revoked")),
            invites_revoked=_opt_int(data.get("invites_revoked")),
            invite_links_revoked=_opt_int(data.get("invite_links_revoked")),
            subscription=_opt_str(data.get("subscription")),
            identity_removed=identity if isinstance(identity, bool) else None,
            request_id=request_id,
        )


@dataclass(frozen=True)
class ModelPolicyRow:
    """One row of the model allow-list pane: a model the plan permits, the
    org's switch on it, and whether the switch can be turned off at all
    (the house model is always allowed, whatever is sent)."""

    id: str
    label: str
    allowed: bool
    locked: bool = False
    partner: bool = False
    research_preview: bool = False
    context_window: int | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ModelPolicyRow:
        context_window = data.get("context_window")
        return cls(
            id=_str(data.get("id")),
            label=_str(data.get("label")),
            allowed=data.get("allowed") is True,
            locked=data.get("locked") is True,
            partner=data.get("partner") is True,
            research_preview=data.get("research_preview") is True,
            context_window=(
                context_window
                if isinstance(context_window, int) and not isinstance(context_window, bool)
                else None
            ),
        )


@dataclass(frozen=True)
class ModelPolicy:
    """`GET /api/org/models`: the plan's models with the org's switch on
    each. `restricted` is False while every model is allowed; `unlisted`
    names ids the org switched off that the plan or the gateway no longer
    offers -- harmless, but a stale entry an admin can clear."""

    house_model: str
    data: list[ModelPolicyRow]
    unlisted: list[str]
    restricted: bool
    request_id: str | None = field(default=None, compare=False)

    def __iter__(self) -> Any:
        return iter(self.data)

    def __len__(self) -> int:
        return len(self.data)

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, request_id: str | None = None) -> ModelPolicy:
        return cls(
            house_model=_str(data.get("house_model")),
            data=[ModelPolicyRow.from_dict(m) for m in _dicts(data.get("data"))],
            unlisted=_strs(data.get("unlisted")),
            restricted=data.get("restricted") is True,
            request_id=request_id,
        )


@dataclass(frozen=True)
class SharingPolicy:
    """`GET /api/org/sharing`: whether members may share conversations by
    link. Switching it off stops every existing link answering at once,
    without revoking them; switching it back on brings them back."""

    enabled: bool
    request_id: str | None = field(default=None, compare=False)

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, request_id: str | None = None) -> SharingPolicy:
        return cls(enabled=data.get("enabled") is True, request_id=request_id)


@dataclass(frozen=True)
class OrgShare:
    """One share link as the org's admins see it: every link any member
    made, with `created_by`. Never the tokens -- the store does not hold
    them. Revoke is `org.shares.revoke`."""

    id: str
    conversation_id: str
    title: str
    message_count: int
    created_at: str
    view_count: int
    status: str
    created_by: str
    include_attachments: bool = False
    expires_at: str | None = None
    revoked_at: str | None = None
    request_id: str | None = field(default=None, compare=False)

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, request_id: str | None = None) -> OrgShare:
        return cls(
            id=_str(data.get("id")),
            conversation_id=_str(data.get("conversation_id")),
            title=_str(data.get("title")),
            message_count=_int(data.get("message_count")),
            created_at=_str(data.get("created_at")),
            view_count=_int(data.get("view_count")),
            status=_str(data.get("status")),
            created_by=_str(data.get("created_by")),
            include_attachments=data.get("include_attachments") is True,
            expires_at=_opt_str(data.get("expires_at")),
            revoked_at=_opt_str(data.get("revoked_at")),
            request_id=request_id,
        )


@dataclass(frozen=True)
class OrgShareList:
    """`GET /api/org/shares`: every link the org's members made, newest
    first, with who made each."""

    data: list[OrgShare]
    request_id: str | None = field(default=None, compare=False)

    def __iter__(self) -> Any:
        return iter(self.data)

    def __len__(self) -> int:
        return len(self.data)

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, request_id: str | None = None) -> OrgShareList:
        return cls(
            data=[OrgShare.from_dict(s) for s in _dicts(data.get("data"))],
            request_id=request_id,
        )


@dataclass(frozen=True)
class RevokedOrgShare:
    """The answer to `org.shares.revoke`."""

    status: str
    request_id: str | None = field(default=None, compare=False)

    @classmethod
    def from_dict(
        cls, data: dict[str, Any], *, request_id: str | None = None
    ) -> RevokedOrgShare:
        return cls(status=_str(data.get("status"), "revoked"), request_id=request_id)


@dataclass(frozen=True)
class SsoSettings:
    """`GET /api/org/sso`: whether SSO is enforced, plus `blockers`, what
    would stop it being turned on -- the preflight runs on the read, so the
    pane can say why the switch is unavailable before it is flipped."""

    sso_required: bool
    blockers: list[dict[str, Any]]
    request_id: str | None = field(default=None, compare=False)

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, request_id: str | None = None) -> SsoSettings:
        blockers = data.get("blockers")
        return cls(
            sso_required=data.get("sso_required") is True,
            blockers=_dicts(blockers),
            request_id=request_id,
        )


@dataclass(frozen=True)
class RecoveryCode:
    """A break-glass sign-in code for an SSO lock-out. `code` is in this
    answer and nowhere else; a mint supersedes any unused one."""

    id: str
    created_at: str
    expires_at: str
    created_by: str
    used_at: str | None = None
    code: str | None = field(default=None, repr=False)
    shown_once: bool | None = None
    request_id: str | None = field(default=None, compare=False)

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, request_id: str | None = None) -> RecoveryCode:
        shown_once = data.get("shown_once")
        return cls(
            id=_str(data.get("id")),
            created_at=_str(data.get("created_at")),
            expires_at=_str(data.get("expires_at")),
            created_by=_str(data.get("created_by")),
            used_at=_opt_str(data.get("used_at")),
            code=_opt_str(data.get("code")),
            shown_once=shown_once if isinstance(shown_once, bool) else None,
            request_id=request_id,
        )


@dataclass(frozen=True)
class SsoPolicySet:
    """The answer to `org.sso.set`. Enforcing answers with the minted
    `recovery` code -- there is no way into the enforced state without being
    handed the way back out; relaxing answers without one."""

    sso_required: bool
    recovery: RecoveryCode | None
    request_id: str | None = field(default=None, compare=False)

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, request_id: str | None = None) -> SsoPolicySet:
        recovery = data.get("recovery")
        return cls(
            sso_required=data.get("sso_required") is True,
            recovery=(
                RecoveryCode.from_dict(recovery) if isinstance(recovery, dict) else None
            ),
            request_id=request_id,
        )


@dataclass(frozen=True)
class DnsRecord:
    """The TXT record an org publishes to prove it owns a domain."""

    type: str
    host: str
    name: str
    value: str

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DnsRecord:
        return cls(
            type=_str(data.get("type"), "TXT"),
            host=_str(data.get("host")),
            name=_str(data.get("name")),
            value=_str(data.get("value")),
        )


@dataclass(frozen=True)
class DomainChallenge:
    """`GET /api/org/domains/{domain}/challenge`: the record to publish.
    Stable once minted, so a mid-propagation re-open shows the same one."""

    record: DnsRecord
    request_id: str | None = field(default=None, compare=False)

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, request_id: str | None = None) -> DomainChallenge:
        record = data.get("record")
        return cls(
            record=DnsRecord.from_dict(record if isinstance(record, dict) else {}),
            request_id=request_id,
        )


@dataclass(frozen=True)
class DomainClaim:
    """One claimed domain: the claim, and whether it has been proven by the
    DNS check (which decides whose address gets the SSO button and where a
    JIT-provisioned user lands -- a security boundary, not a form field)."""

    domain: str
    verified: bool
    created_at: str
    verified_at: str | None = None
    verified_by: str | None = None
    request_id: str | None = field(default=None, compare=False)

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, request_id: str | None = None) -> DomainClaim:
        return cls(
            domain=_str(data.get("domain")),
            verified=data.get("verified") is True,
            created_at=_str(data.get("created_at")),
            verified_at=_opt_str(data.get("verified_at")),
            verified_by=_opt_str(data.get("verified_by")),
            request_id=request_id,
        )


@dataclass(frozen=True)
class DomainClaimList:
    """`GET /api/org/domains`: the org's claimed domains."""

    data: list[DomainClaim]
    request_id: str | None = field(default=None, compare=False)

    def __iter__(self) -> Any:
        return iter(self.data)

    def __len__(self) -> int:
        return len(self.data)

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, request_id: str | None = None) -> DomainClaimList:
        return cls(
            data=[DomainClaim.from_dict(d) for d in _dicts(data.get("data"))],
            request_id=request_id,
        )


@dataclass(frozen=True)
class DomainVerification:
    """The answer to `org.domains.verify`: the claim's state after one DNS
    check. `verified` True is proven; False with `detail` means the record
    is not there yet (202 -- publish it, or wait for propagation), which is
    why `found` names what the resolver actually saw."""

    domain: str
    verified: bool
    verified_at: str | None
    verified_by: str | None
    method: str | None
    last_checked_at: str | None
    last_error: str | None
    record: DnsRecord
    detail: str | None = None
    found: list[str] = field(default_factory=list)
    request_id: str | None = field(default=None, compare=False)

    @classmethod
    def from_dict(
        cls, data: dict[str, Any], *, request_id: str | None = None
    ) -> DomainVerification:
        record = data.get("record")
        return cls(
            domain=_str(data.get("domain")),
            verified=data.get("verified") is True,
            verified_at=_opt_str(data.get("verified_at")),
            verified_by=_opt_str(data.get("verified_by")),
            method=_opt_str(data.get("method")),
            last_checked_at=_opt_str(data.get("last_checked_at")),
            last_error=_opt_str(data.get("last_error")),
            record=DnsRecord.from_dict(record if isinstance(record, dict) else {}),
            detail=_opt_str(data.get("detail")),
            found=_strs(data.get("found")),
            request_id=request_id,
        )


@dataclass(frozen=True)
class IdpConfig:
    """One attached identity provider, as the settings pane lists it.
    Never the client secret: it went to the IdP and is not stored here."""

    provider: str
    kind: str
    name: str
    issuer: str | None
    created_at: str
    request_id: str | None = field(default=None, compare=False)

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, request_id: str | None = None) -> IdpConfig:
        return cls(
            provider=_str(data.get("provider")),
            kind=_str(data.get("kind")),
            name=_str(data.get("name")),
            issuer=_opt_str(data.get("issuer")),
            created_at=_str(data.get("created_at")),
            request_id=request_id,
        )


@dataclass(frozen=True)
class IdpConfigList:
    """`GET /api/org/idp`: the org's identity providers."""

    data: list[IdpConfig]
    request_id: str | None = field(default=None, compare=False)

    def __iter__(self) -> Any:
        return iter(self.data)

    def __len__(self) -> int:
        return len(self.data)

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, request_id: str | None = None) -> IdpConfigList:
        return cls(
            data=[IdpConfig.from_dict(i) for i in _dicts(data.get("data"))],
            request_id=request_id,
        )


@dataclass(frozen=True)
class MfaPolicy:
    """`GET /api/org/mfa`: whether every member must have an authenticator.
    Applies to password sign-in; a member arriving through the org's own
    SSO provider has that provider's factors."""

    mfa_required: bool
    request_id: str | None = field(default=None, compare=False)

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, request_id: str | None = None) -> MfaPolicy:
        return cls(mfa_required=data.get("mfa_required") is True, request_id=request_id)


# --- plans and billing --------------------------------------------------------------


@dataclass(frozen=True)
class Plan:
    """One plan of the public catalog (`GET /api/plans`), cheapest first.
    On a plan sold per seat the price, the allowance and the default overage
    cap are each per seat, and the seats share one pool. `models` names the
    model ids the plan includes; `limits` is the plan's limits object,
    kept as the server shapes it."""

    code: str
    name: str
    price_cents_monthly: int
    price_cents_annual: int
    included_tokens: int
    overage_enabled: bool
    default_overage_cap_micros: int
    trial_days: int
    per_seat: bool = False
    included_tokens_per_seat: int | None = None
    models: list[str] = field(default_factory=list)
    limits: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Plan:
        return cls(
            code=_str(data.get("code")),
            name=_str(data.get("name")),
            price_cents_monthly=_int(data.get("price_cents_monthly")),
            price_cents_annual=_int(data.get("price_cents_annual")),
            included_tokens=_int(data.get("included_tokens")),
            overage_enabled=data.get("overage_enabled") is True,
            default_overage_cap_micros=_int(data.get("default_overage_cap_micros")),
            trial_days=_int(data.get("trial_days")),
            per_seat=data.get("per_seat") is True,
            included_tokens_per_seat=_opt_int(data.get("included_tokens_per_seat")),
            models=_strs(data.get("models")),
            limits=_dict(data.get("limits")),
        )


@dataclass(frozen=True)
class PlanCatalog:
    """`GET /api/plans`: the catalog and, beside it, the price table -- the
    label, multiplier and list price per million tokens for every model
    sold, kept as sent -- and the `default_model`."""

    data: list[Plan]
    models: list[dict[str, Any]]
    default_model: str
    request_id: str | None = field(default=None, compare=False)

    def __iter__(self) -> Any:
        return iter(self.data)

    def __len__(self) -> int:
        return len(self.data)

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, request_id: str | None = None) -> PlanCatalog:
        return cls(
            data=[Plan.from_dict(p) for p in _dicts(data.get("data"))],
            models=_dicts(data.get("models")),
            default_model=_str(data.get("default_model")),
            request_id=request_id,
        )


@dataclass(frozen=True)
class PlanHistoryEvent:
    """One row of the append-only billing trail (`GET /api/plan/history`),
    newest first: which plan and status moved to which, who or what moved it
    (a person's subject, or `stripe:<event id>`), and the reason given."""

    to_plan: str
    to_status: str
    kind: str
    actor: str
    created_at: str
    from_plan: str | None = None
    from_status: str | None = None
    reason: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PlanHistoryEvent:
        return cls(
            to_plan=_str(data.get("to_plan")),
            to_status=_str(data.get("to_status")),
            kind=_str(data.get("kind")),
            actor=_str(data.get("actor")),
            created_at=_str(data.get("created_at")),
            from_plan=_opt_str(data.get("from_plan")),
            from_status=_opt_str(data.get("from_status")),
            reason=_opt_str(data.get("reason")),
            metadata=_dict(data.get("metadata")),
        )


@dataclass(frozen=True)
class PlanHistory:
    data: list[PlanHistoryEvent]
    request_id: str | None = field(default=None, compare=False)

    def __iter__(self) -> Any:
        return iter(self.data)

    def __len__(self) -> int:
        return len(self.data)

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, request_id: str | None = None) -> PlanHistory:
        return cls(
            data=[PlanHistoryEvent.from_dict(r) for r in _dicts(data.get("data"))],
            request_id=request_id,
        )


@dataclass(frozen=True)
class CheckoutLink:
    """The Stripe URL to redirect the browser to, from `billing.checkout`
    (start a subscription) or `billing.portal` (card, plan, cancel)."""

    url: str
    request_id: str | None = field(default=None, compare=False)

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, request_id: str | None = None) -> CheckoutLink:
        return cls(url=_str(data.get("url")), request_id=request_id)


@dataclass(frozen=True)
class BillingStatus:
    """`GET /api/billing/status`: what a billing page renders, in one read.
    `entitlements` is the plan facts any member may read; `billing_enabled`
    says whether the buy/manage buttons should exist at all, `collection_*`
    how the org pays, and `dunning` the late-payment schedule when it is
    late (None when it is not)."""

    entitlements: Entitlements
    billing_enabled: bool
    collection_method: str | None
    days_until_due: int | None
    dunning: dict[str, Any] | None
    request_id: str | None = field(default=None, compare=False)

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, request_id: str | None = None) -> BillingStatus:
        dunning = data.get("dunning")
        collection = data.get("collection_method")
        days = data.get("days_until_due")
        return cls(
            entitlements=Entitlements.from_dict(data),
            billing_enabled=data.get("billing_enabled") is True,
            collection_method=collection if isinstance(collection, str) else None,
            days_until_due=(
                days if isinstance(days, int) and not isinstance(days, bool) else None
            ),
            dunning=dict(dunning) if isinstance(dunning, dict) else None,
            request_id=request_id,
        )


@dataclass(frozen=True)
class Invoice:
    """One invoice of the org's, newest first in `billing.invoices`:
    status and amounts, and Stripe's hosted page and PDF links, which is
    where an invoice is paid."""

    id: str
    number: str
    status: str
    collection_method: str
    currency: str
    amount_due_micros: int
    amount_remaining_micros: int
    issued_at: str
    due_at: str | None = None
    paid_at: str | None = None
    hosted_invoice_url: str | None = None
    invoice_pdf: str | None = None
    request_id: str | None = field(default=None, compare=False)

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, request_id: str | None = None) -> Invoice:
        return cls(
            id=_str(data.get("id")),
            number=_str(data.get("number")),
            status=_str(data.get("status")),
            collection_method=_str(data.get("collection_method")),
            currency=_str(data.get("currency")),
            amount_due_micros=_int(data.get("amount_due_micros")),
            amount_remaining_micros=_int(data.get("amount_remaining_micros")),
            issued_at=_str(data.get("issued_at")),
            due_at=_opt_str(data.get("due_at")),
            paid_at=_opt_str(data.get("paid_at")),
            hosted_invoice_url=_opt_str(data.get("hosted_invoice_url")),
            invoice_pdf=_opt_str(data.get("invoice_pdf")),
            request_id=request_id,
        )


@dataclass(frozen=True)
class InvoiceList:
    """`GET /api/billing/invoices`: the org's invoices, newest first."""

    data: list[Invoice]
    request_id: str | None = field(default=None, compare=False)

    def __iter__(self) -> Any:
        return iter(self.data)

    def __len__(self) -> int:
        return len(self.data)

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, request_id: str | None = None) -> InvoiceList:
        return cls(
            data=[Invoice.from_dict(i) for i in _dicts(data.get("data"))],
            request_id=request_id,
        )


# --- passkeys, MFA, account switching, speech -----------------------------------------


@dataclass(frozen=True)
class Passkey:
    """One registered passkey, as the Security pane lists it. The
    credential itself lives at the identity provider; this is the name and
    the date."""

    id: str
    name: str
    created_at: str
    request_id: str | None = field(default=None, compare=False)

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, request_id: str | None = None) -> Passkey:
        return cls(
            id=_str(data.get("id")),
            name=_str(data.get("name")),
            created_at=_str(data.get("created_at")),
            request_id=request_id,
        )


@dataclass(frozen=True)
class PasskeyList:
    """`GET /api/me/passkeys`: the account's passkeys, the relying-party id
    a browser registration is bound to, and the per-account maximum."""

    data: list[Passkey]
    rp_id: str
    max: int
    request_id: str | None = field(default=None, compare=False)

    def __iter__(self) -> Any:
        return iter(self.data)

    def __len__(self) -> int:
        return len(self.data)

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, request_id: str | None = None) -> PasskeyList:
        return cls(
            data=[Passkey.from_dict(p) for p in _dicts(data.get("passkeys"))],
            rp_id=_str(data.get("rp_id")),
            max=_int(data.get("max"), 10),
            request_id=request_id,
        )


@dataclass(frozen=True)
class PasskeyStart:
    """`POST /api/me/passkeys/start`: the id to register under and the
    creation options for `navigator.credentials.create`. Nothing is live
    until `passkeys.verify` takes the attestation back."""

    passkey_id: str
    options: dict[str, Any]
    request_id: str | None = field(default=None, compare=False)

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, request_id: str | None = None) -> PasskeyStart:
        options = data.get("options")
        return cls(
            passkey_id=_str(data.get("passkey_id")),
            options=dict(options) if isinstance(options, dict) else {},
            request_id=request_id,
        )


@dataclass(frozen=True)
class PasskeyLoginStart:
    """`POST /api/auth/passkey/start`: the request options for
    `navigator.credentials.get` and the sealed `challenge` the finish step
    takes back. The challenge carries the provider's session, sealed; it is
    opaque to the caller and shown here as received."""

    challenge: str = field(repr=False)
    options: dict[str, Any]
    request_id: str | None = field(default=None, compare=False)

    @classmethod
    def from_dict(
        cls, data: dict[str, Any], *, request_id: str | None = None
    ) -> PasskeyLoginStart:
        options = data.get("options")
        return cls(
            challenge=_str(data.get("challenge")),
            options=dict(options) if isinstance(options, dict) else {},
            request_id=request_id,
        )


@dataclass(frozen=True)
class PasskeyLoginFinish:
    """`POST /api/auth/passkey/finish`: the redirect that completes the
    sign-in. A browser flow; an API client has little use for it, but the
    route is here for parity."""

    redirect: str
    request_id: str | None = field(default=None, compare=False)

    @classmethod
    def from_dict(
        cls, data: dict[str, Any], *, request_id: str | None = None
    ) -> PasskeyLoginFinish:
        return cls(redirect=_str(data.get("redirect")), request_id=request_id)


@dataclass(frozen=True)
class MfaState:
    """`GET /api/me/mfa`: whether an authenticator is confirmed, how many
    recovery codes are unspent, and whether the org insists on one (which
    is why Remove is then unavailable)."""

    totp_enrolled: bool
    recovery_codes_left: int
    org_requires_mfa: bool
    request_id: str | None = field(default=None, compare=False)

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, request_id: str | None = None) -> MfaState:
        return cls(
            totp_enrolled=data.get("totp_enrolled") is True,
            recovery_codes_left=_int(data.get("recovery_codes_left")),
            org_requires_mfa=data.get("org_requires_mfa") is True,
            request_id=request_id,
        )


@dataclass(frozen=True)
class TotpEnrolment:
    """`POST /api/me/mfa/totp/start`: the secret, the otpauth URI and its
    QR as a `data:image/svg+xml` URI. Nothing is live until the first code
    confirms it with `totp_confirm`."""

    secret: str = field(repr=False)
    uri: str = field(repr=False)
    qr_svg: str
    request_id: str | None = field(default=None, compare=False)

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, request_id: str | None = None) -> TotpEnrolment:
        return cls(
            secret=_str(data.get("secret")),
            uri=_str(data.get("uri")),
            qr_svg=_str(data.get("qr_svg")),
            request_id=request_id,
        )


@dataclass(frozen=True)
class TotpConfirmed:
    """The answer to `mfa.totp_confirm` and `mfa.recovery_reissue`: the ten
    recovery codes, shown once -- only their hashes exist after this
    response. Store them now."""

    recovery_codes: list[str] = field(repr=False, default_factory=list)
    recovery_codes_left: int = 0
    totp_enrolled: bool | None = None
    request_id: str | None = field(default=None, compare=False)

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, request_id: str | None = None) -> TotpConfirmed:
        enrolled = data.get("totp_enrolled")
        return cls(
            recovery_codes=_strs(data.get("recovery_codes")),
            recovery_codes_left=_int(data.get("recovery_codes_left")),
            totp_enrolled=enrolled if isinstance(enrolled, bool) else None,
            request_id=request_id,
        )


@dataclass(frozen=True)
class TotpRemoved:
    """The answer to `mfa.totp_remove`: the authenticator and its recovery
    codes are gone."""

    totp_enrolled: bool
    recovery_codes_left: int
    request_id: str | None = field(default=None, compare=False)

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, request_id: str | None = None) -> TotpRemoved:
        return cls(
            totp_enrolled=data.get("totp_enrolled") is True,
            recovery_codes_left=_int(data.get("recovery_codes_left")),
            request_id=request_id,
        )


@dataclass(frozen=True)
class BrowserAccount:
    """One account signed in on a browser, as `GET /api/accounts` offers
    them, acting one first. `slot` 0 is the acting account, 1 and up are
    parked. Only what the session envelopes already told that browser --
    never account data."""

    slot: int
    current: bool
    subject: str
    email: str | None = None
    name: str | None = None
    org_name: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BrowserAccount:
        return cls(
            slot=_int(data.get("slot")),
            current=data.get("current") is True,
            subject=_str(data.get("subject")),
            email=_opt_str(data.get("email")),
            name=_opt_str(data.get("name")),
            org_name=_opt_str(data.get("org_name")),
        )


@dataclass(frozen=True)
class BrowserAccountList:
    """`GET /api/accounts`: who is signed in on this browser, acting one
    first, whether there is `room` for another, and the per-browser `max`."""

    data: list[BrowserAccount]
    max: int
    room: bool
    request_id: str | None = field(default=None, compare=False)

    def __iter__(self) -> Any:
        return iter(self.data)

    def __len__(self) -> int:
        return len(self.data)

    @classmethod
    def from_dict(
        cls, data: dict[str, Any], *, request_id: str | None = None
    ) -> BrowserAccountList:
        return cls(
            data=[BrowserAccount.from_dict(a) for a in _dicts(data.get("accounts"))],
            max=_int(data.get("max"), 4),
            room=data.get("room") is True,
            request_id=request_id,
        )


@dataclass(frozen=True)
class AccountSwitched:
    """The answer to `accounts.switch`: which account now acts."""

    status: str
    subject: str
    email: str | None
    request_id: str | None = field(default=None, compare=False)

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, request_id: str | None = None) -> AccountSwitched:
        return cls(
            status=_str(data.get("status"), "ok"),
            subject=_str(data.get("subject")),
            email=_opt_str(data.get("email")),
            request_id=request_id,
        )


@dataclass(frozen=True)
class LoggedOut:
    """The answer to `accounts.logout` and `accounts.logout_all`.
    `end_session_endpoint` is the identity provider's, when there is one,
    for the browser to finish at."""

    status: str
    end_session_endpoint: str | None = None
    request_id: str | None = field(default=None, compare=False)

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, request_id: str | None = None) -> LoggedOut:
        return cls(
            status=_str(data.get("status"), "ok"),
            end_session_endpoint=_opt_str(data.get("end_session_endpoint")),
            request_id=request_id,
        )


@dataclass(frozen=True)
class Transcription:
    """`POST /api/speech/transcribe`: one clip's words. The audio is held
    for the length of the request and nowhere else -- not logged, not
    stored."""

    text: str
    request_id: str | None = field(default=None, compare=False)

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, request_id: str | None = None) -> Transcription:
        return cls(text=_str(data.get("text")), request_id=request_id)


# --- edge pairing and sync ----------------------------------------------------------


@dataclass(frozen=True)
class PairingCode:
    """`POST /api/sync/pairings`: a code an edge box redeems once for its
    key. Twelve characters shown paired-dashed, ten minutes to live."""

    code: str
    name: str
    expires_in: int
    expires_at: str
    request_id: str | None = field(default=None, compare=False)

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, request_id: str | None = None) -> PairingCode:
        return cls(
            code=_str(data.get("code")),
            name=_str(data.get("name")),
            expires_in=_int(data.get("expires_in"), 600),
            expires_at=_str(data.get("expires_at")),
            request_id=request_id,
        )


@dataclass(frozen=True)
class EdgeDevice:
    """One paired box: its key (never the value) and the name it was
    paired under. Revoked ones are listed too -- they are the record of an
    unpairing."""

    id: str
    name: str
    box_name: str
    prefix: str
    kind: str
    scopes: list[str]
    created_at: str
    expires_at: str | None = None
    revoked_at: str | None = None
    last_used_at: str | None = None
    request_id: str | None = field(default=None, compare=False)

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, request_id: str | None = None) -> EdgeDevice:
        return cls(
            id=_str(data.get("id")),
            name=_str(data.get("name")),
            box_name=_str(data.get("box_name")),
            prefix=_str(data.get("prefix")),
            kind=_str(data.get("kind")),
            scopes=_strs(data.get("scopes")),
            created_at=_str(data.get("created_at")),
            expires_at=_opt_str(data.get("expires_at")),
            revoked_at=_opt_str(data.get("revoked_at")),
            last_used_at=_opt_str(data.get("last_used_at")),
            request_id=request_id,
        )


@dataclass(frozen=True)
class EdgeDeviceList:
    """`GET /api/sync/devices`: the org's paired boxes."""

    data: list[EdgeDevice]
    request_id: str | None = field(default=None, compare=False)

    def __iter__(self) -> Any:
        return iter(self.data)

    def __len__(self) -> int:
        return len(self.data)

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, request_id: str | None = None) -> EdgeDeviceList:
        return cls(
            data=[EdgeDevice.from_dict(d) for d in _dicts(data.get("data"))],
            request_id=request_id,
        )


@dataclass(frozen=True)
class PairedKey:
    """`POST /api/sync/pair`: the box's key, minted at redemption. The
    plaintext `key` is in this answer and nowhere else; the box stores it
    and pushes with it."""

    key: str = field(repr=False)
    key_id: str
    name: str
    scopes: list[str]
    request_id: str | None = field(default=None, compare=False)

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, request_id: str | None = None) -> PairedKey:
        return cls(
            key=_str(data.get("key")),
            key_id=_str(data.get("key_id")),
            name=_str(data.get("name")),
            scopes=_strs(data.get("scopes")),
            request_id=request_id,
        )


@dataclass(frozen=True)
class EdgeSyncStatus:
    """`GET /api/sync/status`: what the box's sync job last did -- counts,
    times and the stop reason, never content. `box` is False and `paired`
    False on a process that is not an edge box at all (a platform pod)."""

    box: bool
    paired: bool
    device_id: str | None = None
    platform_url: str | None = None
    name: str | None = None
    paired_at: str | None = None
    last_synced_at: str | None = None
    last_attempt_at: str | None = None
    offline: bool | None = None
    pending: int | None = None
    pending_messages: int | None = None
    pending_capped: bool | None = None
    stopped: str | None = None
    last_error: str | None = None
    skipped: int | None = None
    skipped_recent: int | None = None
    next_attempt_at: str | None = None
    request_id: str | None = field(default=None, compare=False)

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, request_id: str | None = None) -> EdgeSyncStatus:
        return cls(
            box=data.get("box") is True,
            paired=data.get("paired") is True,
            device_id=_opt_str(data.get("device_id")),
            platform_url=_opt_str(data.get("platform_url")),
            name=_opt_str(data.get("name")),
            paired_at=_opt_str(data.get("paired_at")),
            last_synced_at=_opt_str(data.get("last_synced_at")),
            last_attempt_at=_opt_str(data.get("last_attempt_at")),
            offline=(
                data.get("offline") if isinstance(data.get("offline"), bool) else None
            ),
            pending=_opt_int(data.get("pending")),
            pending_messages=_opt_int(data.get("pending_messages")),
            pending_capped=(
                data.get("pending_capped")
                if isinstance(data.get("pending_capped"), bool)
                else None
            ),
            stopped=_opt_str(data.get("stopped")),
            last_error=_opt_str(data.get("last_error")),
            skipped=_opt_int(data.get("skipped")),
            skipped_recent=_opt_int(data.get("skipped_recent")),
            next_attempt_at=_opt_str(data.get("next_attempt_at")),
            request_id=request_id,
        )


@dataclass(frozen=True)
class SyncOutcome:
    """`POST /api/sync/conversations`: the platform's answer to one pushed
    conversation. `accepted + updated + already_present` equal to what was
    sent is the acknowledgement; anything else is named in `rejected` by
    message id."""

    conversation_id: str
    accepted: int
    updated: int
    already_present: int
    rejected: list[dict[str, Any]]
    request_id: str | None = field(default=None, compare=False)

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, request_id: str | None = None) -> SyncOutcome:
        return cls(
            conversation_id=_str(data.get("conversation_id")),
            accepted=_int(data.get("accepted")),
            updated=_int(data.get("updated")),
            already_present=_int(data.get("already_present")),
            rejected=_dicts(data.get("rejected")),
            request_id=request_id,
        )
