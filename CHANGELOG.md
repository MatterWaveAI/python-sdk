# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
