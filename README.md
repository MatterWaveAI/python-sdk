# matterwave-sdk

The Python client for the MatterWave API: the OpenAI-compatible `/v1` surface,
with typed responses, a streaming iterator, retries that honour `Retry-After`,
and the server's `X-Request-Id` on every error.

```
pip install matterwave-sdk
```

The distribution is `matterwave-sdk` and the import package is `matterwave_sdk`.
The bare names belong to others: `matterwave` on PyPI is an unrelated physics
library that ships its own top-level `matterwave` module, and `matterwave` is
also the import name of this repo's API server (`api/`). See
[docs/package-names.md](../../docs/package-names.md).

Python 3.10 or newer; the only dependency is `httpx`.

## Configure

```python
from matterwave_sdk import MatterWave

client = MatterWave(api_key="mw_...", base_url="https://your-matterwave.example")
```

Both arguments fall back to the environment: `MATTERWAVE_API_KEY` and
`MATTERWAVE_BASE_URL`. The base URL is the origin the app is served from; the
OpenAI-style form ending in `/v1` is accepted too. Keys are created under
Settings > API keys in the app and shown once.

## Three examples

### Complete

```python
completion = client.chat.completions.create(
    messages=[{"role": "user", "content": "Say hello in five words."}],
)
print(completion.content)              # the reply
print(completion.usage.total_tokens)   # what it cost
print(completion.request_id)           # the server's X-Request-Id
```

`model` may be omitted; the deployment's default is used. `client.models.list()`
says what is served.

### Stream

```python
stream = client.chat.completions.create(
    messages=[{"role": "user", "content": "Tell me a short story."}],
    stream=True,
)
for text in stream.text():
    print(text, end="", flush=True)
```

Iterating the stream itself yields `ChatCompletionChunk` objects instead of
strings. Pass `include_usage=True` to receive a final chunk with the token
counts (its `choices` is empty). Closing the stream early releases the
connection.

### Async, with error handling

```python
import asyncio
from matterwave_sdk import AsyncMatterWave, RateLimitError, APIError

async def main() -> None:
    async with AsyncMatterWave() as client:
        try:
            completion = await client.chat.completions.create(
                messages=[{"role": "user", "content": "Name one animal."}],
                temperature=0.2,
            )
            print(completion.content)
        except RateLimitError as err:
            # `rate_limit_exceeded` has already been retried with backoff;
            # `insufficient_quota` is the organisation's budget and is not.
            print(err.code, err.retry_after, err.request_id)
        except APIError as err:
            print(err.status_code, err.type, err.code, err.request_id)

asyncio.run(main())
```

## Errors and retries

Every refusal is an `APIError` subclass by status: `BadRequestError` (400),
`AuthenticationError` (401), `PermissionDeniedError` (403), `NotFoundError`
(404), `ConflictError` (409), `RateLimitError` (429), `ServerError` (5xx). Each carries the API's
envelope (`message`, `type`, `code`, `param`) and `request_id`; a
`RateLimitError` also carries `rate_limit` (limit, remaining, reset seconds)
from the `X-RateLimit-*` headers. An error that
arrives inside a stream, after the 200, is an `APIError` with
`status_code=None`. Connection failures are `APIConnectionError`; timeouts
`APITimeoutError`.

A 429 whose code is `rate_limit_exceeded` and any 5xx (and connection errors)
are retried up to `max_retries` times (default 2) with exponential backoff and
jitter, or for exactly `Retry-After` when the server sends one. A 429 whose
code is `insufficient_quota` is raised at once: waiting does not refill a
budget. A streamed request is retried only before its stream starts.

## Tools, structured output and embeddings

`tools`, `tool_choice` and `response_format` are passed to the model as sent and
the answer is relayed: a tool call comes back in `completion.tool_calls` (or as
`delta.tool_calls` fragments on a stream) for your code to run, never run on
the server.

```python
completion = client.chat.completions.create(
    messages=[{"role": "user", "content": "Weather in Oslo?"}],
    tools=[{"type": "function", "function": {"name": "weather", "parameters": {"type": "object"}}}],
)
for call in completion.tool_calls:
    print(call.id, call.function.name, call.function.arguments)
```

`client.embeddings.create(["one", "two"])` returns one embedding per input from
the deployment's embedding model; `client.index().capabilities["embeddings"]`
says whether one is configured.

## Recording a turn into a conversation

```python
client.chat.completions.create(
    messages=[...],
    conversation_id="<a conversation of the key owner's>",
)
```

The request is still the whole prompt; the server splices in no history. Without
`conversation_id` nothing is stored.

## Conversations

`client.conversations` is the app's own conversation API, the one the web app
uses: the server keeps the history, builds the prompt and applies the
instructions, so a conversation made here shows up in the browser and the
other way round. A key acts as its owner.

```python
from matterwave_sdk import frames

conv = client.conversations.create(title="Plans")
with client.conversations.send(conv.id, "Draft a three-line agenda.") as stream:
    for frame in stream:
        if isinstance(frame, frames.DeltaFrame) and frame.content:
            print(frame.content, end="", flush=True)
        elif isinstance(frame, frames.ContextFrame):
            meter = (frame.prompt_tokens, frame.context_window)
        elif isinstance(frame, (frames.ErrorFrame, frames.ModerationBlockFrame)):
            print(frame.detail, frame.code)
print(stream.result.id if stream.result else None)   # the saved assistant message
```

`list(filter=, limit=, cursor=)` is one page of the sidebar (`next_cursor`
on the result; `pages()` iterates them all), and `search(q)`, `get(id,
full=)`, `tree(id)`, `set_active_leaf(id, message_id)`, `fork(id,
message_id)`, `edit(id, message_id, content)` and `regenerate(id,
message_id)` cover the rest of the sidebar and the branch switcher;
`update(id, title=, default_model=)` renames the conversation and keeps the
model its turns use (None clears the model); `context(id)` says how full the
model's window is, and `compact(id)` folds the earlier messages into a summary
now (a `ConflictError` when there is nothing to fold yet). `client.me()` says
who the key acts as.

The stream is named server-sent events, read into typed frames in
`matterwave_sdk.frames`: `CompactionFrame`, `ContextFrame`, `MessageFrame` (the
user turn as saved), `TitleFrame`, `PartStartFrame` / `DeltaFrame` /
`PartEndFrame` per part (text, reasoning, tool call, tool result),
`NoticeFrame`, and one terminal frame: `DoneFrame` (the assistant message),
`ErrorFrame` or `ModerationBlockFrame`. An event the client does not know is an
`UnknownFrame`; the server's heartbeat comments are skipped. `stream.text()`
yields just the answer's words.

If the connection drops before the terminal frame, the stream reconnects once
with `Last-Event-ID` and the server replays the rest of the same generation;
the prompt is never sent twice. A drop before any resumable frame, or a second
drop, is an `APIConnectionError`. The async client is the same with `await` and
`async for`.

## Keys, usage, webhooks, attachments, library and shares

The app's own settings routes, one method per route, sync and async alike.
A key acts as its owner, within its scopes: managing keys needs `keys:manage`,
`webhooks` needs `webhooks:manage`, and `usage.org` / `usage.export_csv` need
`usage:read` (a session needs none). None of them is in a key's default
scopes; pick them when minting the key. The scope never widens the owner's
role: `webhooks` also needs the key's owner to be an org owner or admin, and a
member's `usage:read` key reports only their own row. A key missing either
raises `PermissionDeniedError` (403).

```python
# Keys: the secret is on the answer to create and rotate, and nowhere else.
key = client.keys.create("ci", scopes=["chat:read", "chat:write"], expires_in_days=90)
store_somewhere_safe(key.key)
client.keys.update(key.id, allowed_cidrs=["203.0.113.0/24"], expires_in_days=None)  # None clears
rotated = client.keys.rotate(key.id)  # old secret works until rotated.previous_valid_until
client.keys.revoke(key.id)

# Usage: the balance, the month per member, and the same month as CSV.
balance = client.usage.get()
print(balance.used, "of", balance.included, balance.unit)
report = client.usage.org(period="2026-10")
Path("usage-2026-10.csv").write_text(client.usage.export_csv(period="2026-10"))

# Webhooks: the signing secret is on create and rotate_secret only.
hook = client.webhooks.create("https://hooks.example.com/mw", ["message.completed"])
client.webhooks.test(hook.id)
failed = client.webhooks.deliveries(hook.id, status="failed")

# Attachments, then a turn that uses one.
doc = client.attachments.upload(Path("notes.pdf").read_bytes(), "notes.pdf")
stream = client.conversations.send(conversation_id, "Summarise this.", attachment_ids=[doc.id])
summary = "".join(stream.text())
data = client.attachments.download(client.attachments.get(doc.id))

# Library: folders, tags and paged search.
folder = client.library.create_folder("Work")
client.library.move(conversation_id, folder.id)
page = client.library.search("invoice", limit=20)

# Shares: the token is on create only; view needs no key.
link = client.shares.create(conversation_id, expires_in_s=7 * 86400)
client.shares.revoke(link.share.id)
```

`keys`: `list`, `create`, `update`, `rotate`, `revoke`. `usage`: `get`, `org`,
`export_csv`. `webhooks`: `list`, `create`, `get`, `update`, `delete`,
`rotate_secret`, `test`, `deliveries`, `redeliver`. `attachments`: `upload`,
`get`, `delete`, `download`, `limits`. `library`: `get`, `search`,
`create_folder`, `update_folder`, `delete_folder`, `move`, `create_tag`,
`delete_tag`, `set_tags`. `shares`: `create`, `list`, `list_mine`, `revoke`,
`view`. A delete or revoke returns None; a missing id is a `NotFoundError`.

## Device login

For a terminal or an editor with no browser of its own (RFC 8628): show the
person a code, they approve it in any browser, and the poll returns an API key
that acts as them.

```python
token = client.auth.device.login(
    on_code=lambda code: print(f"Open {code.verification_uri} and enter {code.user_code}"),
    timeout=300,
)
signed_in = MatterWave(api_key=token.access_token)
```

`login` polls at the server's interval and slows down when told to; it raises
`DeviceAuthError` (`code` is `access_denied` or `expired_token`) or
`APITimeoutError`. `start()` and `poll(device_code)` are the two steps on their
own. The SDK never writes the token anywhere: store it in the OS keyring or the
editor's secret storage, not a dotfile.

## Development

```
uv sync --extra dev
uv run pytest
uv run ruff check . && uv run mypy
```

`tests/test_openapi_contract.py` checks this client against
`docs/developers/openapi.json` at the repo root, so a new operation on the API
and a new method here have to land together. That document is the `/v1`
contract; the conversation, device-login and native routes are listed apart in
`APP_OPERATIONS` and checked against it only where it names their paths.
`tests/test_native_clients.py` also holds each native route in
`APP_OPERATIONS` to the routers in `api/matterwave`, when they are beside the
SDK. The
conversation stream's frames are the exception: the document lists them under
`x-sse-events`, and `frames.FRAMES_BY_EVENT` is held to that list, event by
event and field by field.
