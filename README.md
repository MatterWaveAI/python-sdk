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
also the import name of the MatterWave API server. Both PyPI packages publish
under the [MatterWaveAI](https://pypi.org/org/MatterWaveAI/) org, and their
sources live at [github.com/MatterWaveAI](https://github.com/MatterWaveAI).

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
print(completion.content)  # the reply
print(completion.usage.total_tokens)  # what it cost
print(completion.request_id)  # the server's X-Request-Id
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
from the `X-RateLimit-*` headers. A send, edit or regenerate in a
conversation a connector opened (one started from Slack) is a
`ConnectorConversationError`, the `ConflictError` whose `code` is
`connector_conversation` and whose `source` names the connector, the
channel and the thread's link; fork it to continue it here. An error that
arrives inside a stream, after the 200, is an `APIError` with
`status_code=None`. Connection failures are `APIConnectionError`; timeouts
`APITimeoutError`.

A 429 whose code is `rate_limit_exceeded` is retried up to `max_retries` times
(default 2) with exponential backoff and jitter, or for exactly `Retry-After`
when the server sends one. A 429 whose code is `insufficient_quota` is raised
at once: waiting does not refill a budget. So is a 429 or 5xx whose
`Retry-After` is longer than `max_retry_wait` (default 60 seconds), with the
delay in `err.retry_after`. A 5xx or a dropped connection is
retried the same way for a request that is safe to send twice (GET, PUT,
DELETE, and the read-only MCP calls). A POST or PATCH -- a message, a key, an
upload -- is sent again only when the connection was never made: after a 5xx or
a reply cut short, the first copy may already have taken effect, so the error is
raised instead. A streamed request is retried only before its stream starts.

Every write (any method but GET) carries an `Idempotency-Key` header: a fresh
UUID per call, the same on each of its retries, so the server can recognise a
repeat. `client.with_options(idempotency_key=...)` supplies your own, for a
write whose retries may come from another process. The API does not yet answer
a repeated key with the first result, so the narrower retry rule for writes
above still applies.

### Logging and deprecations

`MATTERWAVE_LOG=info` logs one line per response or failure to stderr --
method, path, status, request id and time taken; `MATTERWAVE_LOG=debug` adds
each attempt as it goes out and each wait before a retry. Bodies and headers
are never logged. The lines go to the `matterwave_sdk` logger, so `logging`
configuration works too.

When the API marks a route for retirement (`Deprecation` and `Sunset`
headers), the first call to it raises an `APIDeprecationWarning` naming the
route and its dates, once per route per process. See
[VERSIONING.md](VERSIONING.md) for what each release supports.

### Per-call settings

`timeout` (seconds), `max_retries` and `max_retry_wait` are set on the client
and apply to every call. `with_options` gives a call its own, for any method:

```python
client.with_options(timeout=600, max_retries=0).conversations.compact(conversation_id)
```

The view it returns shares the client's connection pool, so making one per call
costs nothing; `default_headers` passed to it are added to the client's. The
chat, embeddings and conversation-turn methods also take `timeout=` and
`max_retries=` as keywords.

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

To continue the loop, send the assistant message back with one `tool` message
per call. `message.to_message()` and `tool_message(call_id, content)` build them
in the shape the request takes:

```python
from matterwave_sdk import tool_message

messages.append(completion.choices[0].message.to_message())
for call in completion.tool_calls:
    messages.append(tool_message(call.id, run(call)))  # run(): your code
completion = client.chat.completions.create(messages=messages, tools=tools)
```

On a stream, `stream.final_message()` drains it and returns the same message,
each tool call put back together from its `delta.tool_calls` fragments (calls
at different indexes may interleave); `stream.final_completion()` returns the
whole `ChatCompletion` instead, with the finish reason and the usage (pass
`include_usage=True` for the usage). `ChatCompletionAccumulator` does the same
one chunk at a time, for a client that renders the text as it arrives and also
wants the finish reason and usage:

```python
from matterwave_sdk import ChatCompletionAccumulator

acc = ChatCompletionAccumulator()
for chunk in client.chat.completions.create(messages=messages, tools=tools, stream=True):
    print(chunk.content, end="")
    acc.add(chunk)
message, finish = acc.message(), acc.finish_reason
```

`frequency_penalty` and `presence_penalty` (-2 to 2) are relayed as OpenAI
defines them. Before offering an agent mode, a client reads two flags: the
model row's `supports_tools` (`client.app_models()`) and the org's
`local_agent_tools` (`client.me()`). Each is `None` when the server did not say.

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
print(stream.result.id if stream.result else None)  # the saved assistant message
```

`list(filter=, limit=, cursor=)` is one page of the sidebar (`next_cursor`
on the result; `pages()` iterates them all and `items()` every conversation
of every page), and `search(q)`, `get(id,
full=)`, `tree(id)`, `set_active_leaf(id, message_id)`, `fork(id,
message_id)`, `edit(id, message_id, content)` and `regenerate(id,
message_id)` cover the rest of the sidebar and the branch switcher;
`update(id, title=, default_model=)` renames the conversation and keeps the
model its turns use (None clears the model); `context(id)` says how full the
model's window is, and `compact(id)` folds the earlier messages into a summary
now (a `ConflictError` when there is nothing to fold yet). `client.me()` says
who the key acts as.

The stream is named server-sent events, read into typed frames in
`matterwave_sdk.frames`: `CompactingFrame` (first, when the server is folding
earlier turns before this one, so the first token is later than usual),
`CompactionFrame`, `ContextFrame`, `MessageFrame` (the
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
for hit in client.library.search_items("invoice"):  # every page, fetched as it is reached
    print(hit.conversation.title, hit.snippet)

# Shares: the token is on create only; view needs no key.
link = client.shares.create(conversation_id, expires_in_s=7 * 86400)
client.shares.revoke(link.share.id)
```

`keys`: `list`, `create`, `update`, `rotate`, `revoke`. `usage`: `get`, `org`,
`export_csv`. `webhooks`: `list`, `create`, `get`, `update`, `delete`,
`rotate_secret`, `test`, `deliveries`, `redeliver`. `attachments`: `upload`,
`get`, `delete`, `download`, `limits`. `library`: `get`, `search`,
`search_pages`, `search_items`, `create_folder`, `update_folder`, `delete_folder`, `move`, `create_tag`,
`delete_tag`, `set_tags`. `shares`: `create`, `list`, `list_mine`, `revoke`,
`view`. A delete or revoke returns None; a missing id is a `NotFoundError`.

Every cursor-paged list has a page iterator and an item iterator beside it:
`conversations.pages` / `items`, `audit.pages` / `items` and
`library.search_pages` / `search_items`, each fetching the next page only
when the previous one has been consumed. `pages_of(fetch)` and
`items_of(pages)` (and `async_pages_of` / `async_items_of`) are the walk
they share, for any list that answers `{"data": [...], "next_cursor": ...}`:

```python
from matterwave_sdk import items_of, pages_of

for event in items_of(pages_of(lambda cursor: client.audit.list(event="org.sso", cursor=cursor))):
    print(event.occurred_at, event.action)
```

## Your account and your data

The caller's own account, with a key (as its owner) or a person's token:

```python
client.account.update(full_name="Ada Lovelace")
client.account.accept_terms("2026-10")  # a version other than the current one is a 422
held = client.account.enforcement()  # state "none", or the warning or limit and why
client.preferences.update(product_updates=True)  # reads, changes what was passed, writes back
for session in client.sessions.list():
    print(session.user_agent, session.last_seen_at)
client.sessions.revoke_others()

# Everything the account holds, as one JSON document (needs chat:read).
document = client.exports.create_and_download(timeout=600)

# Conversations from ChatGPT's or Claude's data export (needs chat:write).
job = client.imports.create(Path("conversations.json").read_bytes(), "conversations.json")
print(client.imports.wait(job.id).conversations_created)

said = client.speech.transcribe(Path("note.ogg").read_bytes(), "note.ogg", language="en")
client.attachments.scan(attachment_id)  # poll until scan_status is "clean"
data = client.attachments.view(attachment_id)  # the bytes, through the signed link the route mints

status = client.billing.status()  # the plan, its limits, and dunning when late
catalog = MatterWave(api_key=None).plans.list()  # public: the price list and model prices
```

`account`: `update`, `accept_terms`, `enforcement`, `onboarding_complete`, and
the writes an API key may not make, which take a person's token or a session:
`change_password`, the email change (`start_email_change`,
`verify_email_change`, `cancel_email_change`, `resend_email_change_code`,
`email_state`) and `delete` (the `"delete my account"` phrase, checked here
too). The same account routes answer to the names the settings panes use
as well: `account.rename` (the name collapsed and bounded here),
`account.sessions` / `revoke_session` / `logout_others`, and
`account.preferences` / `update_preferences`, which sends exactly the groups
passed and lets the rest go back to their defaults. `preferences`: `get`,
`update` (the route replaces the whole object, so `update` reads it first; a
field not passed keeps its value, `locale=None` clears the locale, and billing
receipts cannot be turned off). `sessions`: `list` (a session's `id` is the
cookie's own value, kept out of `repr`), `revoke`, `revoke_others`. `exports`:
`create` (or `start`), `get`, `wait`, `download`, `create_and_download`, and
`document`, which builds the export inside the request. `imports`: `create`
(or `start`), `list`, `get`, `wait`. `speech.transcribe`, `attachments.scan` / `view`, `billing.status`,
`plans.list` and `policy.stats` (the last two public, sent with no credential).
`wait` raises `MatterWaveError` when the job failed or expired and
`APITimeoutError` when `timeout` passes first.

Beside them: `passkeys` (registration for a signed-in person, and the login
ceremony's two anonymous halves), `mfa` (TOTP enrolment, removal and new
recovery codes), `accounts` (the accounts signed in on one browser, by its
cookies) and `sync` (edge pairing: an owner or admin mints a code, the box
redeems it for a device key with `pair` and pushes conversations with that
key).

## Administering the organisation

Members, invites, the org's settings and its billing are a person's to manage,
not a key's: sign in as an owner or admin (see [Signing a person in](#signing-a-person-in))
and pass the token as `access_token=`.

```python
admin = MatterWave(access_token=signin.session(token))

for member in admin.members.list():
    print(member.email, member.role, member.state)
admin.members.set_role(subject, "admin")
invite = admin.invites.create("new.hire@example.com", role="member")  # mailed; `mail` says if sent
link = admin.invite_links.create(role="viewer", max_uses=5)  # link.url is shown this once

admin.org.set_models(["grt-nano", "grt-pro"])  # the allow-list; None allows the whole plan
admin.org.set_sharing(False)  # silences every share link without revoking it
admin.org.domains.claim("example.com")
print(admin.org.domains.challenge("example.com").record.value)  # publish, then verify()
admin.org.idps.attach(name="Okta", issuer="https://acme.okta.com", client_id=..., client_secret=...)
enforced = admin.org.set_sso(True)  # owner only; store enforced.recovery.code offline

print(admin.billing.invoices().data[0].hosted_invoice_url)
checkout = admin.billing.checkout("team", annual=True)  # send the person to checkout.url
```

`org`: `get`, `rename`, `close` (owner only, irreversible; `confirm` is the org's
name), `models` / `set_models`, `sharing` / `set_sharing`, `shares` /
`revoke_share`, `sso` / `set_sso` / `mint_recovery_code`, `mfa` / `set_mfa`, and
`org.domains` (`list`, `claim`, `challenge`, `verify`, `release`) and `org.idps`
(`list`, `attach`, `detach`). `members`: `list`, `set_role`,
`transfer_ownership`, `remove`. `invites`: `list`, `create`, `resend`, `revoke`,
`lookup`, `accept`. `invite_links`: `list`, `create`, `revoke`, `lookup`, `join`.
`billing`: `checkout`, `portal`, `invoices`, `change_plan` (or `switch_plan`),
`plan_history` beside `status`, and the public `plans` (as `plans.list`).
`members` answers the invite and link routes too: `list_invites`,
`create_invite`, `resend_invite`, `revoke_invite`, `lookup_invite`,
`accept_invite`, `list_invite_links`, `create_invite_link`,
`revoke_invite_link`, `lookup_join_link` (or `join_lookup`) and `join`. `auth.device`: `lookup`, `approve`, `deny`, the person's side
of device login. `invites.lookup` and `invite_links.lookup` are anonymous: the
token is the credential. Reading the org, its members and its invites is any
member's; the rest is an owner's or admin's, and closing the org, transferring
ownership and requiring SSO or two-factor an owner's alone. One-time secrets --
an invite link's `token` and `url`, an SSO recovery `code` -- are kept out of
`repr`.

## Receiving webhooks

`client.webhooks` manages endpoints; `matterwave_sdk.webhooks` is the
receiver's half. Every delivery is signed twice under the endpoint's secret:
`MatterWave-Signature` (`t=<unix>,v1=<hex>`) and the Standard Webhooks headers
(`webhook-id`, `webhook-timestamp`, `webhook-signature`). `unwrap` accepts the
delivery when either verifies within five minutes of the receiver's clock, and
only then decodes the body; anything else raises `WebhookVerificationError`.

```python
from matterwave_sdk import WebhookVerificationError, webhooks


def receive(body: bytes, headers: dict[str, str]) -> int:
    try:
        event = webhooks.unwrap(body, headers, WEBHOOK_SECRET)  # the raw body, not re-encoded JSON
    except WebhookVerificationError:
        return 400
    handle(event["type"], event["data"])  # dedupe on event["id"]: delivery is at-least-once
    return 204
```

`verify_signature` and `verify_standard_signature` check one scheme each;
`sign` and `sign_standard` write them, for tests and for replaying a delivery.

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

The key device login mints carries `chat:read` and `chat:write`, so it acts as
the person in their conversations but not on the routes that manage the
organisation; for those, sign the person in at the identity provider instead.

## Signing a person in

The routes that manage the organisation -- members and invites, the org's
settings, its billing, the audit trail, the org's instructions, budget caps, and
the operator console -- gate on the caller's
membership, not a key scope, and answer every API key 403 "Membership is managed
by a person, not an API key." A person's access token from the deployment's
identity provider is what they take. Pass one as `access_token=`, the token
itself or a callable that returns it; the client sends it where a key would go,
and every other route takes it too.

```python
from matterwave_sdk import MatterWave

signin = MatterWave(base_url="https://your-matterwave.example").auth.person
token = signin.login(
    on_code=lambda code: print(f"Open {code.verification_uri_complete}"),
    timeout=300,
)
admin = MatterWave(
    base_url="https://your-matterwave.example",
    access_token=signin.session(token, on_refresh=store_in_keyring),
)
print(admin.audit.list(event="org.sso").data)
```

`auth.person.login` is the device authorization grant (RFC 8628) against the
identity provider's native client, which `/api/auth-config` names; a deployment
without one raises `MatterWaveError`. It asks for `offline_access` beside the
deployment's scopes, so the `PersonToken` carries a refresh token, and
`session(token)` is a callable that hands over the access token and refreshes it
a minute before it expires, passing each new set to `on_refresh` so the caller
can store the rotated refresh token. `start()`, `poll(device_code)` and
`refresh(token)` are the steps on their own. A token from anywhere else -- an
agent's or a service account's, issued by the same identity provider for the
deployment's project -- works the same way: `access_token="eyJ..."`. On
`AsyncMatterWave` the callable may be a coroutine function.

### Which credential each method needs

`CREDENTIALS` maps every operation to what its route accepts, from the
platform's route document (the drift test holds the two together):

| `CREDENTIALS` | Accepts | Methods |
| --- | --- | --- |
| `public` | nothing | `index`, `auth.device.start` / `poll`, `shares.view`, `plans.list`, `policy.stats`, `invites.lookup`, `invite_links.lookup`, `passkeys.login_start` / `login_finish`, `accounts`, `sync.pair` |
| `key` | any API key, or a person's token | `me`, `account` (but the password, email and deletion methods), `preferences`, `sessions`, `speech`, `billing.status`, `app_models`, `instructions.user` / `update_user`, `notifications`, `announcements.list`, `entitlements.get`, `messages.set_feedback` / `delete_feedback`, `keys.list`, `usage.get`, `attachments`, `library`, `shares.list_mine` / `revoke`, `budget.get`, `sync.status` |
| `key:chat:read` | a key with `chat:read`, or a person's token | `models`, `exports`, the conversation reads, `messages.list_feedback`, `shares.list` |
| `key:chat:write` | a key with `chat:write`, or a person's token | `imports`, `chat.completions.create`, `embeddings.create`, the conversation writes and turns, `shares.create` |
| `key:keys:manage` | a key with `keys:manage`, or a person's token | `keys.create` / `update` / `rotate` / `revoke` |
| `key:usage:read` | a key with `usage:read`, or a person's token | `usage.org`, `usage.export_csv` |
| `key:webhooks:manage` | a key with `webhooks:manage`, or a person's token | `webhooks` |
| `key:conversations:import` | the device key an edge box got from pairing | `sync.push` |
| `person` | a person's token only | `audit`, `instructions.org` / `update_org`, `budget.members` / `set_overage_cap` / `set_member_default_cap` / `set_member_cap`, `org`, `members`, `invites` and `invite_links` (but their two `lookup`s), `billing` (but `status`), `auth.device.lookup` / `approve` / `deny`, `account.change_password` / `delete` and the email change, `passkeys` (but the two login steps), `mfa`, `sync.pairings`, `admin` |

A scope never widens a role: the org routes still need the org role they ask of a
person, whichever credential carries it.

## Connect your editor to MatterWave

`/mcp` on the deployment's origin is an MCP server: the conversation API in a
second wire format, over Streamable HTTP, with the same tenancy, scopes, plan
gates and budget as everything else the key reaches. Claude Desktop, VS Code /
GitHub Copilot and Cursor take the same three facts -- the URL
`https://your-matterwave.example/mcp`, transport `http`, and an API key in an
`Authorization: Bearer` header:

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

(VS Code reads `servers` from `.vscode/mcp.json`; Cursor reads `mcpServers`
with just `url` and `headers`.) The six tools -- `list_conversations`,
`get_conversation`, `search_conversations`, `list_models`,
`create_conversation`, `send_message` -- are filtered by the key's scopes, so
a `chat:read`-only key is never offered a write. There is no OAuth: an API key
as bearer is the only scheme, a known gap until the identity provider can bind
a token to this resource. Get a key from Settings > API keys or by device login
(above), store it like a secret, and revoke it there when the editor moves on.
The full walk-through -- every client's config, the tool table, errors, the
audit trail -- is in [docs/mcp.md](docs/mcp.md), and `examples/mcp_via_sdk.py`
drives the same endpoint through the wrapper:

```python
answer = client.mcp.send_message("<conversation-id>", "Draft a three-line agenda.")
print(answer.text)
```

## The agent tool contract

`matterwave_sdk.agent` is what the MatterWave coding clients (the TUI's `code`
mode, the VS Code agent) share: the local tools a client offers the model on
`/v1`, the approval modes that gate them, the paths those tools may touch, and
the agent-mode system prompt. It is versioned (`agent.CONTRACT_VERSION`,
`1.0.0`; the prompt carries its own `agent.SYSTEM_PROMPT_VERSION`), and its one
source is `src/matterwave_sdk/agent/contract.json`, from which the TypeScript
SDK's copy is generated.

| Tool | Kind | Arguments |
|---|---|---|
| `read_file` | read | `path`, `start_line`?, `end_line`? |
| `list_dir` | read | `path`? |
| `glob` | read | `pattern`, `path`? |
| `grep` | read | `pattern`, `path`?, `glob`?, `ignore_case`? |
| `edit` | write | `path`, `old_string`, `new_string` (fails when missing or not unique) |
| `write_file` | write | `path`, `content` |
| `run_shell` | shell | `command`, `timeout_s`? |

| Mode | Runs without asking | Asks first |
|---|---|---|
| `plan` | the read tools | nothing; writes and the shell are not offered at all |
| `ask` (default) | the read tools | `edit`, `write_file`, `run_shell` |
| `auto-edit` | the read tools, `edit`, `write_file` | `run_shell` |
| `yolo` | everything | nothing; needs a flag each session and is never saved |

```python
import json
from matterwave_sdk import agent, tool_message

tools = agent.tools_for_mode("ask")  # the `tools` to send
system = agent.system_prompt("ask", root=str(root))  # names the tools, the mode and the root

for call in completion.tool_calls:
    decision = agent.allowed(call.function.name, "ask")  # "allow", "ask" or "deny"
    args = json.loads(call.function.arguments)
    try:
        target = agent.confine(root, args["path"], write=call.function.name != "read_file")
    except agent.PathRefused as refused:
        messages.append(tool_message(call.id, str(refused)))  # the contract's sentence
        continue
    # an edit: apply it to the text, show the diff, write only once approved
    old = target.absolute.read_text(encoding="utf-8")
    new = agent.apply_edit(old, args["old_string"], args["new_string"], path=target.relative)
    print(agent.unified_diff(old, new, target.relative))
```

`confine(root, path)` resolves a tool's path and returns where it really leads,
or raises `PathRefused` (with a `reason` and the sentence to hand back to the
model) before anything is read or written: for a `..` segment, an absolute path
outside the root, a link that leads out of the root (symbolic links, and
junctions on Windows), a path on the deny list, by its own name or its
target's, or a write to a protected path. The deny list is `.env*`, `*.pem`,
`id_*` and `.git/` plus the lines of the workspace's `.matterwaveignore`, and it
applies to reads too, so those files are never sent to the model;
`.matterwaveignore` itself can be read but not written. Confinement is not a
sandbox: `run_shell` reaches whatever the user can, which is why it asks.

`apply_edit` is `edit`'s exact replace on text (`EditError` when the string is
missing, not unique, empty or unchanged; a model's LF strings still match a
CRLF file), and `unified_diff` prints the diff an approval shows. The TypeScript
SDK ports both line for line, and the two suites run the same conformance cases
(`tests/agent_conformance.json`), so the two clients refuse, edit and print
alike.

To change the contract, edit `contract.json`, bump `contract_version` (major
when a tool, field or mode goes or changes meaning, minor when one is added,
patch when only text changes), and regenerate the TypeScript copy with that
repo checked out beside this one:

```
uv run python scripts/gen_agent_contract.py            # writes ../typescript-sdk
uv run python scripts/gen_agent_contract.py --check    # exit 1 when the copy has drifted
```

The suite runs the check whenever the TypeScript SDK sits beside this checkout
(or `MATTERWAVE_TS_SDK` names one), and CI clones it to run the check on every
push.

## Python and TypeScript

The two SDKs are one client in two languages: a name here in snake_case is the
same name there in camelCase, and `tests/parity.test` (`test_parity.py`,
`parity.test.ts`) holds each to the other's public surface. Where they differ
on purpose, `tests/parity.json` says why; the differences a caller meets are:

| Python | TypeScript | Why |
| --- | --- | --- |
| `timeout=`, `max_retry_wait=` (seconds) | `timeoutMs`, `maxRetryWaitMs` | each language's unit for time |
| `client.with_options(...)` for any call | the same per call in `RequestOptions`, or `client.withOptions(...)` | a TypeScript call takes an options argument; a Python one would need a keyword on every method |
| `with_options(idempotency_key=...)` | `{ idempotencyKey }` in a call's options or `withOptions` | as above |
| `extra_headers=`, `extra_query=`, `extra_body=` on chat and embeddings | `extraHeaders`, `extraQuery`, `extraBody` in any call's options (`headers` is `extraHeaders`' older name) | the OpenAI-compatible calls are where the API adds fields first |
| `stream.final_text()` | `stream.finalText()` (`accumulate()` is its older name) | |
| `stream.close()`, `client.close()` | `stream.abort()`; the client has no pool to close | httpx holds connections; fetch cancels by signal |
| `http_client=` | `fetch` | the transport each language sends with |
| `on_request` receives the `Request` | `onRequest` receives `{ method, path }` | a TypeScript body may be a form or a stream, not something to hand a logger |
| `MATTERWAVE_LOG` or the `matterwave_sdk` logger | `logLevel` and `logger` (`MATTERWAVE_LOG` where there is an environment) | Python has a logging module; JavaScript does not |
| `APIDeprecationWarning`, a warning | `logger.warn` | each language's way to warn |
| | `dangerouslyAllowBrowser` | only a browser puts a key in reach of a page's visitors |

A configuration mistake (no base URL, a negative retry count) is a
`MatterWaveConfigError` in both, under `MatterWaveError`; in Python it is a
`ValueError` as well. Python checks other arguments with `ValueError`, as
Python does; TypeScript throws `MatterWaveConfigError` for those too.

## Examples and reference

Each file in `examples/` runs as a script (`uv run examples/<name>.py`, which
installs the SDK from its PEP 723 header) and reads `MATTERWAVE_BASE_URL` and
`MATTERWAVE_API_KEY`; `tests/test_examples.py` runs every one against a fake
API.

| File | Shows |
| --- | --- |
| `chat.py` | one completion and its token count |
| `streaming.py` | text as it arrives, then the finish reason and usage |
| `structured_output.py` | `chat.completions.parse` with a validating model |
| `webhook_receiver.py` | a server that verifies each delivery with `webhooks.unwrap` |
| `device_sign_in.py` | device sign-in, for an API key or for a person's own token |
| `pagination.py` | `items()`, `search_items()` and `pages_of` / `items_of` |
| `mcp_via_sdk.py` | the `/mcp` endpoint through `client.mcp` |

The API reference is generated with pdoc; the `docs` workflow builds it on
every push and uploads it as the `api-reference` artifact. To build and check
it locally:

```
uv run --with pdoc==15.0.4 pdoc -o reference matterwave_sdk
uv run python scripts/check_reference.py reference
```

## Development

```
uv sync --extra dev
uv run pytest
uv run ruff check . && uv run ruff format --check && uv run mypy
```

CI runs the same on Python 3.10, 3.11, 3.12 and 3.13.

`pre-commit install` once per clone runs gitleaks on every commit, the same
secret scan CI runs over the whole history.

`tests/test_openapi_contract.py` checks this client against the app's
exported OpenAPI document, `docs/developers/openapi.json` in the app repo,
through the snapshot in `tests/openapi.json`, so a new operation on the API
and a new method here have to land together. That document is the `/v1`
contract; the conversation, device-login and native routes are listed apart in
`APP_OPERATIONS`. The conversation stream's frames are the exception: the
document lists them under `x-sse-events`, and `frames.FRAMES_BY_EVENT` is held
to that list, event by event and field by field.

`tests/test_route_drift.py` holds every route the SDK calls to the platform's
whole route document, `docs/developers/openapi-full.json`, vendored as
`tests/openapi-full.json` at the platform commit `tests/openapi-full.pin.json`
names. It fails when a route the SDK calls moves or goes, and when the document
gains a route that is neither wrapped here nor listed, with the reason, in
`tests/routes-not-covered.json`.

### Refreshing the OpenAPI snapshot

`scripts/refresh_openapi.py` copies the app's export over `tests/openapi.json`,
from a path or an http(s) URL, after checking it is the export (an OpenAPI
document with `paths` and `x-sse-events`). `--check` writes nothing and exits 1
when the snapshot is behind:

```
uv run python scripts/refresh_openapi.py ../matterwave-app/docs/developers/openapi.json
uv run python scripts/refresh_openapi.py --check https://<host>/<path>/openapi.json
```

In CI, run the `ci` workflow by hand (`workflow_dispatch`) with an
`openapi_source` URL: it refreshes the snapshot from it before the tests, so the
run says whether this client still matches that API.

### Refreshing the route document

`scripts/refresh_openapi_full.py` vendors the whole route document from a
checkout of the platform repo, at a branch or commit (`--ref`, `HEAD` by
default), with the routes the platform serves but leaves out of it (the
operator console, the edge mirror, the sign-in forms) and the commit, in
`tests/openapi-full.pin.json`. `--check` writes nothing and exits 1 when the
vendored pair is behind that ref:

```
uv run python scripts/refresh_openapi_full.py ../matterwave-app --ref develop
uv run python scripts/refresh_openapi_full.py ../matterwave-app --ref develop --check
```

`scripts/check_live_routes.py <deployment>/openapi.json` holds a running
deployment to the same pair: every route the SDK calls is served, every pinned
route is still served, and every served route is pinned or internal. The
`route-drift` workflow runs it daily against the repository variable
`MATTERWAVE_LIVE_OPENAPI_URL` and opens an issue with the report on drift.
