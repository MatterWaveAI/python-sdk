# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

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
- Agent contract `1.1.0`, all additions (every 1.0.0 name and default
  behaviour is unchanged):
  - The search rules and a walker: `IgnoreFile` and `SearchIgnore` (nested
    `.gitignore` and `.matterwaveignore` files, `!`, directory-only and
    anchored patterns, `**`, escapes, case ignored, the deny list on top),
    `load_search_ignore(root)` and `walk(root, path)`.
  - The read tools' output as the contract spells it (`OUTPUTS`,
    `TOOL_ERRORS`, `ToolError`): `run_read_file`, `run_list_dir`,
    `run_glob` and `run_grep` over a workspace, and `format_read_file`,
    `format_list_dir`, `format_glob`, `format_grep` and `format_shell` for a
    client that reads files or runs commands itself. The contract carries
    their conformance cases (`CONTRACT["conformance"]`).
  - Tool kinds beyond the seven: `network`, `mcp` and `meta`
    (`TOOL_KINDS`, `KIND_DECISIONS`, `READ_ONLY_DECISIONS`, `ExtraTool`).
    `allowed(tool, mode, kind, read_only=..., path=...)` decides a client's
    own tool by its kind, and `tools_for_mode(mode, extra)` offers them.
  - `refusal(code, ...)` and `APPROVAL_REFUSALS`: the wording for a tool the
    mode denies, an unknown tool, a call the user rejected, a call a
    headless run cannot ask about, and a protected file without approval.
  - `system_prompt(..., extra_tools=..., context_files=...)` (`ContextFile`):
    a "More tools" section and a fenced "Project instructions" section for
    trusted context files; without them the prompt is unchanged.
  - `PROTECTED` adds `.matterwave/`, `MATTERWAVE.md`, `MATTERWAVE.local.md`
    and `AGENTS.md` (`SETTINGS_DIR`, `CONTEXT_FILES`); a write to any of them
    asks in every mode that allows writes (`allowed(..., path=...)`,
    `is_protected`), and `confine(..., write=True, approved=True)` lets an
    approved one through. `PROTECTED_LIST` and `EDIT_CRLF_FALLBACK` are
    exported.
  - New limits: `read_max_file_bytes`, `grep_max_file_bytes` and
    `binary_sniff_bytes`.
  - `confine` refuses, on every platform, a name Windows would read as
    another file or a device: alternate data streams and drive-relative
    names (`stream`), a name ending in a dot or a space (`trailing`), CON,
    PRN, AUX, NUL, COM1-9, LPT1-9, CONIN$ and CONOUT$ with or without an
    extension (`device`), and `\\?\`, `\\.\`, `\??\` and UNC paths (`unc`);
    `windows_alias(path)` names the check on its own. A path the OS maps off the
    root (`nul` on Windows) is now a refusal, never a bare error. The
    contract's `conformance.paths` lists the cases every client must agree
    on, the deny list's case-insensitive matches (`.ENV`, `ID_RSA`) among them.
  - `PROTECTED` also covers what matters when the root is the home folder
    or above it: `.bashrc`, `.bash_profile`, `.profile`, `.zshrc`,
    `.zprofile`, `.ssh/`, `.aws/` and `.config/matterwave` at any depth.
- `tools_of(models, model_id)` and `local_agent_tools_of(me)`: the
  `supports_tools` and `local_agent_tools` flags as `"yes"`, `"no"` or
  `"unknown"` (`ToolsVerdict`), as `vision_of` reads `supports_vision`.
- `MessageParam` and `ToolParam`: `chat.completions.create` and `parse`
  (sync and async) take `messages` and `tools` as plain dicts too, so
  `agent.tools_for_mode(...)` passes mypy without a cast.
- `scripts/gen_agent_contract.py` writes the TypeScript SDK's copy of the
  contract and of the shared conformance cases (`tests/agent_conformance.json`);
  `--check` fails on drift, and the suite and CI run it.
- `scripts/refresh_openapi.py <path-or-url>` refreshes `tests/openapi.json`
  from the app's export (`--check` exits 1 when it is behind), and the `ci`
  workflow takes an `openapi_source` URL on a manual run to test against it.

### Changed

- `tests/openapi.json` refreshed from the app's export: the two penalty
  fields on `ChatCompletionRequest` and the `compacting` stream event.
- CI runs `mypy` beside `ruff` and `pytest`, and checks the TypeScript
  SDK's copy of the agent contract against `contract.json`.
- The agent contract's `list_dir` and `glob` descriptions say what is left
  out (ignored and denied entries; protected ones are listed) and that a
  pattern with no `/` matches names at any depth.

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

### Added in the account families (T-207 to T-229)

- `client.account` (T-207) -- the Account and Preferences panes:
  `rename`, `accept_terms`, `change_password` (session only; every other
  session is ended and counted), the email-change cycle
  (`start_email_change` / `verify_email_change` / `cancel_email_change` /
  `resend_email_change_code` / `email_state`), `sessions`,
  `revoke_session`, `logout_others`, `preferences` /
  `update_preferences` (the route replaces the whole shape), and
  `onboarding_complete`. `delete()` is `DELETE /api/me`: typed
  `DeletedAccount`, the `"delete my account"` phrase checked here as well
  as in the route, and documented as irreversible and session-only.
- `client.exports` and `client.imports` (T-208) -- the Data pane's jobs:
  export `start` / `get` / `download` (the bytes behind the signed link
  *and* the caller's own key), import `start` / `list` / `get` over a
  ChatGPT or Claude export file, with `ExportJob` and `ImportJob`
  carrying the worker's counts.
- `client.members` (T-209) -- `list` (with the caller's role and the
  plan's `seats`), `set_role`, `transfer_ownership`, `remove`, and the
  `invites` and `links` namespaces (`invites.lookup` is anonymous, the
  token is the credential) plus `join_lookup` and `join`.
- `client.org` (T-226) -- `get` / `rename` / `close` (owner; each side
  effect reported, idempotent), `models.get` / `models.set` (the
  allow-list; null lifts it), `sharing.get` / `sharing.set`, the org-wide
  `shares.list` / `shares.revoke`, `sso.get` / `sso.set` (enforcing hands
  back the break-glass code once) / `sso.recovery_code`, `domains`
  (claim / challenge / verify / release), `idp` (list / attach / detach)
  and `mfa.get` / `mfa.set`. Owner or admin by membership row, never a
  token claim -- documented on each class; the audit trail remains
  `client.audit`.
- `client.billing` (T-227) -- `plans` (the public catalog with the price
  table), `switch_plan` (the move to Free; a paid code is a 402 pointing
  at checkout), `plan_history`, `checkout`, `portal`, `status` (any
  member; `billing_enabled` and the dunning schedule included) and
  `invoices`.
- `client.passkeys`, `client.mfa`, `client.accounts` and
  `client.speech` (T-228) -- WebAuthn registration and the login
  ceremony's two halves, TOTP enrolment with the recovery codes shown
  once and proof-of-factor removal and reissue, the browser account
  switcher (`list` / `switch` / `logout` / `logout_all`; cookies are the
  credential, so no key is sent), and transcription (multipart clip,
  optional BCP 47 `language` hint).
- `client.sync` (T-229) -- edge pairing and the mirror it buys:
  `pairings.mint` / `pairings.devices` / `pairings.unpair` (owner or
  admin by session), `pair` (anonymous redemption of a code for the
  device key), `status`, and `push` (one export-shaped conversation,
  upserted by id, with per-message `rejected`). The box is its own
  client: build a `MatterWave` with the paired key and push.
- `Request.data` for form fields beside a multipart upload (speech's
  language hint), carried through `_Config.build`.
- `ORG_ROLES` and the models for every new answer: `SessionInfo`,
  `SessionList`, `SessionRevoked`, `SessionsLoggedOut`, `Preferences`,
  `EmailPreferences`, `NotificationPreferences`, `NameUpdated`,
  `TermsAccepted`, `PasswordChanged`, `EmailChangeStarted`,
  `EmailChangeState`, `EmailChanged`, `EmailChangeCancelled`,
  `OnboardingCompleted`, `DeletedAccount`, `ExportJob`, `ImportJob`,
  `ImportJobList`, `Membership`, `MemberList`, `Seats`, `Invite`,
  `SentInvite`, `InvitePreview`, `InviteLink`, `CreatedInviteLink`,
  `LinkPreview`, `OrgInfo`, `OrgClosed`, `ModelPolicy`, `SharingPolicy`,
  `OrgShare`, `RevokedOrgShare`, `SsoSettings`, `SsoPolicySet`,
  `RecoveryCode`, `DnsRecord`, `DomainChallenge`, `DomainClaim`,
  `DomainVerification`, `IdpConfig`, `MfaPolicy`, `Plan`, `PlanCatalog`,
  `PlanHistory`, `CheckoutLink`, `BillingStatus`, `Invoice`,
  `Passkey`, `PasskeyList`, `PasskeyStart`, `PasskeyLoginStart`,
  `PasskeyLoginFinish`, `MfaState`, `TotpEnrolment`, `TotpConfirmed`,
  `TotpRemoved`, `BrowserAccount`, `AccountSwitched`, `LoggedOut`,
  `Transcription`, `PairingCode`, `EdgeDevice`, `PairedKey`,
  `EdgeSyncStatus` and `SyncOutcome`.

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
