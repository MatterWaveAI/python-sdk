# Connect your editor to MatterWave

MatterWave is an MCP server. The `/mcp` endpoint on the deployment's origin
speaks the Model Context Protocol over Streamable HTTP -- one JSON-RPC POST
per message, one JSON object back, no session, no SSE stream -- so any MCP
client that speaks that transport can read and continue the conversations
the web app holds. Claude Desktop, VS Code / GitHub Copilot and Cursor all
do. Every tool is the same route the SPA and this SDK reach, so the tenancy
boundary, the scopes, the plan gates and the budget are the ones already
enforced there; a tool acts as the key that called it, and only as that.

## What the server offers

Six tools, filtered by the key's scopes -- `tools/list` never names a tool
the key would be refused, so a read-only key is shown no write:

| Tool | Scope | What it does |
|---|---|---|
| `list_conversations` | `chat:read` | One page of the sidebar, most recent first; `filter` is default/all/pinned/archived and `cursor` pages. |
| `get_conversation` | `chat:read` | One conversation's transcript, oldest first; long transcripts are bounded, and `messages_left_out` says how many of the oldest were dropped. |
| `search_conversations` | `chat:read` | Full-text search over the user's own messages, a snippet per hit. |
| `list_models` | `chat:read` | The deployment's models, which the plan includes, and the default. |
| `create_conversation` | `chat:write` | A new, empty conversation. |
| `send_message` | `chat:write` | Append a user message and return the whole answer; the model's stream is read to its end, so the call can run minutes. |

There are also resources: `resources/list` is the sidebar's first page as
`conversation://{id}` URIs and `resources/read` returns a transcript as
Markdown. There are no prompts. The server answers both protocol eras --
the handshake revisions (`2025-03-26` through `2025-11-25`) and the
`2026-07-28` per-request envelope -- and stays stateless either way: no
session id is issued and any replica can answer any request.

## Get an API key

Authentication is an ordinary MatterWave API key as a bearer. Two ways to
get one:

**Settings > API keys** in the web app. Name it after the editor, leave the
default scopes (`chat:read`, `chat:write` -- everything the tools need),
and copy the secret from the creation answer: it is shown once. For an
assistant that should only read, mint a `chat:read`-only key; the editor is
then never offered `create_conversation` or `send_message`.

**Device login**, for a machine with no browser of its own (RFC 8628):

```python
from matterwave_sdk import MatterWave

client = MatterWave(base_url="https://your-matterwave.example")  # no key needed yet
token = client.auth.device.login(
    client_name="My editor",
    on_code=lambda code: print(f"Open {code.verification_uri} and enter {code.user_code}"),
    timeout=300,
)
print(token.access_token)  # an API key: paste it into the editor config
```

You approve the code in any browser where you are signed in; the poll then
returns a key minted for you with `chat:read` and `chat:write`, named
`Device: My editor` and listed under Settings > API keys like any other --
revoke it there when the editor moves on.

## Point your editor at it

Every client takes the same three facts: the endpoint is
`https://your-matterwave.example/mcp`, the transport is Streamable HTTP
(`http`, not `sse` -- this server answers `GET /mcp` with 405), and the key
rides in an `Authorization: Bearer` header.

### Claude Desktop

`claude_desktop_config.json` (Developer > Edit Config):

```json
{
  "mcpServers": {
    "matterwave": {
      "type": "http",
      "url": "https://your-matterwave.example/mcp",
      "headers": { "Authorization": "Bearer mw_..." }
    }
  }
}
```

A build whose config file does not take remote servers takes the same
server through Settings > Connectors instead.

### VS Code / GitHub Copilot

`.vscode/mcp.json`, or "MCP: Add Server" in the Command Palette (for every
project, "MCP: Open User Configuration"):

```json
{
  "servers": {
    "matterwave": {
      "type": "http",
      "url": "https://your-matterwave.example/mcp",
      "headers": { "Authorization": "Bearer mw_..." }
    }
  }
}
```

Start it from "MCP: List Servers", then use Agent Mode in Copilot Chat.

### Cursor

`~/.cursor/mcp.json`, or Settings > MCP & Integrations > New MCP Server
(the `url` form is Cursor's way of naming the HTTP transport):

```json
{
  "mcpServers": {
    "matterwave": {
      "url": "https://your-matterwave.example/mcp",
      "headers": { "Authorization": "Bearer mw_..." }
    }
  }
}
```

Reload; the tools appear under the matterwave entry, only ever the ones the
key's scopes allow.

## No OAuth, deliberately

MCP's OAuth-style authorization -- protected-resource metadata, dynamic
client registration, an editor's "Sign in with MatterWave" -- is not
implemented on the server, and an API key in a header is the only scheme.
That is a known gap, not an oversight: the identity provider cannot yet
bind a token to this resource, and a MatterWave key is a bearer the API
already knows how to scope, expire, rate-limit, audit and revoke. Until the
platform closes the gap, every config above pastes a key.

## Errors

A refusal before any method runs -- no key, a revoked key, the key's rate
limit -- arrives as a JSON-RPC error whose HTTP status travels with it:
`401` for a missing or bad key, `403` for a suspended owner, `429` with
`Retry-After` for the window. A refusal a *tool* decided on -- the budget,
the plan, another tenant's conversation, moderation -- is different: a
result with `isError: true` and a machine-readable
`structuredContent.error.code` (`budget`, `upgrade_required`, `not_found`,
`moderation`, ...), so the model on the other side can tell you why.

## Through the SDK instead

`client.mcp` speaks the same endpoint and needs no protocol library:

```python
from matterwave_sdk import MatterWave

client = MatterWave(api_key="mw_...", base_url="https://your-matterwave.example")
print([tool.name for tool in client.mcp.list_tools()])
answer = client.mcp.send_message("<conversation-id>", "Draft a three-line agenda.")
print(answer.text)
```

A runnable example: `examples/mcp_via_sdk.py` drives the wrapper end to
end -- list the tools the key's scopes allow, start a conversation, send
one message, search for it.

## Security notes

- **The key is the person, within its scopes.** Every tool call acts as the
  key's owner in their org; nothing on this path holds a service identity.
  Prefer a `chat:read`-only key for an assistant that must not start
  conversations or send messages.
- **Storage.** Editor configs are plaintext. Keep the file out of anything
  that syncs or commits (`.vscode/mcp.json` is tracked by default --
  ignore it or use the user-scope configuration), and prefer the client's
  own secret storage where it offers one.
- **Revocation.** Settings > API keys revokes any key, device-login keys
  included; revocation is immediate and final. Rotation issues a new secret
  and lets the old one live out a short grace window.
- **Audit.** Every tool call a key makes is one row in the org's audit
  trail -- which key, which tool, refused or not, never the content -- and
  the key's `last_used_at` moves with it.
- **Rate limit.** The same 60 requests a minute per key as `/v1`, shared by
  nothing; one `send_message` can hold a slot for minutes, so an agent
  looping on it should budget its calls.
- **Origins.** The endpoint applies the transport's DNS-rebinding rule: a
  browser client's `Origin` must be the deployment's own or CORS-allowed.
  Desktop editors send no `Origin` and pass as-is.
