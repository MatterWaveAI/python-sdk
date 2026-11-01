# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- A person's token in place of an API key: `access_token=` on `MatterWave`
  and `AsyncMatterWave`, the token itself or a callable asked for it before
  each call (a coroutine function on the async client). The routes that
  manage the organisation, which refuse every key, take it.
- `client.auth.person`: the device authorization grant against the identity
  provider's native client that `/api/auth-config` names (`login`, `start`,
  `poll`, `refresh`, `config`), returning a `PersonToken` with a refresh
  token (`offline_access`), and `session(token, on_refresh=)`, the
  `access_token=` callable that refreshes a minute before expiry.
- `CREDENTIALS`: the credential each operation needs (`public`, `key`,
  `key:<scope>`, `person`), held by the drift test to the per-route
  `security` of the platform's route document; the README tabulates it.
  The missing-credential error now names both ways to give one.
- Route drift tests that run in every checkout: `tests/openapi-full.json`
  (the platform's whole route document, vendored at the commit
  `tests/openapi-full.pin.json` names, by `scripts/refresh_openapi_full.py`)
  holds every route the SDK calls, and every route the platform documents is
  wrapped or listed with its reason in `tests/routes-not-covered.json`.
  `scripts/check_live_routes.py` and the daily `route-drift` workflow hold a
  running deployment's `/openapi.json` to the same pair and open an issue on
  drift. The two checks that needed the platform checkout beside the SDK, and
  skipped without it, are gone.
- `stream.final_completion()` (sync and async) and
  `ChatCompletionAccumulator.completion()`: the whole streamed answer as a
  `ChatCompletion` -- the message with its tool calls, the finish reason, the
  usage, the id and model. A stream that ended without a finish reason raises
  `MatterWaveError` rather than pass for a whole answer.
- Standard Webhooks verification: `webhooks.verify_standard_signature`
  checks `webhook-id`, `webhook-timestamp` and `webhook-signature` (`v1,` and
  base64, keyed by the base64 after `whsec_`), and `webhooks.unwrap` accepts a
  delivery whose MatterWave or Standard Webhooks signature verifies.
  `webhooks.sign_standard` and `webhooks.secret_key_bytes` write and key them.
- Page and item iterators for every cursor-paged list: `audit.pages()` /
  `audit.items()` (the same filters as `audit.list`) and
  `library.search_pages(q)` / `library.search_items(q)`, sync and async.
  `pages_of(fetch)` / `items_of(pages)` and their async twins are the walk
  they share, exported for any list answering `{"data", "next_cursor"}`;
  `conversations.pages()` / `items()` now use it too.
- `frequency_penalty` and `presence_penalty` on `chat.completions.create`
  (sync and async), relayed to the model as OpenAI defines them (-2 to 2);
  `None` leaves them out of the body.
- `CompactingFrame` (`frames.CompactingFrame`, exported): the conversation
  stream's `compacting` event, sent first on a turn that folds earlier
  messages before it is answered, so a client can restart its wait for the
  first token. Not terminal; `compaction` or `notice` follows.
- `stream.final_message()` (sync and async) and `ChatCompletionAccumulator`:
  a stream's text and its tool calls, each reassembled from its
  `delta.tool_calls` fragments, as an `AssistantMessage`; the accumulator
  also keeps the finish reason, usage and model.
- `AssistantMessage.to_message()`, `ToolCall.to_dict()` and
  `tool_message(call_id, content)`: the assistant turn and its `tool`
  answers in the shape the next request's `messages` takes.
- `AppModel.supports_tools` (`GET /api/models`) and `Me.local_agent_tools`
  (`GET /api/me`): whether a model can call tools and whether the org lets
  clients run local agent tools. `None` when the server does not send them.
- `matterwave_sdk.agent`: the agent tool contract the coding clients share,
  version `1.0.0` (`agent.CONTRACT_VERSION`). Its one source is
  `src/matterwave_sdk/agent/contract.json`: the seven local tools
  (`read_file`, `list_dir`, `glob`, `grep`, `edit`, `write_file`,
  `run_shell`) with their JSON Schemas, which are read-only, the approval
  modes `plan`, `ask` (the default), `auto-edit` and `yolo` with what each
  allows and asks for, the default deny list (`.env*`, `*.pem`, `id_*`,
  `.git/`, plus `.matterwaveignore`), the refusal and edit-error sentences,
  the limits, the shell per platform, and the versioned agent-mode system
  prompt. Typed constants (`TOOLS`, `APPROVAL_MODES`, `DEFAULT_DENY`, ...)
  and helpers: `allowed(tool, mode)` (`allow`, `ask` or `deny`),
  `tools_for_mode(mode)` (the `tools` for `/v1`; `plan` gets the read tools
  only), `system_prompt(mode, root)`, `confine(root, path)` (resolve, follow
  links, refuse `..`, outside paths, link escapes and denied files),
  `DenyList`, `apply_edit` (exact, unique replace) and `unified_diff`.
- `scripts/gen_agent_contract.py` writes the TypeScript SDK's copy of the
  contract and of the shared conformance cases (`tests/agent_conformance.json`);
  `--check` fails on drift, and the suite and CI run it.
- `scripts/refresh_openapi.py <path-or-url>` refreshes `tests/openapi.json`
  from the app's export (`--check` exits 1 when it is behind), and the `ci`
  workflow takes an `openapi_source` URL on a manual run to test against it.

### Changed

- The methods whose routes refuse every API key -- `audit.list` /
  `export_csv`, `instructions.org` / `update_org` and `budget.members` /
  `set_overage_cap` / `set_member_default_cap` / `set_member_cap` -- say so:
  each needs a person's token, and its docs quote the 403 a key gets
  ("Membership is managed by a person, not an API key."). They were
  documented as working with a key, which the platform never allowed.
- `tests/openapi.json` refreshed from the app's export: the two penalty
  fields on `ChatCompletionRequest` and the `compacting` stream event.
- CI runs `mypy` beside `ruff` and `pytest`, and checks the TypeScript
  SDK's copy of the agent contract against `contract.json`.

### Fixed

- The README's link to a `docs/package-names.md` that only existed in the
  old monorepo now points at the MatterWaveAI GitHub org.

## [0.5.0] - 2026-10-31

### Added

- `matterwave_sdk.webhooks.unwrap(payload, headers, secret)` — the
  receiver's half of webhooks: verify a delivered event and decode it. The
  `MatterWave-Signature` header (`t=<unix>,v1=<hex HMAC-SHA256>` over
  `<t>.<body>`) is checked with a constant-time compare, any `v1=` element
  may match so a rotated secret verifies against either value during a
  changeover, and the timestamp must sit within 300 seconds (the platform's
  window) of the receiver's clock. `WebhookVerificationError` for a
  missing, stale or wrong signature, or a body that is not a JSON object;
  `sign` ships beside it, the platform sender's exact function (T-201).
- Per-call `timeout=` and `max_retries=` on `chat.completions.create`,
  `embeddings.create` and the conversation `send` / `edit` / `regenerate`
  turns, overriding the client's settings for that one call — a single
  long generation need not be cut by the 60s default, nor hold the whole
  client at its pace (T-202).
- `client.last_response` — the last HTTP response received, success or
  refusal: status, headers, the request id, `Retry-After` and the
  `X-RateLimit-*` window the parsed models leave behind (T-203).
- `client.chat.completions.parse(response_model=Model)` — `create` plus
  validation: the answer's text read as JSON and handed to the caller's
  pydantic model (`model_validate`, or v1's `parse_obj`), whose instance
  comes back. A non-JSON answer is a `ParseError`; a schema failure raises
  the model's own error (T-204).
- `stream.final_text()` on every stream — the whole answer, accumulated:
  iterate for the pieces, call this for the whole (T-205).
- `client.conversations.items()` — every conversation of every page, one
  at a time, the item-level paginator over `pages()` (T-205).
- `on_request` / `on_response` client hooks — the request about to go out
  and the response that came back, once per attempt, for logging (T-205).
- `close()` now refuses anything further: a request sent after close
  raises `MatterWaveError`, and an in-flight conversation turn is refused
  at its resume rather than retried against a dead client (T-206).
- `UnprocessableEntityError` — the 422 validation refusal as its own
  class, the per-field messages joined onto the error (T-206).
- `client.audit.list()` and `client.audit.export_csv()` — the org's audit
  trail (`GET /api/org/audit`, `GET /api/org/audit.csv`), the reads the org
  pane's audit tab makes: one page of `audit_events` newest first (`data`,
  `next_cursor`, `actions`) narrowed by `event` (an action exactly or as a
  dotted prefix), `actor` (subject or email, case-insensitively),
  `since`/`until`, `cursor` and `limit` (1 to 200, 50 when omitted), and
  every matching row as the CSV the server streams. Owner or admin by
  membership row: the platform refuses an API key on these routes, so they
  answer a signed-in session.
- `AuditPage` and `AuditEvent` models for the rows: `id`, `occurred_at`,
  `actor` (the writer's subject), `actor_email` where one is resolved,
  `action`, `target` and the writer's `detail`.

### Documentation

- "Connect your editor to MatterWave": a README section and a `docs/mcp.md`
  walk-through -- getting a key (Settings > API keys or device login),
  copy-paste MCP config for Claude Desktop, VS Code / Copilot and Cursor,
  the six tools and their scopes, the errors the two refusal shapes arrive
  as, the no-OAuth limitation and the security notes. `examples/mcp_via_sdk.py`
  drives `client.mcp` end to end (list tools, create, send, search). The old
  `examples/mcp_client.py`, built on the official `mcp` protocol library, is
  removed: one example per SDK.

## [0.4.0] - 2026-10-30

### Added

- `client.admin.announcements` — the operator's announcement CRUD
  (`/api/admin/announcements`), the routes the admin console's Announcements
  tab speaks: `list()` (every row, newest first, each with its `state`),
  `create(message, *, severity="info", link=None, starts_at=None,
  ends_at=None, dismissible=True)`, `update(id, ...)` (only what is passed
  changes; `link=None` clears the link, `ends_at=None` makes the window
  open-ended, `end_now=True` ends it at the server's clock) and
  `delete(id)`. These are operator routes: they need the platform `admin`
  role, and an API key with user scopes cannot call them. The signed-in
  read side stays `client.announcements.list()`.
- `AdminAnnouncement` and `AdminAnnouncementList` models for the operator's
  view: every column, who wrote and last changed it, and where the window
  stands (`state`: `active`, `scheduled` or `ended`).

## [0.3.0] - 2026-10-30

### Added

- `client.notifications.list()` / `read(id)` / `read_all()` — the bell in the
  web app's header: the caller's own rows, newest first, with the unread count;
  mark one read (`NotFoundError` for an id that is not the caller's) or all.
- `client.announcements.list()` — the operator's banner, active rows only,
  most severe first.
- `client.entitlements.get()` — what the caller's org may do: the plan, its
  allowance and caps, and, for a downgraded org, `writes_blocked` with
  `read_only_reason`. Any member may read it; complements `app_models()`'s
  per-model `included`.
- `client.conversations.report(id, kind, *, message_id=None, note=None)` —
  file a report on a conversation or one of its messages; `kind` is `unsafe`
  or `unhelpful`, and the answer (`FiledReport`) carries the report's `id`.
- Typed models for all of the above: `Notification`, `NotificationList`,
  `NotificationsReadAll`, `Announcement`, `AnnouncementList`, `Entitlements`
  with its `EntitlementLimits`, `FiledReport`, and the `REPORT_KINDS`
  vocabulary.
- CI on every push and pull request (lint and test); the publish workflow
  stays tag-only.

Releases before 0.3.0 predate this changelog; see the git history.
