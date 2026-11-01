"""The response types of the organisation routes: the org itself, its
members, invites and invite links, its model allow-list, sharing, SSO,
two-factor and domain and identity-provider settings, its billing
administration, and the device-login decisions a person makes.

Like the account models, they read their fields through `Wire`. The
secrets some of them carry once -- an invite link's token, an SSO recovery
code -- are kept out of `repr`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ._wire import Wire
from .models import ApiKey

# --- the org -------------------------------------------------------------------


@dataclass(frozen=True)
class Org(Wire):
    """`GET`/`PATCH /api/org`: the organisation, and `role`, the caller's
    own role in it (`owner`, `admin`, `member` or `viewer`)."""

    id: str
    name: str
    created_at: str
    role: str
    request_id: str | None = field(default=None, compare=False)


@dataclass(frozen=True)
class ClosedOrg(Wire):
    """`POST /api/org/close`: what closing did. Conversations are erased
    after `retention_days`; `subscription` is `cancelled`, `none` or
    `failed`. `already_closed` on a retry, which repeats nothing."""

    status: str
    closed_at: str
    already_closed: bool
    conversations_deleted: int
    retention_days: int
    api_keys_revoked: int | None
    invites_revoked: int | None
    invite_links_revoked: int | None
    subscription: str
    members_mailed: int
    members_not_mailed: int
    identity_removed: bool
    request_id: str | None = field(default=None, compare=False)


# --- members, invites and links --------------------------------------------------


@dataclass(frozen=True)
class Member(Wire):
    """One member. `state` is `pending_verification`, `active` or `suspended`."""

    subject: str
    email: str
    name: str | None
    role: str
    state: str
    created_at: str
    last_seen_at: str | None
    request_id: str | None = field(default=None, compare=False)


@dataclass(frozen=True)
class Seats(Wire):
    """Seats in use (members and pending invites), the plan's maximum (None
    for unlimited) and how many are billed."""

    used: int
    max: int | None
    billed: int | None


@dataclass(frozen=True)
class MemberList(Wire):
    """`GET /api/org/members`: every member, the caller's own `role`, and the seats."""

    data: list[Member]
    role: str
    seats: Seats
    request_id: str | None = field(default=None, compare=False)

    def __iter__(self) -> Any:
        return iter(self.data)

    def __len__(self) -> int:
        return len(self.data)


@dataclass(frozen=True)
class Invite(Wire):
    """An invite by email. `status` is `pending`, `accepted`, `expired` or
    `revoked`. On `create` and `resend`, `mail` says whether the mail went
    (`sent` or `failed`, with `detail` saying to resend); the join link
    itself is only ever in the mail."""

    id: str
    email: str
    role: str
    invited_by: str
    created_at: str
    expires_at: str
    status: str
    mail: str | None
    detail: str | None
    request_id: str | None = field(default=None, compare=False)


@dataclass(frozen=True)
class InviteList(Wire):
    """`GET /api/org/invites`: the open invites, newest first."""

    data: list[Invite]
    request_id: str | None = field(default=None, compare=False)

    def __iter__(self) -> Any:
        return iter(self.data)

    def __len__(self) -> int:
        return len(self.data)


@dataclass(frozen=True)
class InviteLookup(Wire):
    """What an invite's token leads to, before it is accepted."""

    org_name: str
    email: str
    role: str
    expires_at: str
    status: str
    request_id: str | None = field(default=None, compare=False)


@dataclass(frozen=True)
class InviteLink(Wire):
    """A shareable join link. `status` is `active`, `expired`, `used_up`
    or `revoked`. `token` and `url` are on the answer to `create` only --
    shown once, kept out of `repr` -- and None everywhere else."""

    id: str
    role: str
    max_uses: int
    uses: int
    created_by: str
    created_at: str
    expires_at: str
    status: str
    token: str | None = field(default=None, repr=False)
    url: str | None = field(default=None, repr=False)
    request_id: str | None = field(default=None, compare=False)


@dataclass(frozen=True)
class InviteLinkList(Wire):
    """`GET /api/org/invite-links`: the links not revoked."""

    data: list[InviteLink]
    request_id: str | None = field(default=None, compare=False)

    def __iter__(self) -> Any:
        return iter(self.data)

    def __len__(self) -> int:
        return len(self.data)


@dataclass(frozen=True)
class LinkLookup(Wire):
    """What a join link leads to: `status` is `active`, `expired` or `used_up`."""

    org_name: str
    role: str
    expires_at: str
    status: str
    request_id: str | None = field(default=None, compare=False)


# --- policies ------------------------------------------------------------------


@dataclass(frozen=True)
class ModelPolicyEntry(Wire):
    """One model the plan offers: whether the org allows it, and `locked`
    for the house model, which is always allowed."""

    id: str
    label: str
    partner: bool
    context_window: int | None
    research_preview: bool
    allowed: bool
    locked: bool


@dataclass(frozen=True)
class ModelPolicy(Wire):
    """`GET`/`PUT /api/org/models`: the org's model allow-list. `restricted`
    is False when every model the plan allows is allowed; `unlisted` names
    allowed ids the gateway no longer serves."""

    house_model: str
    data: list[ModelPolicyEntry]
    unlisted: list[str]
    restricted: bool
    request_id: str | None = field(default=None, compare=False)

    def __iter__(self) -> Any:
        return iter(self.data)

    def __len__(self) -> int:
        return len(self.data)


@dataclass(frozen=True)
class SharingPolicy(Wire):
    """`GET`/`PUT /api/org/sharing`: whether members may share links.
    Turning it off silences every link at once without revoking them."""

    enabled: bool
    request_id: str | None = field(default=None, compare=False)


@dataclass(frozen=True)
class OrgShare(Wire):
    """One share link a member made: never its token. `status` is
    `active`, `expired` or `revoked`."""

    id: str
    conversation_id: str
    title: str
    message_count: int
    include_attachments: bool
    created_at: str
    expires_at: str | None
    revoked_at: str | None
    view_count: int
    status: str
    created_by: str


@dataclass(frozen=True)
class OrgShareList(Wire):
    """`GET /api/org/shares`: every link the org's members made."""

    data: list[OrgShare]
    request_id: str | None = field(default=None, compare=False)

    def __iter__(self) -> Any:
        return iter(self.data)

    def __len__(self) -> int:
        return len(self.data)


@dataclass(frozen=True)
class RevokedOrgShare(Wire):
    status: str
    request_id: str | None = field(default=None, compare=False)


@dataclass(frozen=True)
class RecoveryCode(Wire):
    """The break-glass code that signs an owner in past enforced SSO: valid
    72 hours, once. `code` is shown this once and kept out of `repr`;
    store it offline."""

    id: str
    created_at: str
    expires_at: str
    created_by: str
    used_at: str | None
    code: str = field(repr=False)
    shown_once: bool = True


@dataclass(frozen=True)
class SsoSettings(Wire):
    """`GET`/`PUT /api/org/sso`. `blockers` says why SSO cannot be required
    yet; `recovery` is the code minted when it was just turned on."""

    sso_required: bool
    blockers: list[str]
    recovery: RecoveryCode | None
    request_id: str | None = field(default=None, compare=False)


@dataclass(frozen=True)
class MintedRecoveryCode(Wire):
    """`POST /api/org/sso/recovery-code`: a new code, which supersedes any unused one."""

    recovery: RecoveryCode
    request_id: str | None = field(default=None, compare=False)


@dataclass(frozen=True)
class MfaSettings(Wire):
    """`GET`/`PUT /api/org/mfa`: whether every member needs a second factor."""

    mfa_required: bool
    request_id: str | None = field(default=None, compare=False)


# --- domains and identity providers --------------------------------------------


@dataclass(frozen=True)
class Domain(Wire):
    """A domain the org claims; verified once its DNS challenge is found."""

    domain: str
    verified: bool
    verified_at: str | None
    verified_by: str | None
    created_at: str
    request_id: str | None = field(default=None, compare=False)


@dataclass(frozen=True)
class DomainList(Wire):
    data: list[Domain]
    request_id: str | None = field(default=None, compare=False)

    def __iter__(self) -> Any:
        return iter(self.data)

    def __len__(self) -> int:
        return len(self.data)


@dataclass(frozen=True)
class DnsRecord(Wire):
    """The TXT record that proves a domain: publish `value` at `name`."""

    type: str
    host: str
    name: str
    value: str


@dataclass(frozen=True)
class DomainChallenge(Wire):
    """`GET /api/org/domains/{domain}/challenge`: the record, stable across calls."""

    record: DnsRecord
    request_id: str | None = field(default=None, compare=False)


@dataclass(frozen=True)
class DomainVerification(Wire):
    """`POST /api/org/domains/{domain}/verify`. Not yet `verified` is not an
    error: the record was not found (`found` lists the TXT values that
    were, `detail` says what to do) and the check can be run again."""

    domain: str
    verified: bool
    verified_at: str | None
    verified_by: str | None
    method: str | None
    last_checked_at: str | None
    last_error: str | None
    record: DnsRecord | None
    detail: str | None
    found: list[str]
    request_id: str | None = field(default=None, compare=False)


@dataclass(frozen=True)
class IdentityProvider(Wire):
    """An identity provider the org signs in through. The client secret is
    never returned."""

    provider: str
    kind: str
    name: str
    issuer: str | None
    created_at: str
    request_id: str | None = field(default=None, compare=False)


@dataclass(frozen=True)
class IdentityProviderList(Wire):
    data: list[IdentityProvider]
    request_id: str | None = field(default=None, compare=False)

    def __iter__(self) -> Any:
        return iter(self.data)

    def __len__(self) -> int:
        return len(self.data)


# --- billing administration ----------------------------------------------------


@dataclass(frozen=True)
class BillingLink(Wire):
    """A Stripe page to send the person to: Checkout or the customer portal."""

    url: str
    request_id: str | None = field(default=None, compare=False)


@dataclass(frozen=True)
class Invoice(Wire):
    """One invoice. Amounts are micro-dollars; `hosted_invoice_url` is
    where it is paid and `invoice_pdf` its PDF."""

    id: str
    number: str | None
    status: str
    collection_method: str
    currency: str
    amount_due_micros: int
    amount_remaining_micros: int
    due_at: str | None
    paid_at: str | None
    hosted_invoice_url: str | None
    invoice_pdf: str | None
    issued_at: str
    dispute_status: str | None


@dataclass(frozen=True)
class InvoiceList(Wire):
    """`GET /api/billing/invoices`: the latest 24, newest first."""

    data: list[Invoice]
    request_id: str | None = field(default=None, compare=False)

    def __iter__(self) -> Any:
        return iter(self.data)

    def __len__(self) -> int:
        return len(self.data)


@dataclass(frozen=True)
class PlanChange(Wire):
    """One change of plan or status, who made it and why."""

    from_plan: str | None
    to_plan: str | None
    from_status: str | None
    to_status: str | None
    kind: str
    actor: str
    reason: str | None
    metadata: dict[str, Any]
    created_at: str


@dataclass(frozen=True)
class PlanHistory(Wire):
    """`GET /api/plan/history`: the latest 50 changes, newest first."""

    data: list[PlanChange]
    request_id: str | None = field(default=None, compare=False)

    def __iter__(self) -> Any:
        return iter(self.data)

    def __len__(self) -> int:
        return len(self.data)


# --- device login decisions ----------------------------------------------------


@dataclass(frozen=True)
class DeviceLookup(Wire):
    """Which client asked for a code, before the person decides."""

    user_code: str
    client_name: str | None
    expires_in: int
    request_id: str | None = field(default=None, compare=False)


@dataclass(frozen=True)
class DeviceDecision(Wire):
    """`approved` or `denied`. On approval, `key` is the API key minted for
    the device -- its row, never the secret, which goes only to the
    device's next poll."""

    status: str
    client_name: str | None
    key: ApiKey | None
    request_id: str | None = field(default=None, compare=False)
