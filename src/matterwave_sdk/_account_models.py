"""The response types of the account and data routes: the caller's name,
terms, preferences and sessions, data exports and imports, speech, an
attachment's scan, the enforcement banner, the billing status, and the
public plan and policy figures.

They read their fields through `Wire`: a field the body lacks gets its
type's empty value, and one the platform adds later is dropped. The
platform documents these bodies as open objects, so the fields here are
the ones its routes write today.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ._wire import Wire
from .models import Entitlements

# --- the account ---------------------------------------------------------------


@dataclass(frozen=True)
class UpdatedName(Wire):
    """`PATCH /api/me`: the display name as saved, whitespace collapsed."""

    name: str
    request_id: str | None = field(default=None, compare=False)


@dataclass(frozen=True)
class TermsAcceptance(Wire):
    """`POST /api/me/accept-terms`: the version the person accepted and when."""

    terms_version: str
    terms_accepted_at: str | None
    terms_current: bool
    request_id: str | None = field(default=None, compare=False)


@dataclass(frozen=True)
class EnforcementLimit(Wire):
    messages: int
    window_s: int


@dataclass(frozen=True)
class Enforcement(Wire):
    """`GET /api/me/enforcement`: the banner's state. `state` is `none`, or
    `warned`, `rate_limited` or `suspended` with the reason, since when,
    until when (None for open-ended), whether it holds the person (`user`)
    or the whole org, and where to appeal; `limit` is the window a
    `rate_limited` caller is held to."""

    state: str
    reason: str | None
    since: str | None
    expires_at: str | None
    scope: str | None
    appeal_path: str | None
    limit: EnforcementLimit | None
    request_id: str | None = field(default=None, compare=False)


@dataclass(frozen=True)
class EmailPreferences(Wire):
    """Which mail the person gets. Billing receipts cannot be turned off."""

    product_updates: bool
    usage_alerts: bool
    billing_receipts: bool


@dataclass(frozen=True)
class NotificationPreferences(Wire):
    budget_warn: bool


@dataclass(frozen=True)
class Preferences(Wire):
    """`GET`/`PUT /api/me/preferences`. `locale` is a BCP-47 tag (`en`,
    `en-GB`), or None for the browser's."""

    email: EmailPreferences
    locale: str | None
    notifications: NotificationPreferences
    request_id: str | None = field(default=None, compare=False)


@dataclass(frozen=True)
class Session(Wire):
    """One signed-in browser. `id` is the session cookie's own value -- the
    one field that would authenticate -- so it is left out of `repr`."""

    id: str = field(repr=False)
    current: bool
    created_at: str
    last_seen_at: str
    user_agent: str | None
    ip: str | None


@dataclass(frozen=True)
class SessionList(Wire):
    """`GET /api/me/sessions`: every browser signed in as the caller.
    `current_id` is the calling session's (None for a key or a token)."""

    current_id: str | None = field(repr=False)
    sessions: list[Session]
    request_id: str | None = field(default=None, compare=False)

    def __iter__(self) -> Any:
        return iter(self.sessions)

    def __len__(self) -> int:
        return len(self.sessions)


@dataclass(frozen=True)
class RevokedSession(Wire):
    """`DELETE /api/me/sessions/{id}`: `current` when it was the caller's own."""

    status: str
    current: bool
    request_id: str | None = field(default=None, compare=False)


@dataclass(frozen=True)
class RevokedSessions(Wire):
    """`POST /api/me/sessions/revoke-others`: how many were signed out."""

    revoked: int
    request_id: str | None = field(default=None, compare=False)


# --- exports and imports -------------------------------------------------------


@dataclass(frozen=True)
class AccountExport(Wire):
    """One export of the account's data. `status` is `pending`, `ready`,
    `failed` or `expired`; `download_url` is set only when it is `ready`: a
    same-origin path signed for the owner, minted fresh on each read and
    left out of `repr`."""

    id: str
    status: str
    size_bytes: int | None
    created_at: str
    completed_at: str | None
    expires_at: str | None
    download_url: str | None = field(default=None, repr=False)
    request_id: str | None = field(default=None, compare=False)


@dataclass(frozen=True)
class ImportJob(Wire):
    """One import of another assistant's export. `status` is `pending`,
    `running`, `done` or `failed` (`error` says why); `source` is
    `chatgpt` or `claude`. Importing the same file again creates nothing
    new: its conversations count as `conversations_existing`."""

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
    error: str | None
    created_at: str
    started_at: str | None
    completed_at: str | None
    request_id: str | None = field(default=None, compare=False)


@dataclass(frozen=True)
class ImportJobList(Wire):
    """`GET /api/me/imports`: the latest ten, newest first."""

    data: list[ImportJob]
    request_id: str | None = field(default=None, compare=False)

    def __iter__(self) -> Any:
        return iter(self.data)

    def __len__(self) -> int:
        return len(self.data)


# --- speech and attachments ----------------------------------------------------


@dataclass(frozen=True)
class Transcription(Wire):
    """`POST /api/speech/transcribe`: the clip's words. The audio is never stored."""

    text: str
    request_id: str | None = field(default=None, compare=False)


@dataclass(frozen=True)
class AttachmentScan(Wire):
    """`GET /api/attachments/{id}/scan`: `uploaded`, `scanning`, `clean`,
    `infected` or `scan_failed`. Only a `clean` file can be read."""

    id: str
    scan_status: str
    request_id: str | None = field(default=None, compare=False)


# --- billing, plans and policy -------------------------------------------------


@dataclass(frozen=True)
class DunningSchedule(Wire):
    warn_at: str
    warn_final_at: str
    limited_at: str
    suspended_at: str | None
    days_limit_at: str


@dataclass(frozen=True)
class DunningStep(Wire):
    stage: str
    at: str


@dataclass(frozen=True)
class DunningLimits(Wire):
    stops_overage: bool
    blocks_new_keys: bool
    blocks_upgrades: bool


@dataclass(frozen=True)
class Dunning(Wire):
    """A late payment and what happens next: the stage, how overdue and
    how much, the dates of each step, and the link to pay (`pay_url`)."""

    stage: str
    source: str
    first_overdue_at: str
    days_overdue: int
    outstanding_micros: int
    schedule: DunningSchedule | None
    next_step: DunningStep | None
    overage_stopped: bool
    limited: DunningLimits | None
    auto_suspend: bool
    pay_url: str | None


@dataclass(frozen=True)
class BillingStatus:
    """`GET /api/billing/status`: what a billing page renders, in one read
    any member may make -- the org's `entitlements`, whether billing is on
    at all, how the org pays, and `dunning` when a payment is late (None in
    good standing)."""

    entitlements: Entitlements
    billing_enabled: bool
    collection_method: str
    days_until_due: int | None
    dunning: Dunning | None
    request_id: str | None = field(default=None, compare=False)

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, request_id: str | None = None) -> BillingStatus:
        days = data.get("days_until_due")
        dunning = data.get("dunning")
        return cls(
            entitlements=Entitlements.from_dict(data),
            billing_enabled=data.get("billing_enabled") is True,
            collection_method=str(data.get("collection_method") or "charge_automatically"),
            days_until_due=days if isinstance(days, int) and not isinstance(days, bool) else None,
            dunning=Dunning.from_dict(dunning) if isinstance(dunning, dict) else None,
            request_id=request_id,
        )


@dataclass(frozen=True)
class Plan(Wire):
    """One plan on the price list. Prices are in cents; `limits` is the
    plan's raw limits object (the keys `Entitlements.limits` reads)."""

    code: str
    name: str
    price_cents_monthly: int
    price_cents_annual: int
    included_tokens: int
    included_tokens_per_seat: int | None
    per_seat: bool
    overage_enabled: bool
    default_overage_cap_micros: int
    trial_days: int
    models: list[str]
    limits: dict[str, Any]


@dataclass(frozen=True)
class ModelPrice(Wire):
    """A model's list price per million tokens, and the multiplier its use
    is counted at against a plan's allowance."""

    id: str
    label: str
    multiplier: int
    input_usd_per_m: float
    output_usd_per_m: float
    partner: bool


@dataclass(frozen=True)
class PlanCatalog(Wire):
    """`GET /api/plans`: every plan, cheapest first, the model prices, and
    the deployment's default model. Public: no credential is sent."""

    data: list[Plan]
    models: list[ModelPrice]
    default_model: str
    request_id: str | None = field(default=None, compare=False)

    def __iter__(self) -> Any:
        return iter(self.data)

    def __len__(self) -> int:
        return len(self.data)


@dataclass(frozen=True)
class PolicyStats(Wire):
    """`GET /api/policy/stats`: the public policy page's counts. `reports`
    holds `by_kind` and `unsafe_by_status`; `enforcement` holds `by_state`,
    `active` and `appeals`; each is a map of name to count."""

    reports: dict[str, dict[str, int]]
    enforcement: dict[str, dict[str, int]]
    request_id: str | None = field(default=None, compare=False)
