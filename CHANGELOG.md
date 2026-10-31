# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
