"""The MCP client: MatterWave's `/mcp` endpoint, Streamable-HTTP JSON-RPC,
as `client.mcp`. It is the conversation API in a second wire format -- every
tool is the same route the web app and the SDK reach -- so a script or an
agent can list and continue a person's conversations without speaking the
protocol itself.

    tools = client.mcp.list_tools()
    answer = client.mcp.send_message(conversation_id, "Draft an agenda.")
    print(answer.text)

Two protocol eras share the endpoint, and `protocol_version` picks one.

* The handshake revisions (`2025-03-26`, `2025-06-18`, `2025-11-25`; the
  default is `2025-06-18`): the wrapper opens with `initialize` once per
  version and answers with whatever the server echoes -- a version it has
  never heard of is answered with `2025-06-18`.
* The modern revision (`2026-07-28`): no handshake, no session; each request
  carries `params._meta` naming the protocol version and the client's
  capabilities, mirrored into `MCP-Protocol-Version`, `Mcp-Method` and
  `Mcp-Name` headers that must agree with the body.

One POST per message, one JSON object back. Authentication is the client's
own API key as a bearer, and the per-key rate limit is the one `/v1` has, so
its 429 raises the ordinary `RateLimitError`. A refusal before any method
runs is likewise an `APIError` family member by HTTP status. A tool that ran
and refused is different: it arrives as a *result* with `isError: true` and
a `structuredContent.error`, and the wrapper raises `McpToolError` so the
reason (`budget`, `not_found`, `upgrade_required`, `moderation`) is code and
the prose is `detail`.
"""

from __future__ import annotations

import base64
import itertools
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from . import _base
from ._base import Request
from ._version import __version__
from .errors import McpError, McpToolError

if TYPE_CHECKING:
    from ._client import AsyncMatterWave, MatterWave

# The protocol's own constants, as the server spells them.
META_PROTOCOL_VERSION = "io.modelcontextprotocol/protocolVersion"
META_CLIENT_CAPABILITIES = "io.modelcontextprotocol/clientCapabilities"

#: The handshake revisions, and the newest of them a client asking for
#: something unknown is answered with.
HANDSHAKE_VERSIONS: tuple[str, ...] = ("2025-03-26", "2025-06-18", "2025-11-25")
DEFAULT_HANDSHAKE_VERSION = "2025-06-18"
#: The per-request-envelope revisions: no handshake, `_meta` on every call.
MODERN_VERSIONS: tuple[str, ...] = ("2026-07-28",)

PROTOCOL_VERSION_HEADER = "MCP-Protocol-Version"
METHOD_HEADER = "Mcp-Method"
NAME_HEADER = "Mcp-Name"
#: Methods whose named parameter is mirrored into `Mcp-Name`.
NAME_BEARING_METHODS: dict[str, str] = {"tools/call": "name", "resources/read": "uri"}
#: `Mcp-Name` values that are not header-safe travel base64-encoded between
#: these markers, the same sentinels the server decodes.
B64_PREFIX, B64_SUFFIX = "=?base64?", "?="

#: `send_message` runs a whole model turn; the client's 60s default is not
#: the right bound for it. Ten minutes is the long-poll the route earns.
SEND_MESSAGE_TIMEOUT_S = 600.0

CLIENT_INFO = {"name": "matterwave-sdk", "version": __version__}


def _name_header(value: str) -> str:
    """`Mcp-Name` as the wire wants it: as-is when every byte is printable
    ASCII, base64 between the server's sentinels otherwise, so a tool name
    or a `conversation://` URI in any script survives the hop."""
    if value.isascii() and all(0x20 <= ord(c) <= 0x7E for c in value):
        return value
    encoded = base64.b64encode(value.encode("utf-8")).decode("ascii")
    return f"{B64_PREFIX}{encoded}{B64_SUFFIX}"


def _rpc_error(error: Any) -> McpError:
    """The JSON-RPC error object a response carried, as an exception."""
    if not isinstance(error, dict):
        return McpError("The MCP endpoint answered with a malformed error.")
    data = error.get("data")
    return McpError(str(error.get("message") or "The MCP request failed."), code=_code(error), data=data)


def _code(error: dict[str, Any]) -> int | None:
    value = error.get("code")
    return value if isinstance(value, int) and not isinstance(value, bool) else None


@dataclass(frozen=True)
class McpTool:
    """One tool `tools/list` offers: the server only names the ones the
    key's scopes allow, so a read-only key is never shown a write."""

    name: str
    title: str
    description: str
    input_schema: dict[str, Any]
    annotations: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> McpTool:
        title = data.get("title")
        annotations = data.get("annotations")
        return cls(
            name=_str_of(data.get("name")),
            title=title if isinstance(title, str) else "",
            description=_str_of(data.get("description")),
            input_schema=dict(data["inputSchema"]) if isinstance(data.get("inputSchema"), dict) else {},
            annotations=dict(annotations) if isinstance(annotations, dict) else {},
        )


def _str_of(value: Any) -> str:
    return value if isinstance(value, str) else ""


@dataclass(frozen=True)
class McpToolResult:
    """A tool's answer: the text blocks joined, the structured content ({}
    when the tool sent none), and the raw result as it arrived."""

    text: str
    structured: dict[str, Any]
    raw: dict[str, Any]


@dataclass(frozen=True)
class SendMessageResult:
    """`send_message` read the model's stream to its end. `text` is the
    whole answer; `message` is the saved assistant message as the server
    summarised it; `notices` are the turn's asides (a compaction, a title
    the server chose); `truncated` says the token limit cut the answer."""

    conversation_id: str
    text: str
    message: dict[str, Any] | None
    title: str | None
    notices: list[str]
    truncated: bool


class Mcp:
    """The blocking MCP client, on top of `MatterWave`'s key, transport,
    retry loop and error family."""

    def __init__(self, client: MatterWave) -> None:
        self._client = client
        self._initialized: set[str] = set()
        self._ids = itertools.count(1)

    # -- the protocol -------------------------------------------------------

    def initialize(self, *, protocol_version: str = DEFAULT_HANDSHAKE_VERSION) -> dict[str, Any]:
        """The handshake revision's opener: propose `protocolVersion`, get
        the server's answer back -- its info, capabilities and
        instructions. The server issues no session id and keeps no state,
        so there is nothing here to hold either."""
        if protocol_version not in HANDSHAKE_VERSIONS:
            raise ValueError(
                f"initialize speaks the handshake revisions "
                f"{', '.join(HANDSHAKE_VERSIONS)}, got {protocol_version!r}"
            )
        result = self._rpc(
            "initialize",
            {
                "protocolVersion": protocol_version,
                "capabilities": {},
                "clientInfo": CLIENT_INFO,
            },
            protocol_version,
        )
        self._initialized.add(protocol_version)
        return result

    def list_tools(self, *, protocol_version: str = DEFAULT_HANDSHAKE_VERSION) -> list[McpTool]:
        """The tools this key may call: the server hides one whose scope the
        key lacks, so a `chat:read` key is shown no write and the model on
        the other side never plans on it."""
        result = self._rpc("tools/list", {}, protocol_version)
        tools = result.get("tools")
        return [McpTool.from_dict(t) for t in tools if isinstance(t, dict)] if isinstance(tools, list) else []

    def call_tool(
        self,
        name: str,
        arguments: dict[str, Any] | None = None,
        *,
        protocol_version: str = DEFAULT_HANDSHAKE_VERSION,
        timeout: float | None = None,
    ) -> McpToolResult:
        """Run one tool. A refusal the *tool* decided on -- the budget, the
        plan, a conversation that is not this tenant's -- raises
        `McpToolError`; a refusal before the method ran (auth, the key's
        rate limit) raises the ordinary `APIError` family by status."""
        result = self._rpc(
            "tools/call",
            {"name": name, "arguments": dict(arguments or {})},
            protocol_version,
            timeout=timeout,
            named=name,
        )
        return _tool_result(name, result)

    def send_message(
        self,
        conversation_id: str,
        content: str,
        *,
        model: str | None = None,
        protocol_version: str = DEFAULT_HANDSHAKE_VERSION,
        timeout: float = SEND_MESSAGE_TIMEOUT_S,
    ) -> SendMessageResult:
        """The one long tool: append the message and get the whole answer
        back, the model's stream read to its end. `timeout` is the bound on
        that wait (ten minutes by default, not the client's 60s)."""
        arguments: dict[str, Any] = {"conversation_id": conversation_id, "content": content}
        if model is not None:
            arguments["model"] = model
        result = self.call_tool("send_message", arguments, protocol_version=protocol_version, timeout=timeout)
        structured = result.structured
        notices = structured.get("notices")
        return SendMessageResult(
            conversation_id=_str_of(structured.get("conversation_id")) or conversation_id,
            text=result.text,
            message=_dict_of(structured.get("message")),
            title=_opt_str_of(structured.get("title")),
            notices=[n for n in notices if isinstance(n, str)] if isinstance(notices, list) else [],
            truncated=structured.get("truncated") is True,
        )

    # -- the wire -----------------------------------------------------------

    def _rpc(
        self,
        method: str,
        params: dict[str, Any],
        protocol_version: str,
        *,
        timeout: float | None = None,
        named: str | None = None,
    ) -> dict[str, Any]:
        if not protocol_version:
            raise ValueError("protocol_version must be a non-empty revision string")
        if (
            method != "initialize"
            and protocol_version in HANDSHAKE_VERSIONS
            and protocol_version not in self._initialized
        ):
            # Handshake era: the opener goes first, once per version, and
            # its answer is the server's business, not ours to keep.
            self.initialize(protocol_version=protocol_version)
        body: dict[str, Any] = {
            "jsonrpc": "2.0",
            "id": next(self._ids),
            "method": method,
            "params": self._params(params, protocol_version),
        }
        request = Request(
            "POST",
            "/mcp",
            body,
            keyed=True,
            stream=False,
            headers=self._headers(method, named, protocol_version),
            timeout=timeout,
        )
        response = self._client._send(request)
        payload = _base.parse_json_object(response)
        if "error" in payload:
            # The handshake era puts every JSON-RPC error in a 200; the
            # modern one gives them HTTP statuses the retry loop has
            # already turned into the APIError family.
            raise _rpc_error(payload.get("error"))
        result = payload.get("result")
        return dict(result) if isinstance(result, dict) else {}

    @staticmethod
    def _params(params: dict[str, Any], protocol_version: str) -> dict[str, Any]:
        if protocol_version not in MODERN_VERSIONS:
            return params
        # Modern era: every request is self-contained -- the protocol
        # version and the client's capabilities ride in `_meta`, mirrored
        # into the headers `_headers` sets beside them.
        meta = dict(params.get("_meta") or {})
        meta[META_PROTOCOL_VERSION] = protocol_version
        meta[META_CLIENT_CAPABILITIES] = {}
        return {**params, "_meta": meta}

    @staticmethod
    def _headers(method: str, named: str | None, protocol_version: str) -> dict[str, str]:
        if protocol_version not in MODERN_VERSIONS:
            return {}
        headers = {PROTOCOL_VERSION_HEADER: protocol_version, METHOD_HEADER: method}
        name_key = NAME_BEARING_METHODS.get(method)
        if name_key is not None and named is not None:
            headers[NAME_HEADER] = _name_header(named)
        return headers


def _tool_result(name: str, result: dict[str, Any]) -> McpToolResult:
    structured = result.get("structuredContent")
    structured = dict(structured) if isinstance(structured, dict) else {}
    text = "".join(
        block.get("text", "")
        for block in result.get("content", [])
        if isinstance(block, dict) and block.get("type") == "text"
    )
    if result.get("isError") is True:
        error = structured.get("error")
        error = dict(error) if isinstance(error, dict) else {}
        code = error.get("code")
        raise McpToolError(
            str(error.get("detail") or text or "The tool refused."),
            tool=name,
            error_code=str(code) if code is not None else "tool_error",
            detail=str(error.get("detail") or text or "The tool refused."),
            request_id=_opt_str_of(error.get("request_id")),
            status=_opt_int_of(error.get("status")),
            error=error or None,
        )
    return McpToolResult(text=text, structured=structured, raw=result)


def _dict_of(value: Any) -> dict[str, Any] | None:
    return dict(value) if isinstance(value, dict) else None


def _opt_str_of(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def _opt_int_of(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


class AsyncMcp:
    """The awaitable MCP client; the same surface as `Mcp`, every call a
    coroutine."""

    def __init__(self, client: AsyncMatterWave) -> None:
        self._client = client
        self._initialized: set[str] = set()
        self._ids = itertools.count(1)

    async def initialize(self, *, protocol_version: str = DEFAULT_HANDSHAKE_VERSION) -> dict[str, Any]:
        if protocol_version not in HANDSHAKE_VERSIONS:
            raise ValueError(
                f"initialize speaks the handshake revisions "
                f"{', '.join(HANDSHAKE_VERSIONS)}, got {protocol_version!r}"
            )
        result = await self._rpc(
            "initialize",
            {
                "protocolVersion": protocol_version,
                "capabilities": {},
                "clientInfo": CLIENT_INFO,
            },
            protocol_version,
        )
        self._initialized.add(protocol_version)
        return result

    async def list_tools(self, *, protocol_version: str = DEFAULT_HANDSHAKE_VERSION) -> list[McpTool]:
        result = await self._rpc("tools/list", {}, protocol_version)
        tools = result.get("tools")
        return [McpTool.from_dict(t) for t in tools if isinstance(t, dict)] if isinstance(tools, list) else []

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any] | None = None,
        *,
        protocol_version: str = DEFAULT_HANDSHAKE_VERSION,
        timeout: float | None = None,
    ) -> McpToolResult:
        result = await self._rpc(
            "tools/call",
            {"name": name, "arguments": dict(arguments or {})},
            protocol_version,
            timeout=timeout,
            named=name,
        )
        return _tool_result(name, result)

    async def send_message(
        self,
        conversation_id: str,
        content: str,
        *,
        model: str | None = None,
        protocol_version: str = DEFAULT_HANDSHAKE_VERSION,
        timeout: float = SEND_MESSAGE_TIMEOUT_S,
    ) -> SendMessageResult:
        arguments: dict[str, Any] = {"conversation_id": conversation_id, "content": content}
        if model is not None:
            arguments["model"] = model
        result = await self.call_tool(
            "send_message", arguments, protocol_version=protocol_version, timeout=timeout
        )
        structured = result.structured
        notices = structured.get("notices")
        return SendMessageResult(
            conversation_id=_str_of(structured.get("conversation_id")) or conversation_id,
            text=result.text,
            message=_dict_of(structured.get("message")),
            title=_opt_str_of(structured.get("title")),
            notices=[n for n in notices if isinstance(n, str)] if isinstance(notices, list) else [],
            truncated=structured.get("truncated") is True,
        )

    async def _rpc(
        self,
        method: str,
        params: dict[str, Any],
        protocol_version: str,
        *,
        timeout: float | None = None,
        named: str | None = None,
    ) -> dict[str, Any]:
        if not protocol_version:
            raise ValueError("protocol_version must be a non-empty revision string")
        if (
            method != "initialize"
            and protocol_version in HANDSHAKE_VERSIONS
            and protocol_version not in self._initialized
        ):
            await self.initialize(protocol_version=protocol_version)
        body: dict[str, Any] = {
            "jsonrpc": "2.0",
            "id": next(self._ids),
            "method": method,
            "params": Mcp._params(params, protocol_version),
        }
        request = Request(
            "POST",
            "/mcp",
            body,
            keyed=True,
            stream=False,
            headers=Mcp._headers(method, named, protocol_version),
            timeout=timeout,
        )
        response = await self._client._send(request)
        payload = _base.parse_json_object(response)
        if "error" in payload:
            raise _rpc_error(payload.get("error"))
        result = payload.get("result")
        return dict(result) if isinstance(result, dict) else {}
