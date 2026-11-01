# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- The sign-in code step of two-factor, on `client.mfa`: `verify_login`
  (a code or a recovery code against the sealed challenge the password step
  handed out), `start_login_enrolment` and `confirm_login_enrolment` (an
  authenticator for a sign-in the org's policy holds until one is
  enrolled). Anonymous: the challenge is the credential. `MfaVerified` and
  `MfaEnrolConfirmed` are their answers.
- Examples, each run by `tests/test_examples.py` against a fake API: `chat`,
  `streaming`, `structured_output`, `webhook_receiver`, `device_sign_in`
  (an API key or a person's token) and `pagination`.
- A generated API reference: the `docs` workflow builds it with pdoc,
  checks every public name has its page (`scripts/check_reference.py`) and
  uploads it as the `api-reference` artifact.
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
- Agent contract `1.2.0`, fixes from the clients' testing. A minor version,
  as the contract's own rule asks, since it adds the refusal `loop` and the
  tool errors `invalid_glob`, `special_file` and `timeout`; a client with an
  exhaustive map over refusals or tool errors must add them. Each
  observable fix is a case in `CONTRACT["conformance"]`:
  - `confine` checks Windows aliases only in the names below the root, so a
    root inside `/x/con/`, `/x/a:b/` or `/x/x./` takes absolute paths into
    it again; `windows_alias(path, root)` is that check on its own.
  - A leading byte-order mark in `.gitignore` or `.matterwaveignore` is
    dropped, and an invalid line (a `[...]` range that runs backwards) is
    skipped, as git does, instead of failing every search and `confine`.
  - glob classes are translated so every regex dialect reads them alike (a
    `]` first is literal, `[^...]` negates too), and a backwards range is
    `GlobError` from `glob_to_regex` and the new `invalid_glob` ToolError
    from `glob_matches`, `run_glob` and `run_grep`.
  - A loop of links is `PathRefused` with the new code `loop`, never a bare
    OSError, and the walker leaves such links out.
  - `run_read_file` and `run_grep` read no more than the cap + 1 bytes; a
    pipe, socket or device (or a link to one) is the new `special_file`
    ToolError, is left out of searches, and is never waited on.
  - `CONTEXT_FILES` is MATTERWAVE.md, AGENTS.md, MATTERWAVE.local.md, so
    passed in that order the user's own file wins; the prompt slot's rules
    say so.
  - `format_shell(..., omitted=N)` formats an output of which a client kept
    only the start and the end; `fill`, `limit` and `check_lines` are
    exported.
  - `run_grep(..., timeout_s=...)` runs the search on a thread and raises
    the new `timeout` ToolError at the deadline. CPython's `re` cannot be
    interrupted inside one match, so a pattern that is exponential on a
    single line still holds the call until that match ends.
  - The `PROTECTED_LIST` and `allowed()` docs name the whole 1.1 list.
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

- `client.with_options(timeout=, max_retries=, max_retry_wait=,
  default_headers=, idempotency_key=)` (sync and async): the client with
  those settings replaced for the calls made through it, so any method --
  the resources, `models.retrieve`, the rest -- can have its own bounds.
  The view shares the connection pool, and closing the client ends it.
- An `Idempotency-Key` header on every write (any method but GET): a fresh
  UUID per call, the same on each of its retries;
  `with_options(idempotency_key=...)` supplies the caller's own.
- `max_retry_wait` (default 60 seconds): a `Retry-After` longer than it
  raises the error at once, with `retry_after` set, rather than waiting.
- `extra_headers=` and `extra_query=` beside `extra_body=` on
  `chat.completions.create` / `parse` and `embeddings.create`.
- `MATTERWAVE_LOG=info` logs a line per response or failure (method, path,
  status, request id, time) to the `matterwave_sdk` logger and stderr;
  `debug` adds each attempt and each retry's wait. Bodies and headers are
  never logged.
- `APIDeprecationWarning`: raised once per route when a response carries
  `Deprecation` or `Sunset`, naming the route and the dates.
- `MatterWaveConfigError`, a `MatterWaveError` and a `ValueError`, for a
  client setting that cannot be used.
- `VERSIONING.md`: the 0.x policy and the API namespace each release speaks.
- Classifiers for Python 3.10 to 3.13; the sdist carries `CHANGELOG.md`,
  `VERSIONING.md` and `docs/`.
- `tests/surface.json` and `tests/parity.json`: the public surface, and
  each difference from the TypeScript SDK with its reason, held by a test
  against that SDK's copy. The README has the table.
- Releases: a tag must name the package version, a CycloneDX SBOM is built,
  and each tag gets a GitHub release with its CHANGELOG section, the wheel,
  the sdist and the SBOM (`scripts/release.py`).
- CI on Python 3.10, 3.11, 3.12 and 3.13 with `ruff format --check`, a
  gitleaks scan of the whole history, and a check that no tracked file
  names a private ticket; `.pre-commit-config.yaml` runs gitleaks on each
  commit; `CODEOWNERS`.

### Changed

- The licence is the Apache License 2.0, in place of MIT. `LICENSE` holds
  its text, `NOTICE` names the copyright holder, the package metadata says
  `Apache-2.0`, and the sdist and the wheel carry both files.
- Each operation has one name, the same in both SDKs (snake_case here,
  camelCase in TypeScript): the resource, then a verb, or a verb and its
  object when the resource holds more than one kind of thing. Renamed:
  `account.email_state` is `account.email_change_state`,
  `account.resend_email_change_code` is `account.resend_email_change`,
  `account.onboarding_complete` is `account.complete_onboarding`,
  `passkeys.start` is `passkeys.start_registration`, `passkeys.verify` is
  `passkeys.verify_registration`, `passkeys.login_start` is
  `passkeys.start_login`, `passkeys.login_finish` is `passkeys.finish_login`,
  `mfa.totp_start` is `mfa.start_totp`, `mfa.totp_confirm` is
  `mfa.confirm_totp`, `mfa.totp_remove` is `mfa.remove_totp`,
  `mfa.recovery_reissue` is `mfa.reissue_recovery_codes`,
  `sync.pairings.mint` is `sync.mint_pairing_code`, `sync.pairings.devices`
  is `sync.devices` and `sync.pairings.unpair` is `sync.unpair`.
  `APP_OPERATIONS` and `CREDENTIALS` use the new names.
- `account.delete` takes `confirm=` by keyword and has no default: the
  caller types the phrase `"delete my account"` every time.
  `org.close(confirm=...)` and `org.idps.attach(name=..., issuer=...,
  client_id=..., client_secret=...)` take their arguments by keyword only.
- A POST or PATCH is retried only after a 429 or a connection that was
  never made. After a 5xx or a reply cut short the first copy may have taken
  effect -- a message stored and billed, a key minted -- so the error is
  raised instead. GET, PUT, DELETE and the read-only MCP calls retry as
  before.
- A bad base URL, a negative `max_retries` and the like raise
  `MatterWaveConfigError`, still a `ValueError`.
- Every action in the workflows is pinned to a commit, and the workflow
  token is read-only unless a job needs more.
- The source and tests are formatted with `ruff format`.
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
- The agent contract's `list_dir` and `glob` descriptions say what is left
  out (ignored and denied entries; protected ones are listed) and that a
  pattern with no `/` matches names at any depth.

### Removed

- The second names for routes that already had one. Use the name beside
  each: `account.rename` (`account.update(full_name=...)`),
  `account.sessions`, `account.revoke_session` and `account.logout_others`
  (`sessions.list`, `sessions.revoke`, `sessions.revoke_others`),
  `account.preferences` and `account.update_preferences`
  (`preferences.get`, `preferences.update`), `exports.start`
  (`exports.create`), `imports.start` (`imports.create`), `billing.plans`
  (`plans.list`), `billing.switch_plan` (`billing.change_plan`),
  `members.list_invites`, `create_invite`, `resend_invite`, `revoke_invite`,
  `lookup_invite` and `accept_invite` (`invites.list`, `create`, `resend`,
  `revoke`, `lookup`, `accept`), `members.list_invite_links`,
  `create_invite_link`, `revoke_invite_link` and `join`
  (`invite_links.list`, `create`, `revoke`, `join`),
  `members.lookup_join_link` and `members.join_lookup`
  (`invite_links.lookup`), and `SessionList.data` (`SessionList.sessions`).

### Fixed

- A retried write could send a message, or mint a key, twice.
- A `Retry-After: 3600` held a call for an hour.
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
  `sign` ships beside it, the platform sender's exact function.
- Per-call `timeout=` and `max_retries=` on `chat.completions.create`,
  `embeddings.create` and the conversation `send` / `edit` / `regenerate`
  turns, overriding the client's settings for that one call — a single
  long generation need not be cut by the 60s default, nor hold the whole
  client at its pace.
- `client.last_response` — the last HTTP response received, success or
  refusal: status, headers, the request id, `Retry-After` and the
  `X-RateLimit-*` window the parsed models leave behind.
- `client.chat.completions.parse(response_model=Model)` — `create` plus
  validation: the answer's text read as JSON and handed to the caller's
  pydantic model (`model_validate`, or v1's `parse_obj`), whose instance
  comes back. A non-JSON answer is a `ParseError`; a schema failure raises
  the model's own error.
- `stream.final_text()` on every stream — the whole answer, accumulated:
  iterate for the pieces, call this for the whole.
- `client.conversations.items()` — every conversation of every page, one
  at a time, the item-level paginator over `pages()`.
- `on_request` / `on_response` client hooks — the request about to go out
  and the response that came back, once per attempt, for logging.
- `close()` now refuses anything further: a request sent after close
  raises `MatterWaveError`, and an in-flight conversation turn is refused
  at its resume rather than retried against a dead client.
- `UnprocessableEntityError` — the 422 validation refusal as its own
  class, the per-field messages joined onto the error.
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

### Added in the account families

- `client.account` -- the Account pane: `update` (the display name),
  `accept_terms`, `enforcement` (the moderation banner's state),
  `change_password` (every other session is ended and counted), the
  email-change cycle (`start_email_change` / `verify_email_change` /
  `cancel_email_change` / `resend_email_change_code` / `email_state`),
  `onboarding_complete`, and `delete()`: `DELETE /api/me`, typed
  `DeletedAccount`, the `"delete my account"` phrase checked here as well
  as in the route, documented as irreversible. The password, email and
  deletion routes refuse an API key and take a person's token or a session.
- `client.preferences` (`get`, `update`: read, merge the changes, write the
  whole object back, since the route replaces it) and `client.sessions`
  (`list`, `revoke`, `revoke_others`; session ids kept out of `repr`).
  `client.account` carries the same routes as `sessions`, `revoke_session`,
  `logout_others`, `preferences` and `update_preferences` (which sends
  exactly the groups passed, the rest going back to their defaults), and
  `rename`, which collapses and bounds the name before sending it.
- `client.exports` and `client.imports` -- the Data pane's jobs: export
  `create` / `get` / `wait` / `download` (the bytes behind the signed link
  *and* the caller's own credential) / `create_and_download`, and
  `document` (the export built inside the request); import `create` /
  `list` / `get` / `wait` over a ChatGPT or Claude export file. `start` is
  `create` under the name the Data pane uses.
- `client.speech.transcribe` -- one clip, multipart, its type named from
  the file's extension or `content_type`, with an optional BCP 47
  `language` hint. A type that is not audio is refused before sending; any
  audio type goes to the route, which answers 415 for one it does not take.
- `client.attachments.scan` / `view` -- an attachment's scan verdict, and
  its bytes through the route's same-origin signed redirect.
- `client.billing` -- `status` (any member; `billing_enabled` and the
  dunning schedule included), and for an owner or admin `checkout`,
  `portal`, `invoices`, `change_plan` (the move to Free; a paid code is a
  402 pointing at checkout; `switch_plan` too) and `plan_history`.
  `client.plans.list` (the catalog with the price table; `billing.plans`
  too) and `client.policy.stats` are public and send no credential.
- `client.org` -- `get` / `rename` / `close` (owner, confirmed by the
  org's name), `models` / `set_models` (the allow-list; None lifts it),
  `sharing` / `set_sharing`, `shares` / `revoke_share`, `sso` / `set_sso`
  (enforcing hands back the break-glass code once) / `mint_recovery_code`,
  `mfa` / `set_mfa`, `org.domains` (claim / challenge / verify / release)
  and `org.idps` (list / attach / detach).
- `client.members` (`list` with the caller's role and the plan's `seats`,
  `set_role`, `transfer_ownership`, `remove`), `client.invites` (`list`,
  `create`, `resend`, `revoke`, the anonymous `lookup`, and `accept`) and
  `client.invite_links` (`list`, `create` -- the token and url shown once --
  `revoke`, the anonymous `lookup`, and `join`). `client.members` answers
  the same routes as `list_invites`, `create_invite`, `resend_invite`,
  `revoke_invite`, `lookup_invite`, `accept_invite`, `list_invite_links`,
  `create_invite_link`, `revoke_invite_link`, `lookup_join_link` (or
  `join_lookup`) and `join`.
- `client.auth.device.lookup` / `approve` / `deny` -- the other side of
  device login, deciding a code a device shows.
- The organisation, member, invite and billing-administration routes gate
  on the caller's membership row: they refuse every API key and take a
  person's token. One-time secrets (an invite link's token and url, an SSO
  recovery code) are kept out of `repr`.
- `client.passkeys`, `client.mfa` and `client.accounts` -- WebAuthn
  registration and the login ceremony's two halves, TOTP enrolment with
  the recovery codes shown once and proof-of-factor removal and reissue,
  and the browser account switcher (`list` / `switch` / `logout` /
  `logout_all`; cookies are the credential, so no key is sent).
- `client.sync` -- edge pairing and the mirror it buys:
  `pairings.mint` / `pairings.devices` / `pairings.unpair` (owner or
  admin, signed in), `pair` (anonymous redemption of a code for the
  device key), `status`, and `push` (one export-shaped conversation,
  upserted by id, with per-message `rejected`). The box is its own
  client: build a `MatterWave` with the paired key and push.
- `Request.form` for form fields beside a multipart upload (speech's
  language hint), and `Request.follow_redirects` for a route that answers
  with a signed link to the bytes.
- `ORG_ROLES` and the models for every new answer: `UpdatedName`,
  `TermsAcceptance`, `Enforcement`, `EnforcementLimit`, `PasswordChanged`,
  `EmailChangeStarted`, `EmailChangeState`, `EmailChanged`,
  `EmailChangeCancelled`, `OnboardingCompleted`, `DeletedAccount`,
  `Preferences`, `EmailPreferences`, `NotificationPreferences`, `Session`,
  `SessionList`, `RevokedSession`, `RevokedSessions`, `AccountExport`,
  `ImportJob`, `ImportJobList`, `Transcription`, `AttachmentScan`,
  `BillingStatus`, `Dunning`, `DunningSchedule`, `DunningStep`,
  `DunningLimits`, `Plan`, `ModelPrice`, `PlanCatalog`, `PolicyStats`,
  `Org`, `ClosedOrg`, `Member`, `Seats`, `MemberList`, `Invite`,
  `InviteList`, `InviteLookup`, `InviteLink`, `InviteLinkList`,
  `LinkLookup`, `ModelPolicy`, `ModelPolicyEntry`, `SharingPolicy`,
  `OrgShare`, `OrgShareList`, `RevokedOrgShare`, `RecoveryCode`,
  `SsoSettings`, `MintedRecoveryCode`, `MfaSettings`, `Domain`,
  `DomainList`, `DnsRecord`, `DomainChallenge`, `DomainVerification`,
  `IdentityProvider`, `IdentityProviderList`, `BillingLink`, `Invoice`,
  `InvoiceList`, `PlanChange`, `PlanHistory`, `DeviceLookup`,
  `DeviceDecision`, `Passkey`, `PasskeyList`, `PasskeyStart`,
  `PasskeyLoginStart`, `PasskeyLoginFinish`, `MfaState`, `TotpEnrolment`,
  `TotpConfirmed`, `TotpRemoved`, `BrowserAccount`, `BrowserAccountList`,
  `AccountSwitched`, `LoggedOut`, `PairingCode`, `EdgeDevice`,
  `EdgeDeviceList`, `PairedKey`, `EdgeSyncStatus` and `SyncOutcome`. The
  account and organisation answers read their fields through one shared
  `from_dict` (`_wire.Wire`), with the rules the other models follow.

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
