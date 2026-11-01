"""The native API clients: `keys`, `usage`, `audit`, `webhooks`,
`attachments`, `library`, `shares`, `budget`, `notifications`,
`announcements`, `entitlements`, the `admin` namespace and the account
families -- `account`, `exports`/`imports`, `members`, `org`, `billing`,
`passkeys`, `mfa`, `accounts`, `speech` and `sync` -- sync and async.
Each method is one route; the request is built in `_native` and the answer
parsed into `models`, so the two halves here differ only in `await`.

These are the routes the web app's settings panes use, open to an API key
as its owner. Managing keys needs a key with `keys:manage` (or a session),
webhooks a key with `webhooks:manage`, the org usage report
(`usage.org`, `usage.export_csv`) a key with `usage:read`, and moving a
budget cap an owner or admin. The audit trail (`audit.list`,
`audit.export_csv`) is the one exception among the reads: the routes gate
on the membership row alone, so an API key is refused there outright and a
signed-in session is what answers. The scope never widens the owner's role:
webhooks still need the owner to be an org owner or admin, and a member's
`usage:read` key sees only their own row. Notifications are the caller's
own rows, announcements are the operator's banner anyone signed in reads,
and entitlements are the plan facts any member of the org may read. The
`admin` namespace is the exception to all of it: the operator's own
routes, the platform `admin` role, not an API key's.

The account families keep the routes' own scoping, documented on each
class: the password, email and deletion writes refuse an API key outright
(a session answers); the org and members panes gate on the membership row
(owner or admin); and the machine half of `sync` wants the device key the
box paired with, not a session.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import IO, TYPE_CHECKING, Any

from . import _base
from . import _native as req
from ._base import UNSET, Request
from .models import (
    AccountSwitched,
    AdminAnnouncement,
    AdminAnnouncementList,
    AnnouncementList,
    ApiKey,
    ApiKeyList,
    Attachment,
    AttachmentLimits,
    AuditPage,
    BillingStatus,
    BrowserAccountList,
    Budget,
    CheckoutLink,
    Conversation,
    ConversationTags,
    CreatedInviteLink,
    CreatedShare,
    DeletedAccount,
    DomainChallenge,
    DomainClaim,
    DomainClaimList,
    DomainVerification,
    EdgeDeviceList,
    EdgeSyncStatus,
    EmailChangeCancelled,
    EmailChanged,
    EmailChangeStarted,
    EmailChangeState,
    Entitlements,
    ExportJob,
    Folder,
    IdpConfig,
    IdpConfigList,
    ImportJob,
    ImportJobList,
    InviteLinkList,
    InviteList,
    InvitePreview,
    InvoiceList,
    Library,
    LinkPreview,
    LoggedOut,
    MemberBudget,
    MemberBudgetList,
    MemberList,
    Membership,
    MfaPolicy,
    MfaState,
    ModelPolicy,
    NameUpdated,
    Notification,
    NotificationList,
    NotificationsReadAll,
    OnboardingCompleted,
    OrgClosed,
    OrgInfo,
    OrgShareList,
    OrgUsage,
    PairedKey,
    PairingCode,
    Passkey,
    PasskeyList,
    PasskeyLoginFinish,
    PasskeyLoginStart,
    PasskeyStart,
    PasswordChanged,
    PlanCatalog,
    PlanHistory,
    Preferences,
    RecoveryCode,
    RevokedOrgShare,
    SearchResults,
    SentInvite,
    SessionList,
    SessionRevoked,
    SessionsLoggedOut,
    SharedConversation,
    ShareList,
    SharingPolicy,
    SsoPolicySet,
    SsoSettings,
    SyncOutcome,
    Tag,
    TermsAccepted,
    TotpConfirmed,
    TotpEnrolment,
    TotpRemoved,
    Transcription,
    UsageBalance,
    WebhookDelivery,
    WebhookDeliveryList,
    WebhookEndpoint,
    WebhookEndpointList,
)

if TYPE_CHECKING:
    from ._client import AsyncMatterWave, MatterWave


class _Sync:
    def __init__(self, client: MatterWave) -> None:
        self._client = client

    def _object(self, request: Request) -> tuple[dict[str, Any], str | None]:
        response = self._client._send(request)
        return _base.parse_json_object(response), _base.request_id_of(response)

    def _none(self, request: Request) -> None:
        self._client._send(request).close()


class _Async:
    def __init__(self, client: AsyncMatterWave) -> None:
        self._client = client

    async def _object(self, request: Request) -> tuple[dict[str, Any], str | None]:
        response = await self._client._send(request)
        return _base.parse_json_object(response), _base.request_id_of(response)

    async def _none(self, request: Request) -> None:
        await (await self._client._send(request)).aclose()


# --- keys ----------------------------------------------------------------------


class Keys(_Sync):
    """`/api/keys`: the org's API keys. The secret is in the answer to
    `create` and `rotate` (`ApiKey.key`) and nowhere else, ever."""

    def list(self) -> ApiKeyList:
        """Every key, revoked ones included; never a secret."""
        body, rid = self._object(req.list_keys_request())
        return ApiKeyList.from_dict(body, request_id=rid)

    def create(
        self,
        name: str,
        *,
        scopes: Sequence[str] | None = None,
        expires_in_days: int | None = None,
        expires_at: datetime | str | None = None,
        allowed_cidrs: Sequence[str] | None = None,
        rpm_limit: int | None = None,
    ) -> ApiKey:
        """Mint a key. `scopes` omitted is the default (`chat:read`,
        `chat:write`); `expires_in_days` (1-3650) or `expires_at` (with a
        timezone), not both; `allowed_cidrs` the networks it may be used
        from; `rpm_limit` a ceiling under the platform's."""
        body, rid = self._object(
            req.create_key_request(
                name,
                scopes=scopes,
                expires_in_days=expires_in_days,
                expires_at=expires_at,
                allowed_cidrs=allowed_cidrs,
                rpm_limit=rpm_limit,
            )
        )
        return ApiKey.from_dict(body, request_id=rid)

    def update(
        self,
        key_id: str,
        *,
        name: str | None = None,
        scopes: Sequence[str] | None = UNSET,
        expires_in_days: int | None = UNSET,
        expires_at: datetime | str | None = UNSET,
        allowed_cidrs: Sequence[str] | None = UNSET,
        rpm_limit: int | None = UNSET,
    ) -> ApiKey:
        """`PATCH /api/keys/{id}`: change a key in place; the secret stays.
        A keyword left out is left alone; None clears it (the default
        scopes, no expiry, any network, the platform's limit)."""
        body, rid = self._object(
            req.update_key_request(
                key_id,
                name=name,
                scopes=scopes,
                expires_in_days=expires_in_days,
                expires_at=expires_at,
                allowed_cidrs=allowed_cidrs,
                rpm_limit=rpm_limit,
            )
        )
        return ApiKey.from_dict(body, request_id=rid)

    def rotate(self, key_id: str) -> ApiKey:
        """A new secret for the same key. The old one works until
        `previous_valid_until`, so a deploy can swap the value."""
        body, rid = self._object(req.rotate_key_request(key_id))
        return ApiKey.from_dict(body, request_id=rid)

    def revoke(self, key_id: str) -> None:
        """Revoke, for good; the row stays as evidence. A key may always revoke itself."""
        self._none(req.revoke_key_request(key_id))


class AsyncKeys(_Async):
    async def list(self) -> ApiKeyList:
        body, rid = await self._object(req.list_keys_request())
        return ApiKeyList.from_dict(body, request_id=rid)

    async def create(
        self,
        name: str,
        *,
        scopes: Sequence[str] | None = None,
        expires_in_days: int | None = None,
        expires_at: datetime | str | None = None,
        allowed_cidrs: Sequence[str] | None = None,
        rpm_limit: int | None = None,
    ) -> ApiKey:
        body, rid = await self._object(
            req.create_key_request(
                name,
                scopes=scopes,
                expires_in_days=expires_in_days,
                expires_at=expires_at,
                allowed_cidrs=allowed_cidrs,
                rpm_limit=rpm_limit,
            )
        )
        return ApiKey.from_dict(body, request_id=rid)

    async def update(
        self,
        key_id: str,
        *,
        name: str | None = None,
        scopes: Sequence[str] | None = UNSET,
        expires_in_days: int | None = UNSET,
        expires_at: datetime | str | None = UNSET,
        allowed_cidrs: Sequence[str] | None = UNSET,
        rpm_limit: int | None = UNSET,
    ) -> ApiKey:
        body, rid = await self._object(
            req.update_key_request(
                key_id,
                name=name,
                scopes=scopes,
                expires_in_days=expires_in_days,
                expires_at=expires_at,
                allowed_cidrs=allowed_cidrs,
                rpm_limit=rpm_limit,
            )
        )
        return ApiKey.from_dict(body, request_id=rid)

    async def rotate(self, key_id: str) -> ApiKey:
        body, rid = await self._object(req.rotate_key_request(key_id))
        return ApiKey.from_dict(body, request_id=rid)

    async def revoke(self, key_id: str) -> None:
        await self._none(req.revoke_key_request(key_id))


# --- usage ---------------------------------------------------------------------


class UsageClient(_Sync):
    """What the org has used: the balance this period, the per-member month,
    and the same month as CSV."""

    def get(self) -> UsageBalance:
        """`GET /api/me/usage`: the org's balance this period, by model and by day."""
        body, rid = self._object(req.my_usage_request())
        return UsageBalance.from_dict(body, request_id=rid)

    def org(self, *, period: str | None = None) -> OrgUsage:
        """`GET /api/org/usage`: per member for one calendar month
        (`period` like `2026-10`, this month when omitted). An owner or
        admin sees everyone; anyone else their own row."""
        body, rid = self._object(req.org_usage_request(period))
        return OrgUsage.from_dict(body, request_id=rid)

    def export_csv(self, *, period: str | None = None) -> str:
        """`GET /api/org/usage.csv`: the rows of `org`, scoped the same way,
        as the CSV text the server wrote (`cost_usd` to six places)."""
        return self._client._send(req.org_usage_csv_request(period)).text


class AsyncUsageClient(_Async):
    async def get(self) -> UsageBalance:
        body, rid = await self._object(req.my_usage_request())
        return UsageBalance.from_dict(body, request_id=rid)

    async def org(self, *, period: str | None = None) -> OrgUsage:
        body, rid = await self._object(req.org_usage_request(period))
        return OrgUsage.from_dict(body, request_id=rid)

    async def export_csv(self, *, period: str | None = None) -> str:
        return (await self._client._send(req.org_usage_csv_request(period))).text


# --- audit ---------------------------------------------------------------------


class Audit(_Sync):
    """`/api/org/audit`: the org's trail of who changed what, the reads the
    org pane's audit tab makes. Owner or admin, and by membership row rather
    than a token claim: the platform refuses an API key on these routes
    outright, so they answer a signed-in session."""

    def list(
        self,
        *,
        event: str | None = None,
        actor: str | None = None,
        since: datetime | str | None = None,
        until: datetime | str | None = None,
        cursor: str | None = None,
        limit: int | None = None,
    ) -> AuditPage:
        """One page, newest first. `event` matches an action exactly or as a
        dotted prefix (`org.sso` finds `org.sso.enforced`); `actor` matches
        a subject or an email, case-insensitively; `since`/`until` bound the
        window (a naive datetime is read as UTC); `cursor` is a previous
        page's `next_cursor`; `limit` is 1 to 200 (50 when omitted). A
        cursor this API did not mint is the server's 422."""
        body, rid = self._object(
            req.org_audit_request(
                event=event, actor=actor, since=since, until=until, cursor=cursor, limit=limit
            )
        )
        return AuditPage.from_dict(body, request_id=rid)

    def export_csv(
        self,
        *,
        event: str | None = None,
        actor: str | None = None,
        since: datetime | str | None = None,
        until: datetime | str | None = None,
    ) -> str:
        """`GET /api/org/audit.csv`: every matching row, as the CSV text the
        server streamed (the columns of `AuditEvent`, `detail` as JSON)."""
        return self._client._send(
            req.org_audit_csv_request(event=event, actor=actor, since=since, until=until)
        ).text


class AsyncAudit(_Async):
    async def list(
        self,
        *,
        event: str | None = None,
        actor: str | None = None,
        since: datetime | str | None = None,
        until: datetime | str | None = None,
        cursor: str | None = None,
        limit: int | None = None,
    ) -> AuditPage:
        body, rid = await self._object(
            req.org_audit_request(
                event=event, actor=actor, since=since, until=until, cursor=cursor, limit=limit
            )
        )
        return AuditPage.from_dict(body, request_id=rid)

    async def export_csv(
        self,
        *,
        event: str | None = None,
        actor: str | None = None,
        since: datetime | str | None = None,
        until: datetime | str | None = None,
    ) -> str:
        return (
            await self._client._send(
                req.org_audit_csv_request(event=event, actor=actor, since=since, until=until)
            )
        ).text


# --- webhooks ------------------------------------------------------------------


class Webhooks(_Sync):
    """`/api/org/webhooks`: outbound webhook endpoints and their delivery
    log. The signing secret is in the answer to `create` and
    `rotate_secret` (`WebhookEndpoint.secret`) and nowhere else."""

    def list(self) -> WebhookEndpointList:
        """The endpoints, and every event type one may subscribe to."""
        body, rid = self._object(req.list_webhooks_request())
        return WebhookEndpointList.from_dict(body, request_id=rid)

    def create(
        self, url: str, events: Sequence[str], *, description: str | None = None
    ) -> WebhookEndpoint:
        """A new endpoint for a public https URL, subscribed to `events`."""
        body, rid = self._object(req.create_webhook_request(url, events, description))
        return WebhookEndpoint.from_dict(body, request_id=rid)

    def get(self, endpoint_id: str) -> WebhookEndpoint:
        body, rid = self._object(req.get_webhook_request(endpoint_id))
        return WebhookEndpoint.from_dict(body, request_id=rid)

    def update(
        self,
        endpoint_id: str,
        *,
        url: str | None = None,
        events: Sequence[str] | None = None,
        description: str | None = None,
        enabled: bool | None = None,
    ) -> WebhookEndpoint:
        """Change what was passed; `enabled=True` re-enables one the failures switched off."""
        body, rid = self._object(
            req.update_webhook_request(
                endpoint_id, url=url, events=events, description=description, enabled=enabled
            )
        )
        return WebhookEndpoint.from_dict(body, request_id=rid)

    def delete(self, endpoint_id: str) -> None:
        self._none(req.delete_webhook_request(endpoint_id))

    def rotate_secret(self, endpoint_id: str) -> WebhookEndpoint:
        """A new signing secret, shown once; the old one stops signing now."""
        body, rid = self._object(req.rotate_webhook_secret_request(endpoint_id))
        return WebhookEndpoint.from_dict(body, request_id=rid)

    def test(self, endpoint_id: str) -> WebhookDelivery:
        """Send a `ping` now. Answers whether or not the receiver took it:
        the delivery's `status` says which."""
        body, rid = self._object(req.test_webhook_request(endpoint_id))
        return WebhookDelivery.from_dict(body, request_id=rid)

    def deliveries(self, endpoint_id: str, *, status: str | None = None) -> WebhookDeliveryList:
        """The delivery log, filtered to `pending`, `sending`, `delivered` or `failed`."""
        body, rid = self._object(req.list_deliveries_request(endpoint_id, status))
        return WebhookDeliveryList.from_dict(body, request_id=rid)

    def redeliver(self, endpoint_id: str, delivery_id: str) -> WebhookDelivery:
        """Queue the same event again as a new delivery; its result lands in the log."""
        body, rid = self._object(req.redeliver_request(endpoint_id, delivery_id))
        return WebhookDelivery.from_dict(body, request_id=rid)


class AsyncWebhooks(_Async):
    async def list(self) -> WebhookEndpointList:
        body, rid = await self._object(req.list_webhooks_request())
        return WebhookEndpointList.from_dict(body, request_id=rid)

    async def create(
        self, url: str, events: Sequence[str], *, description: str | None = None
    ) -> WebhookEndpoint:
        body, rid = await self._object(req.create_webhook_request(url, events, description))
        return WebhookEndpoint.from_dict(body, request_id=rid)

    async def get(self, endpoint_id: str) -> WebhookEndpoint:
        body, rid = await self._object(req.get_webhook_request(endpoint_id))
        return WebhookEndpoint.from_dict(body, request_id=rid)

    async def update(
        self,
        endpoint_id: str,
        *,
        url: str | None = None,
        events: Sequence[str] | None = None,
        description: str | None = None,
        enabled: bool | None = None,
    ) -> WebhookEndpoint:
        body, rid = await self._object(
            req.update_webhook_request(
                endpoint_id, url=url, events=events, description=description, enabled=enabled
            )
        )
        return WebhookEndpoint.from_dict(body, request_id=rid)

    async def delete(self, endpoint_id: str) -> None:
        await self._none(req.delete_webhook_request(endpoint_id))

    async def rotate_secret(self, endpoint_id: str) -> WebhookEndpoint:
        body, rid = await self._object(req.rotate_webhook_secret_request(endpoint_id))
        return WebhookEndpoint.from_dict(body, request_id=rid)

    async def test(self, endpoint_id: str) -> WebhookDelivery:
        body, rid = await self._object(req.test_webhook_request(endpoint_id))
        return WebhookDelivery.from_dict(body, request_id=rid)

    async def deliveries(
        self, endpoint_id: str, *, status: str | None = None
    ) -> WebhookDeliveryList:
        body, rid = await self._object(req.list_deliveries_request(endpoint_id, status))
        return WebhookDeliveryList.from_dict(body, request_id=rid)

    async def redeliver(self, endpoint_id: str, delivery_id: str) -> WebhookDelivery:
        body, rid = await self._object(req.redeliver_request(endpoint_id, delivery_id))
        return WebhookDelivery.from_dict(body, request_id=rid)


# --- attachments ---------------------------------------------------------------


class Attachments(_Sync):
    """`/api/attachments`: files to attach to a message (`attachment_ids`
    on `conversations.send`)."""

    def upload(
        self, file: bytes | IO[bytes], filename: str, *, conversation_id: str | None = None
    ) -> Attachment:
        """Upload one file. The server decides its type from the bytes and
        scans it; it is readable once `scan_status` is `clean`."""
        body, rid = self._object(req.upload_attachment_request(file, filename, conversation_id))
        return Attachment.from_dict(body, request_id=rid)

    def get(self, attachment_id: str) -> Attachment:
        """The metadata, with a fresh `download_url`."""
        body, rid = self._object(req.get_attachment_request(attachment_id))
        return Attachment.from_dict(body, request_id=rid)

    def delete(self, attachment_id: str) -> None:
        """Mark it deleted; the stored bytes go on the sweeper's next pass."""
        self._none(req.delete_attachment_request(attachment_id))

    def download(self, attachment: Attachment | str) -> bytes:
        """The file's bytes, from an attachment's `download_url` (or the URL
        itself). The URL expires in minutes: `get` mints a fresh one."""
        url = attachment if isinstance(attachment, str) else attachment.download_url
        if not url:
            raise ValueError("the attachment carries no download_url; fetch it with get()")
        return self._client._send(req.download_attachment_request(url)).content

    def limits(self) -> AttachmentLimits:
        """What the caller may still upload."""
        body, rid = self._object(req.attachment_limits_request())
        return AttachmentLimits.from_dict(body, request_id=rid)


class AsyncAttachments(_Async):
    async def upload(
        self, file: bytes | IO[bytes], filename: str, *, conversation_id: str | None = None
    ) -> Attachment:
        body, rid = await self._object(
            req.upload_attachment_request(file, filename, conversation_id)
        )
        return Attachment.from_dict(body, request_id=rid)

    async def get(self, attachment_id: str) -> Attachment:
        body, rid = await self._object(req.get_attachment_request(attachment_id))
        return Attachment.from_dict(body, request_id=rid)

    async def delete(self, attachment_id: str) -> None:
        await self._none(req.delete_attachment_request(attachment_id))

    async def download(self, attachment: Attachment | str) -> bytes:
        url = attachment if isinstance(attachment, str) else attachment.download_url
        if not url:
            raise ValueError("the attachment carries no download_url; fetch it with get()")
        return (await self._client._send(req.download_attachment_request(url))).content

    async def limits(self) -> AttachmentLimits:
        body, rid = await self._object(req.attachment_limits_request())
        return AttachmentLimits.from_dict(body, request_id=rid)


# --- library -------------------------------------------------------------------


class LibraryClient(_Sync):
    """The caller's folders and tags, and search across their conversations."""

    def get(self) -> Library:
        """Folders, tags and which tags each conversation carries, in one read."""
        body, rid = self._object(req.get_library_request())
        return Library.from_dict(body, request_id=rid)

    def search(
        self, q: str, *, cursor: str | None = None, limit: int | None = None
    ) -> SearchResults:
        """`GET /api/search`: titles and messages, ranked; `limit` 1-50
        (20 when omitted), `cursor` a previous page's `next_cursor`."""
        body, rid = self._object(req.library_search_request(q, cursor, limit))
        return SearchResults.from_dict(body, request_id=rid)

    def create_folder(self, name: str) -> Folder:
        body, rid = self._object(req.create_folder_request(name))
        return Folder.from_dict(body, request_id=rid)

    def update_folder(
        self, folder_id: str, *, name: str | None = None, position: int | None = None
    ) -> Folder:
        """Rename the folder, move it in the order, or both."""
        body, rid = self._object(req.update_folder_request(folder_id, name, position))
        return Folder.from_dict(body, request_id=rid)

    def delete_folder(self, folder_id: str) -> None:
        """Its conversations go back to the main list; nothing else is deleted."""
        self._none(req.delete_folder_request(folder_id))

    def move(self, conversation_id: str, folder_id: str | None) -> Conversation:
        """File the conversation in a folder; None returns it to the main list."""
        body, rid = self._object(req.move_conversation_request(conversation_id, folder_id))
        return Conversation.from_dict(body, request_id=rid)

    def create_tag(self, name: str) -> Tag:
        """The tag of that name, made now or already there."""
        body, rid = self._object(req.create_tag_request(name))
        return Tag.from_dict(body, request_id=rid)

    def delete_tag(self, tag_id: str) -> None:
        self._none(req.delete_tag_request(tag_id))

    def set_tags(self, conversation_id: str, tag_ids: Sequence[str]) -> ConversationTags:
        """Replace the conversation's tags with exactly these."""
        body, rid = self._object(req.set_tags_request(conversation_id, tag_ids))
        return ConversationTags.from_dict(body, request_id=rid)


class AsyncLibraryClient(_Async):
    async def get(self) -> Library:
        body, rid = await self._object(req.get_library_request())
        return Library.from_dict(body, request_id=rid)

    async def search(
        self, q: str, *, cursor: str | None = None, limit: int | None = None
    ) -> SearchResults:
        body, rid = await self._object(req.library_search_request(q, cursor, limit))
        return SearchResults.from_dict(body, request_id=rid)

    async def create_folder(self, name: str) -> Folder:
        body, rid = await self._object(req.create_folder_request(name))
        return Folder.from_dict(body, request_id=rid)

    async def update_folder(
        self, folder_id: str, *, name: str | None = None, position: int | None = None
    ) -> Folder:
        body, rid = await self._object(req.update_folder_request(folder_id, name, position))
        return Folder.from_dict(body, request_id=rid)

    async def delete_folder(self, folder_id: str) -> None:
        await self._none(req.delete_folder_request(folder_id))

    async def move(self, conversation_id: str, folder_id: str | None) -> Conversation:
        body, rid = await self._object(req.move_conversation_request(conversation_id, folder_id))
        return Conversation.from_dict(body, request_id=rid)

    async def create_tag(self, name: str) -> Tag:
        body, rid = await self._object(req.create_tag_request(name))
        return Tag.from_dict(body, request_id=rid)

    async def delete_tag(self, tag_id: str) -> None:
        await self._none(req.delete_tag_request(tag_id))

    async def set_tags(self, conversation_id: str, tag_ids: Sequence[str]) -> ConversationTags:
        body, rid = await self._object(req.set_tags_request(conversation_id, tag_ids))
        return ConversationTags.from_dict(body, request_id=rid)


# --- budget --------------------------------------------------------------------


class BudgetClient(_Sync):
    """`/api/budget`: the org's allowance, what it has spent past it, and
    the caps that stop it. Every member may read the balance (their own cap
    and spend included); moving a cap is an owner or admin, and audited."""

    def get(self) -> Budget:
        """`GET /api/budget`: the period, the overage and the caps, with the
        caller's own `member_cap` and `member_spent` beside the org's."""
        body, rid = self._object(req.get_budget_request())
        return Budget.from_dict(body, request_id=rid)

    def set_overage_cap(self, overage_cap_micros: int) -> Budget:
        """`PUT /api/budget`: the most overage a period may run up, in
        micro-dollars at list price. 0 restores the plan's default (its
        monthly price). Owner or admin only."""
        body, rid = self._object(req.set_overage_cap_request(overage_cap_micros))
        return Budget.from_dict(body, request_id=rid)

    def members(self) -> MemberBudgetList:
        """`GET /api/budget/members`: every member's cap in force and spend
        this month. Owner or admin only."""
        body, rid = self._object(req.member_budgets_request())
        return MemberBudgetList.from_dict(body, request_id=rid)

    def set_member_default_cap(self, member_default_cap_micros: int | None) -> MemberBudgetList:
        """`PUT /api/budget/members`: the cap every member without one of
        their own is held to; None lifts it. Owner or admin only."""
        body, rid = self._object(req.set_member_default_cap_request(member_default_cap_micros))
        return MemberBudgetList.from_dict(body, request_id=rid)

    def set_member_cap(self, subject: str, cap_micros: int | None) -> MemberBudget:
        """`PUT /api/budget/members/{subject}`: one member's own cap, which
        beats the org's default up or down; None removes it so the default
        applies. Owner or admin only, and only for a current member."""
        body, rid = self._object(req.set_member_cap_request(subject, cap_micros))
        return MemberBudget.from_dict(body, request_id=rid)


class AsyncBudgetClient(_Async):
    async def get(self) -> Budget:
        body, rid = await self._object(req.get_budget_request())
        return Budget.from_dict(body, request_id=rid)

    async def set_overage_cap(self, overage_cap_micros: int) -> Budget:
        body, rid = await self._object(req.set_overage_cap_request(overage_cap_micros))
        return Budget.from_dict(body, request_id=rid)

    async def members(self) -> MemberBudgetList:
        body, rid = await self._object(req.member_budgets_request())
        return MemberBudgetList.from_dict(body, request_id=rid)

    async def set_member_default_cap(self, member_default_cap_micros: int | None) -> MemberBudgetList:
        body, rid = await self._object(req.set_member_default_cap_request(member_default_cap_micros))
        return MemberBudgetList.from_dict(body, request_id=rid)

    async def set_member_cap(self, subject: str, cap_micros: int | None) -> MemberBudget:
        body, rid = await self._object(req.set_member_cap_request(subject, cap_micros))
        return MemberBudget.from_dict(body, request_id=rid)


# --- shares --------------------------------------------------------------------


class Shares(_Sync):
    """Read-only links to a snapshot of a conversation. The token is in the
    answer to `create` and nowhere else."""

    def create(self, conversation_id: str, *, expires_in_s: int | None = None) -> CreatedShare:
        """Snapshot the active path and mint a link. `expires_in_s` is 60
        seconds to a year; None lives until revoked."""
        body, rid = self._object(req.create_share_request(conversation_id, expires_in_s))
        return CreatedShare.from_dict(body, request_id=rid)

    def list(self, conversation_id: str) -> ShareList:
        """The links made of one conversation."""
        body, rid = self._object(req.list_shares_request(conversation_id))
        return ShareList.from_dict(body, request_id=rid)

    def list_mine(self) -> ShareList:
        """Every link the caller made."""
        body, rid = self._object(req.list_my_shares_request())
        return ShareList.from_dict(body, request_id=rid)

    def revoke(self, share_id: str) -> None:
        self._none(req.revoke_share_request(share_id))

    def view(self, token: str) -> SharedConversation:
        """The public read anyone with the link gets; no key is sent."""
        body, rid = self._object(req.view_share_request(token))
        return SharedConversation.from_dict(body, request_id=rid)


class AsyncShares(_Async):
    async def create(
        self, conversation_id: str, *, expires_in_s: int | None = None
    ) -> CreatedShare:
        body, rid = await self._object(req.create_share_request(conversation_id, expires_in_s))
        return CreatedShare.from_dict(body, request_id=rid)

    async def list(self, conversation_id: str) -> ShareList:
        body, rid = await self._object(req.list_shares_request(conversation_id))
        return ShareList.from_dict(body, request_id=rid)

    async def list_mine(self) -> ShareList:
        body, rid = await self._object(req.list_my_shares_request())
        return ShareList.from_dict(body, request_id=rid)

    async def revoke(self, share_id: str) -> None:
        await self._none(req.revoke_share_request(share_id))

    async def view(self, token: str) -> SharedConversation:
        body, rid = await self._object(req.view_share_request(token))
        return SharedConversation.from_dict(body, request_id=rid)


# --- notifications --------------------------------------------------------------


class Notifications(_Sync):
    """The caller's in-app notifications: the bell in the web app's header.
    Reads are the caller's own rows only; another person's id is
    indistinguishable from one that does not exist -- both a 404."""

    def list(self, *, unread: bool | None = None, limit: int | None = None) -> NotificationList:
        """`GET /api/me/notifications`: the rows, newest first, with the
        unread count the bell shows. `unread=True` lists only the unread
        ones (False only the read ones); `limit` is 1 to 100 (30 when
        omitted)."""
        body, rid = self._object(req.list_notifications_request(unread, limit))
        return NotificationList.from_dict(body, request_id=rid)

    def read(self, notification_id: str) -> Notification:
        """`POST /api/me/notifications/{id}/read`: mark one read -- an
        already-read row keeps its first `read_at` -- and get it back with
        the bell's count now. `NotFoundError` for an id that is not the
        caller's own unread-or-read row."""
        body, rid = self._object(req.read_notification_request(notification_id))
        return Notification.from_dict(body, request_id=rid)

    def read_all(self) -> NotificationsReadAll:
        """`POST /api/me/notifications/read-all`: mark every unread row
        read. The answer says how many that was."""
        body, rid = self._object(req.read_all_notifications_request())
        return NotificationsReadAll.from_dict(body, request_id=rid)


class AsyncNotifications(_Async):
    async def list(
        self, *, unread: bool | None = None, limit: int | None = None
    ) -> NotificationList:
        body, rid = await self._object(req.list_notifications_request(unread, limit))
        return NotificationList.from_dict(body, request_id=rid)

    async def read(self, notification_id: str) -> Notification:
        body, rid = await self._object(req.read_notification_request(notification_id))
        return Notification.from_dict(body, request_id=rid)

    async def read_all(self) -> NotificationsReadAll:
        body, rid = await self._object(req.read_all_notifications_request())
        return NotificationsReadAll.from_dict(body, request_id=rid)


# --- announcements --------------------------------------------------------------


class Announcements(_Sync):
    """`GET /api/announcements`: the operator's banner, as everyone signed
    in sees it -- the active ones only, most severe first. Writing
    announcements is one namespace up: `client.admin.announcements`."""

    def list(self) -> AnnouncementList:
        body, rid = self._object(req.active_announcements_request())
        return AnnouncementList.from_dict(body, request_id=rid)


class AsyncAnnouncements(_Async):
    async def list(self) -> AnnouncementList:
        body, rid = await self._object(req.active_announcements_request())
        return AnnouncementList.from_dict(body, request_id=rid)


# --- admin: announcements ---------------------------------------------------------


class Admin:
    """`/api/admin/*`: the operator surface, the routes the admin console
    speaks. Everything under it needs a principal holding the platform
    `admin` role -- the admin console's session -- and an API key with
    user scopes cannot call any of it; the routes answer 403 before the
    store is ever reached."""

    def __init__(self, client: MatterWave) -> None:
        self.announcements = AdminAnnouncements(client)


class AsyncAdmin:
    def __init__(self, client: AsyncMatterWave) -> None:
        self.announcements = AsyncAdminAnnouncements(client)


class AdminAnnouncements(_Sync):
    """`/api/admin/announcements`: the operator's announcement CRUD, the
    rows the admin console's Announcements tab manages -- every column and
    every window, not just the ones active now, each write one
    `audit_events` row on the platform's side. These are operator routes:
    they mirror the admin console's announcement management and need the
    platform `admin` role, so an API key with user scopes cannot call
    them. The signed-in read side stays `client.announcements.list()`."""

    def list(self) -> AdminAnnouncementList:
        """Every announcement, newest first, each with its `state`:
        `active`, `scheduled` or `ended`."""
        body, rid = self._object(req.list_admin_announcements_request())
        return AdminAnnouncementList.from_dict(body, request_id=rid)

    def create(
        self,
        message: str,
        *,
        severity: str = "info",
        link: str | None = None,
        starts_at: datetime | str | None = None,
        ends_at: datetime | str | None = None,
        dismissible: bool = True,
    ) -> AdminAnnouncement:
        """Post an announcement. `severity` is `info`, `warning` or
        `critical` -- a critical one is never dismissible, whatever was
        asked, the store refuses the row otherwise. `starts_at` opens the
        window (the server's clock when omitted; a naive datetime is read
        as UTC) and `ends_at` closes it (None is open-ended); an edge the
        wrong way round is a 422. `link` is an absolute http(s) URL or a
        path on this site."""
        body, rid = self._object(
            req.create_admin_announcement_request(
                message,
                severity=severity,
                link=link,
                starts_at=starts_at,
                ends_at=ends_at,
                dismissible=dismissible,
            )
        )
        return AdminAnnouncement.from_dict(body, request_id=rid)

    def update(
        self,
        announcement_id: str,
        *,
        message: str | None = None,
        severity: str | None = None,
        link: str | None = UNSET,
        starts_at: datetime | str | None = None,
        ends_at: datetime | str | None = UNSET,
        dismissible: bool | None = None,
        end_now: bool = False,
    ) -> AdminAnnouncement:
        """Change what was passed; the rest stays. `link=None` clears the
        link and `ends_at=None` makes the window open-ended for good;
        `end_now=True` ends it at the server's clock instead of a caller's
        guess. `NotFoundError` for an id that is not one."""
        body, rid = self._object(
            req.update_admin_announcement_request(
                announcement_id,
                message=message,
                severity=severity,
                link=link,
                starts_at=starts_at,
                ends_at=ends_at,
                dismissible=dismissible,
                end_now=end_now,
            )
        )
        return AdminAnnouncement.from_dict(body, request_id=rid)

    def delete(self, announcement_id: str) -> None:
        """Remove it for good. `NotFoundError` when there is no such id."""
        self._none(req.delete_admin_announcement_request(announcement_id))


class AsyncAdminAnnouncements(_Async):
    async def list(self) -> AdminAnnouncementList:
        body, rid = await self._object(req.list_admin_announcements_request())
        return AdminAnnouncementList.from_dict(body, request_id=rid)

    async def create(
        self,
        message: str,
        *,
        severity: str = "info",
        link: str | None = None,
        starts_at: datetime | str | None = None,
        ends_at: datetime | str | None = None,
        dismissible: bool = True,
    ) -> AdminAnnouncement:
        body, rid = await self._object(
            req.create_admin_announcement_request(
                message,
                severity=severity,
                link=link,
                starts_at=starts_at,
                ends_at=ends_at,
                dismissible=dismissible,
            )
        )
        return AdminAnnouncement.from_dict(body, request_id=rid)

    async def update(
        self,
        announcement_id: str,
        *,
        message: str | None = None,
        severity: str | None = None,
        link: str | None = UNSET,
        starts_at: datetime | str | None = None,
        ends_at: datetime | str | None = UNSET,
        dismissible: bool | None = None,
        end_now: bool = False,
    ) -> AdminAnnouncement:
        body, rid = await self._object(
            req.update_admin_announcement_request(
                announcement_id,
                message=message,
                severity=severity,
                link=link,
                starts_at=starts_at,
                ends_at=ends_at,
                dismissible=dismissible,
                end_now=end_now,
            )
        )
        return AdminAnnouncement.from_dict(body, request_id=rid)

    async def delete(self, announcement_id: str) -> None:
        await self._none(req.delete_admin_announcement_request(announcement_id))


# --- entitlements ---------------------------------------------------------------


class EntitlementsClient(_Sync):
    """`GET /api/entitlements`: what the caller's org may do -- the plan,
    its allowance and caps, and, for an org a downgrade left over its
    conversation cap, `writes_blocked` with the reason beside it. Any
    member may read it; complements `app_models()`'s per-model `included`."""

    def get(self) -> Entitlements:
        body, rid = self._object(req.entitlements_request())
        return Entitlements.from_dict(body, request_id=rid)


class AsyncEntitlementsClient(_Async):
    async def get(self) -> Entitlements:
        body, rid = await self._object(req.entitlements_request())
        return Entitlements.from_dict(body, request_id=rid)


# --- account self-service (T-207) ---------------------------------------------------


class Account(_Sync):
    """The person's own account, beyond reading it: the writes the Account
    and Preferences panes make, and the two that do not come back from
    either. Every route here is about the caller and nobody else, so there
    is no role gate and no org scoping.

    Three of these refuse an API key outright and answer a signed-in
    session only -- the password change, both email-change steps that move
    the address, and the account deletion. A key acts for its owner, and
    the owner's password, address and existence are not the key's to
    change; the rest take a key as their owner."""

    def rename(self, name: str) -> NameUpdated:
        """`PATCH /api/me`: the display name, whitespace-collapsed, at most
        120 characters once collapsed. The provider is updated before the
        row, so what `me()` shows and what the next login says never
        disagree."""
        body, rid = self._object(req.rename_account_request(name))
        return NameUpdated.from_dict(body, request_id=rid)

    def accept_terms(self, version: str) -> TermsAccepted:
        """Record acceptance of the terms. Only the current version is
        accepted -- the one `me().terms_version` names -- and a stale
        constant is the server's 422."""
        body, rid = self._object(req.accept_terms_request(version))
        return TermsAccepted.from_dict(body, request_id=rid)

    def change_password(self, current_password: str, new_password: str) -> PasswordChanged:
        """`POST /api/me/password`. **Session only**: an API key is refused
        with a 403, because its owner's password is not the key's to
        change. The new password needs at least 12 characters (NIST 800-63B:
        length, nothing else); the current one is checked on every call, so
        this is rate-limited hard server-side. On success every *other*
        session is ended -- a changed password usually answers a device the
        person no longer trusts -- and `revoked` counts them. Neither
        password is logged, here or anywhere."""
        body, rid = self._object(req.change_password_request(current_password, new_password))
        return PasswordChanged.from_dict(body, request_id=rid)

    def email_state(self) -> EmailChangeState:
        """`GET /api/me/email`: the sign-in address and any change still
        waiting for its code, so a client can come back to the code step
        after a reload. Session only."""
        body, rid = self._object(req.email_change_state_request())
        return EmailChangeState.from_dict(body, request_id=rid)

    def start_email_change(self, new_email: str, current_password: str) -> EmailChangeStarted:
        """`POST /api/me/email`: mail a code to the new address. **Session
        only.** Nothing changes until `verify_email_change` takes the code
        back -- the old address keeps signing in. An account with no
        password signs in through its org's SSO, whose directory owns the
        address: that is a 409 (`code` `sso_managed`), not a change."""
        body, rid = self._object(req.start_email_change_request(new_email, current_password))
        return EmailChangeStarted.from_dict(body, request_id=rid)

    def verify_email_change(self, code: str) -> EmailChanged:
        """`POST /api/me/email/verify`: the code back, the address moved.
        **Session only.** Every other session is ended, the change is
        audited, and the old address is told -- a takeover that moves the
        address first is otherwise silent. The requesting session keeps
        going."""
        body, rid = self._object(req.verify_email_change_request(code))
        return EmailChanged.from_dict(body, request_id=rid)

    def cancel_email_change(self) -> EmailChangeCancelled:
        """`POST /api/me/email/cancel`: abandon a started change; the
        mailed code stops working. Harmless when nothing is pending.
        Session only."""
        body, rid = self._object(req.cancel_email_change_request())
        return EmailChangeCancelled.from_dict(body, request_id=rid)

    def resend_email_change_code(self) -> EmailChangeStarted:
        """`POST /api/me/email/resend`: a fresh code for the pending
        address. Refused with a 409 when nothing is pending. Session only."""
        body, rid = self._object(req.resend_email_change_request())
        return EmailChangeStarted.from_dict(body, request_id=rid)

    def sessions(self) -> SessionList:
        """`GET /api/me/sessions`: the live sign-ins, the one making this
        request flagged. `current_id` is None for an API caller, and then
        nothing is flagged."""
        body, rid = self._object(req.list_sessions_request())
        return SessionList.from_dict(body, request_id=rid)

    def revoke_session(self, session_id: str) -> SessionRevoked:
        """`DELETE /api/me/sessions/{id}`: sign one session out. The
        caller's own is allowed -- it is "sign out" -- and `current` says
        when that was the one."""
        body, rid = self._object(req.revoke_session_request(session_id))
        return SessionRevoked.from_dict(body, request_id=rid)

    def logout_others(self) -> SessionsLoggedOut:
        """`POST /api/me/sessions/revoke-others`: every session but this
        one; `revoked` counts them. With no session of its own (an API
        caller) there is no "this one" to keep and every row goes."""
        body, rid = self._object(req.logout_others_request())
        return SessionsLoggedOut.from_dict(body, request_id=rid)

    def preferences(self) -> Preferences:
        """`GET /api/me/preferences`: the mail toggles and locale."""
        body, rid = self._object(req.get_preferences_request())
        return Preferences.from_dict(body, request_id=rid)

    def update_preferences(
        self,
        *,
        email: Mapping[str, Any] | None = None,
        notifications: Mapping[str, Any] | None = None,
        locale: str | None = None,
    ) -> Preferences:
        """`PUT /api/me/preferences`. The route replaces the whole shape,
        so every group is always sent: a group left out is the defaults,
        not "leave it alone". `email` takes the toggles as a mapping
        (`{"product_updates": True}` -- `billing_receipts` is transactional
        mail and may not be turned off), `notifications`
        `{"budget_warn": False}`, `locale` a BCP-47 tag or None; anything
        else is the server's 422."""
        body, rid = self._object(
            req.put_preferences_request(email=email, notifications=notifications, locale=locale)
        )
        return Preferences.from_dict(body, request_id=rid)

    def onboarding_complete(self) -> OnboardingCompleted:
        """`POST /api/me/onboarding/complete`: the first-run walkthrough was
        finished or skipped -- both count. Idempotent."""
        body, rid = self._object(req.onboarding_complete_request())
        return OnboardingCompleted.from_dict(body, request_id=rid)

    def delete(self, confirm: str = req.DELETE_ACCOUNT_PHRASE) -> DeletedAccount:
        """`DELETE /api/me`: **delete the account, for good.**

        Every conversation the caller owns is soft-deleted (rows now, bytes
        within `retention_days`), their API keys are revoked, their custom
        instructions are hard-deleted, and the identity provider is asked to
        retire the login. There is no undo: this is the route the "type a
        phrase" dialog backs, and `confirm` must be exactly
        `"delete my account"` -- the same check the route makes, made here
        so a script cannot do by accident what a person is made to type.

        The last owner of an org that still has members is refused with a
        409: hand the org over first. **A signed-in session is required** --
        the route refuses an API key -- and the answer's
        `identity_removed` is False when the data is gone but the login is
        not, which the caller must say rather than smooth over."""
        body, rid = self._object(req.delete_account_request(confirm))
        return DeletedAccount.from_dict(body, request_id=rid)


class AsyncAccount(_Async):
    """As `Account`; every method a coroutine."""

    async def rename(self, name: str) -> NameUpdated:
        body, rid = await self._object(req.rename_account_request(name))
        return NameUpdated.from_dict(body, request_id=rid)

    async def accept_terms(self, version: str) -> TermsAccepted:
        body, rid = await self._object(req.accept_terms_request(version))
        return TermsAccepted.from_dict(body, request_id=rid)

    async def change_password(self, current_password: str, new_password: str) -> PasswordChanged:
        body, rid = await self._object(
            req.change_password_request(current_password, new_password)
        )
        return PasswordChanged.from_dict(body, request_id=rid)

    async def email_state(self) -> EmailChangeState:
        body, rid = await self._object(req.email_change_state_request())
        return EmailChangeState.from_dict(body, request_id=rid)

    async def start_email_change(self, new_email: str, current_password: str) -> EmailChangeStarted:
        body, rid = await self._object(req.start_email_change_request(new_email, current_password))
        return EmailChangeStarted.from_dict(body, request_id=rid)

    async def verify_email_change(self, code: str) -> EmailChanged:
        body, rid = await self._object(req.verify_email_change_request(code))
        return EmailChanged.from_dict(body, request_id=rid)

    async def cancel_email_change(self) -> EmailChangeCancelled:
        body, rid = await self._object(req.cancel_email_change_request())
        return EmailChangeCancelled.from_dict(body, request_id=rid)

    async def resend_email_change_code(self) -> EmailChangeStarted:
        body, rid = await self._object(req.resend_email_change_request())
        return EmailChangeStarted.from_dict(body, request_id=rid)

    async def sessions(self) -> SessionList:
        body, rid = await self._object(req.list_sessions_request())
        return SessionList.from_dict(body, request_id=rid)

    async def revoke_session(self, session_id: str) -> SessionRevoked:
        body, rid = await self._object(req.revoke_session_request(session_id))
        return SessionRevoked.from_dict(body, request_id=rid)

    async def logout_others(self) -> SessionsLoggedOut:
        body, rid = await self._object(req.logout_others_request())
        return SessionsLoggedOut.from_dict(body, request_id=rid)

    async def preferences(self) -> Preferences:
        body, rid = await self._object(req.get_preferences_request())
        return Preferences.from_dict(body, request_id=rid)

    async def update_preferences(
        self,
        *,
        email: Mapping[str, Any] | None = None,
        notifications: Mapping[str, Any] | None = None,
        locale: str | None = None,
    ) -> Preferences:
        body, rid = await self._object(
            req.put_preferences_request(email=email, notifications=notifications, locale=locale)
        )
        return Preferences.from_dict(body, request_id=rid)

    async def onboarding_complete(self) -> OnboardingCompleted:
        body, rid = await self._object(req.onboarding_complete_request())
        return OnboardingCompleted.from_dict(body, request_id=rid)

    async def delete(self, confirm: str = req.DELETE_ACCOUNT_PHRASE) -> DeletedAccount:
        """As `Account.delete`: the account, for good. Session only."""
        body, rid = await self._object(req.delete_account_request(confirm))
        return DeletedAccount.from_dict(body, request_id=rid)


# --- export and import jobs (T-208) ----------------------------------------------------


class Exports(_Sync):
    """The account's data leaving: the export the Settings -> Data pane
    builds. `start` queues the platform's worker and answers at once; the
    job is polled with `get` until `status` is `ready`, and `download`
    fetches the bytes -- behind the link's signature *and* the caller's own
    credentials, so a forwarded link is useless to anyone else. One pending
    export per person (409 while one is running)."""

    def start(self) -> ExportJob:
        """`POST /api/me/export` (202): the job, `pending` until the worker
        takes it. The person is told in-app and by mail when it is ready."""
        body, rid = self._object(req.start_export_request())
        return ExportJob.from_dict(body, request_id=rid)

    def get(self, export_id: str) -> ExportJob:
        """`GET /api/me/export/{id}`: where it stands, and a fresh signed
        `download_url` once it is ready. Another person's export is the
        same 404 as none."""
        body, rid = self._object(req.export_status_request(export_id))
        return ExportJob.from_dict(body, request_id=rid)

    def download(self, job: ExportJob | str) -> bytes:
        """The export's bytes, from a job's `download_url` (or the URL
        itself), fetched with the caller's own key on top of the link's
        signature. The URL lasts until `expires_at`; `get` mints a fresh
        one."""
        url = job if isinstance(job, str) else job.download_url
        if not url:
            raise ValueError("the export carries no download_url; fetch it with get()")
        return self._client._send(req.export_download_request(url)).content


class AsyncExports(_Async):
    """As `Exports`; every method a coroutine."""

    async def start(self) -> ExportJob:
        body, rid = await self._object(req.start_export_request())
        return ExportJob.from_dict(body, request_id=rid)

    async def get(self, export_id: str) -> ExportJob:
        body, rid = await self._object(req.export_status_request(export_id))
        return ExportJob.from_dict(body, request_id=rid)

    async def download(self, job: ExportJob | str) -> bytes:
        url = job if isinstance(job, str) else job.download_url
        if not url:
            raise ValueError("the export carries no download_url; fetch it with get()")
        return (await self._client._send(req.export_download_request(url))).content


class Imports(_Sync):
    """A ChatGPT or Claude history arriving: upload the export file, and
    the platform's worker reads it, counts what it found and files the
    conversations in. One import at a time per account (409 while one
    runs); the file is parsed before anything is queued, so a non-export is
    a 422 that started nothing."""

    def start(self, file: bytes | IO[bytes], filename: str) -> ImportJob:
        """`POST /api/me/imports` (202): the job with the counts as found.
        Progress is on the same row; poll with `get`."""
        body, rid = self._object(req.start_import_request(file, filename))
        return ImportJob.from_dict(body, request_id=rid)

    def list(self) -> ImportJobList:
        """`GET /api/me/imports`: the recent imports, newest first."""
        body, rid = self._object(req.list_imports_request())
        return ImportJobList.from_dict(body, request_id=rid)

    def get(self, import_id: str) -> ImportJob:
        """`GET /api/me/imports/{id}`: where one stands. Another person's
        import is the same 404 as none."""
        body, rid = self._object(req.import_status_request(import_id))
        return ImportJob.from_dict(body, request_id=rid)


class AsyncImports(_Async):
    """As `Imports`; every method a coroutine."""

    async def start(self, file: bytes | IO[bytes], filename: str) -> ImportJob:
        body, rid = await self._object(req.start_import_request(file, filename))
        return ImportJob.from_dict(body, request_id=rid)

    async def list(self) -> ImportJobList:
        body, rid = await self._object(req.list_imports_request())
        return ImportJobList.from_dict(body, request_id=rid)

    async def get(self, import_id: str) -> ImportJob:
        body, rid = await self._object(req.import_status_request(import_id))
        return ImportJob.from_dict(body, request_id=rid)


# --- members, invites, invite links, join (T-209) ------------------------------------------


class OrgInvites(_Sync):
    """The org's mailed invitations. Listing is any member's; sending,
    re-sending and revoking are an owner's or admin's (by membership row,
    never a token claim). The seat a promise of a seat: an invite past the
    plan's `max_seats` is the same 402 every entitlement gate answers."""

    def __init__(self, client: MatterWave) -> None:
        super().__init__(client)

    def list(self) -> InviteList:
        """`GET /api/org/invites`: every invitation, each with its
        `status`: `pending`, `accepted`, `expired` or `revoked`."""
        body, rid = self._object(req.list_invites_request())
        return InviteList.from_dict(body, request_id=rid)

    def create(self, email: str, *, role: str = "member") -> SentInvite:
        """`POST /api/org/invites` (201): the row, then the mail. The
        answer says whether the mail went (`mail`: `"sent"` or `"failed"`)
        -- a relay failure does not undo the row, and a resend is the
        recovery. Already a member, or an invite already pending for the
        address: a 409."""
        body, rid = self._object(req.create_invite_request(email, role))
        return SentInvite.from_dict(body, request_id=rid)

    def resend(self, invite_id: str) -> SentInvite:
        """`POST /api/org/invites/{id}/resend`: a new token, a new expiry
        and the mail again -- the recovery for a mail that never arrived.
        Rate-limited per invite server-side."""
        body, rid = self._object(req.resend_invite_request(invite_id))
        return SentInvite.from_dict(body, request_id=rid)

    def revoke(self, invite_id: str) -> None:
        """`DELETE /api/org/invites/{id}`: the link in the mail stops
        working."""
        self._none(req.revoke_invite_request(invite_id))

    def lookup(self, token: str) -> InvitePreview:
        """`POST /api/org/invites/lookup`: what a join page asks before it
        renders. Anonymous -- the token is the credential -- and it answers
        only whose org, which address, which role, and whether the link
        still works."""
        body, rid = self._object(req.lookup_invite_request(token))
        return InvitePreview.from_dict(body, request_id=rid)

    def accept(self, token: str) -> Membership:
        """`POST /api/org/invites/accept`: the signed-in path. The verified
        email must be the one invited, and the account must already be in
        that org -- one account, one org; the 409 says what to do instead.
        A stranger joins through signup instead, with the token."""
        body, rid = self._object(req.accept_invite_request(token))
        return Membership.from_dict(body, request_id=rid)


class AsyncOrgInvites(_Async):
    async def list(self) -> InviteList:
        body, rid = await self._object(req.list_invites_request())
        return InviteList.from_dict(body, request_id=rid)

    async def create(self, email: str, *, role: str = "member") -> SentInvite:
        body, rid = await self._object(req.create_invite_request(email, role))
        return SentInvite.from_dict(body, request_id=rid)

    async def resend(self, invite_id: str) -> SentInvite:
        body, rid = await self._object(req.resend_invite_request(invite_id))
        return SentInvite.from_dict(body, request_id=rid)

    async def revoke(self, invite_id: str) -> None:
        await self._none(req.revoke_invite_request(invite_id))

    async def lookup(self, token: str) -> InvitePreview:
        body, rid = await self._object(req.lookup_invite_request(token))
        return InvitePreview.from_dict(body, request_id=rid)

    async def accept(self, token: str) -> Membership:
        body, rid = await self._object(req.accept_invite_request(token))
        return Membership.from_dict(body, request_id=rid)


class OrgInviteLinks(_Sync):
    """Join links: a token anyone holding it may redeem, so making, seeing
    and revoking them are an owner's or admin's business. Only the hash is
    kept -- the token exists in the answer to `create` and wherever its
    maker pasted it -- so a lost link is revoked and replaced, never shown
    again."""

    def list(self) -> InviteLinkList:
        """`GET /api/org/invite-links`: the links not revoked, with their
        uses."""
        body, rid = self._object(req.list_invite_links_request())
        return InviteLinkList.from_dict(body, request_id=rid)

    def create(
        self, *, role: str = "member", max_uses: int = 10, expires_in_days: int = 7
    ) -> CreatedInviteLink:
        """`POST /api/org/invite-links` (201): a link with a finite life
        (a month at most) and a team's worth of uses (500 at most). Never
        an owner link -- ownership goes to a person someone chose, and
        asking for one is a ValueError here and a 422 there."""
        body, rid = self._object(
            req.create_invite_link_request(role, max_uses, expires_in_days)
        )
        return CreatedInviteLink.from_dict(body, request_id=rid)

    def revoke(self, link_id: str) -> None:
        """`DELETE /api/org/invite-links/{id}`: nobody new joins with it."""
        self._none(req.revoke_invite_link_request(link_id))


class AsyncOrgInviteLinks(_Async):
    async def list(self) -> InviteLinkList:
        body, rid = await self._object(req.list_invite_links_request())
        return InviteLinkList.from_dict(body, request_id=rid)

    async def create(
        self, *, role: str = "member", max_uses: int = 10, expires_in_days: int = 7
    ) -> CreatedInviteLink:
        body, rid = await self._object(
            req.create_invite_link_request(role, max_uses, expires_in_days)
        )
        return CreatedInviteLink.from_dict(body, request_id=rid)

    async def revoke(self, link_id: str) -> None:
        await self._none(req.revoke_invite_link_request(link_id))


class Members(_Sync):
    """The org's people. Every route here is gated on the caller's
    membership row, never a token claim: listing is any member's, changing
    roles and removing others an owner's or admin's, and only an owner
    touches an owner at all. Nobody demotes or removes the last owner, and
    a membership change re-bills a per-seat plan behind the same request."""

    def __init__(self, client: MatterWave) -> None:
        super().__init__(client)
        self.invites = OrgInvites(client)
        self.links = OrgInviteLinks(client)

    def list(self) -> MemberList:
        """`GET /api/org/members`: every member with their role, plus the
        caller's own `role` (which the token's claims say nothing about)
        and the plan's `seats`."""
        body, rid = self._object(req.list_members_request())
        return MemberList.from_dict(body, request_id=rid)

    def set_role(self, subject: str, role: str) -> Membership:
        """`PUT /api/org/members/{subject}/role`. Granting ownership is an
        owner's move; demoting the last owner is a 409."""
        body, rid = self._object(req.set_member_role_request(subject, role))
        return Membership.from_dict(body, request_id=rid)

    def transfer_ownership(self, subject: str) -> Membership:
        """`POST /api/org/members/{subject}/transfer-ownership`: hand the
        org over in one step -- they become an owner and the caller an
        admin, together or not at all. Owner only."""
        body, rid = self._object(req.transfer_ownership_request(subject))
        return Membership.from_dict(body, request_id=rid)

    def remove(self, subject: str) -> None:
        """`DELETE /api/org/members/{subject}`: access ends on their next
        request. Removing yourself is leaving, and every role may; removing
        anyone else is a manager's. The last owner cannot be removed."""
        self._none(req.remove_member_request(subject))

    def join_lookup(self, token: str) -> LinkPreview:
        """`POST /api/join/lookup`: what a join page asks before it renders
        a link -- whose org, which role, and whether the link still works.
        Anonymous."""
        body, rid = self._object(req.lookup_invite_link_request(token))
        return LinkPreview.from_dict(body, request_id=rid)

    def join(self, token: str) -> Membership:
        """`POST /api/join`: redeem a link. The account must already be in
        the link's org without a membership in it (one account, one org);
        the seat it takes is checked now, because a link reserves none."""
        body, rid = self._object(req.join_by_link_request(token))
        return Membership.from_dict(body, request_id=rid)


class AsyncMembers(_Async):
    """As `Members`; every method a coroutine."""

    def __init__(self, client: AsyncMatterWave) -> None:
        super().__init__(client)
        self.invites = AsyncOrgInvites(client)
        self.links = AsyncOrgInviteLinks(client)

    async def list(self) -> MemberList:
        body, rid = await self._object(req.list_members_request())
        return MemberList.from_dict(body, request_id=rid)

    async def set_role(self, subject: str, role: str) -> Membership:
        body, rid = await self._object(req.set_member_role_request(subject, role))
        return Membership.from_dict(body, request_id=rid)

    async def transfer_ownership(self, subject: str) -> Membership:
        body, rid = await self._object(req.transfer_ownership_request(subject))
        return Membership.from_dict(body, request_id=rid)

    async def remove(self, subject: str) -> None:
        await self._none(req.remove_member_request(subject))

    async def join_lookup(self, token: str) -> LinkPreview:
        body, rid = await self._object(req.lookup_invite_link_request(token))
        return LinkPreview.from_dict(body, request_id=rid)

    async def join(self, token: str) -> Membership:
        body, rid = await self._object(req.join_by_link_request(token))
        return Membership.from_dict(body, request_id=rid)


# --- org management (T-226) -------------------------------------------------------------------


class OrgModels(_Sync):
    """`/api/org/models`: the model allow-list, the switches the policy
    pane shows. **Owner or admin by membership row** -- members learn the
    outcome from the picker, which `/api/models` already narrows for them.
    The house model is allowed whatever is sent, so it never has to be
    listed."""

    def get(self) -> ModelPolicy:
        """`GET /api/org/models`: the plan's models with the org's switch
        on each, `unlisted` naming stale ids an admin can clear."""
        body, rid = self._object(req.get_model_policy_request())
        return ModelPolicy.from_dict(body, request_id=rid)

    def set(self, allowed: Sequence[str] | None) -> ModelPolicy:
        """`PUT /api/org/models`: the ids members may use, or None to lift
        the restriction. An id the plan does not permit is the server's 422
        naming it: a switch for a model nobody can reach would read as
        policy and enforce nothing."""
        body, rid = self._object(req.put_model_policy_request(allowed))
        return ModelPolicy.from_dict(body, request_id=rid)


class AsyncOrgModels(_Async):
    async def get(self) -> ModelPolicy:
        body, rid = await self._object(req.get_model_policy_request())
        return ModelPolicy.from_dict(body, request_id=rid)

    async def set(self, allowed: Sequence[str] | None) -> ModelPolicy:
        body, rid = await self._object(req.put_model_policy_request(allowed))
        return ModelPolicy.from_dict(body, request_id=rid)


class OrgSharing(_Sync):
    """`/api/org/sharing`: whether members may share conversations by
    link. **Owner or admin by membership row.** Switching it off stops
    every existing link answering at once -- the public read checks the
    setting on each request -- without revoking them, so switching it back
    on brings the links back."""

    def get(self) -> SharingPolicy:
        body, rid = self._object(req.get_sharing_request())
        return SharingPolicy.from_dict(body, request_id=rid)

    def set(self, enabled: bool) -> SharingPolicy:
        """`PUT /api/org/sharing`. Audited: every member is subject to it,
        and the audit log is where an owner later finds out who made the
        change."""
        body, rid = self._object(req.put_sharing_request(enabled))
        return SharingPolicy.from_dict(body, request_id=rid)


class AsyncOrgSharing(_Async):
    async def get(self) -> SharingPolicy:
        body, rid = await self._object(req.get_sharing_request())
        return SharingPolicy.from_dict(body, request_id=rid)

    async def set(self, enabled: bool) -> SharingPolicy:
        body, rid = await self._object(req.put_sharing_request(enabled))
        return SharingPolicy.from_dict(body, request_id=rid)


class OrgShares(_Sync):
    """`/api/org/shares`: every link the org's members made. **Owner or
    admin by membership row.** Never the tokens -- the store does not hold
    them; revoking is the one permanent withdrawal, and a link of another
    org and one that does not exist are the same 404."""

    def list(self) -> OrgShareList:
        body, rid = self._object(req.list_org_shares_request())
        return OrgShareList.from_dict(body, request_id=rid)

    def revoke(self, share_id: str) -> RevokedOrgShare:
        body, rid = self._object(req.revoke_org_share_request(share_id))
        return RevokedOrgShare.from_dict(body, request_id=rid)


class AsyncOrgShares(_Async):
    async def list(self) -> OrgShareList:
        body, rid = await self._object(req.list_org_shares_request())
        return OrgShareList.from_dict(body, request_id=rid)

    async def revoke(self, share_id: str) -> RevokedOrgShare:
        body, rid = await self._object(req.revoke_org_share_request(share_id))
        return RevokedOrgShare.from_dict(body, request_id=rid)


class OrgSso(_Sync):
    """`/api/org/sso`: single sign-on enforcement and its way back. **Read
    and configure are an owner's or admin's; enforcing is the owner's
    alone** -- the setting that can lock the organisation out of its own
    account. Enabling runs a preflight first and mints a break-glass
    `recovery` code in the same response, shown exactly once: there is no
    way into the enforced state without being handed the way out of it.
    Disabling is open to any admin, because undoing a lock-out should not
    need the person who caused it."""

    def get(self) -> SsoSettings:
        """`GET /api/org/sso`: the policy, plus the `blockers` that would
        stop it being turned on -- the preflight runs on the read."""
        body, rid = self._object(req.get_sso_request())
        return SsoSettings.from_dict(body, request_id=rid)

    def set(self, sso_required: bool) -> SsoPolicySet:
        """`PUT /api/org/sso`. Enforcing answers with the minted
        `recovery` code; a preflight failure (no verified domain, no
        provider) is a 409 before anything is set."""
        body, rid = self._object(req.put_sso_request(sso_required))
        return SsoPolicySet.from_dict(body, request_id=rid)

    def recovery_code(self) -> RecoveryCode:
        """`POST /api/org/sso/recovery-code` (201): a fresh break-glass
        code, superseding any unused one. Owner only, and audited."""
        body, rid = self._object(req.mint_sso_recovery_code_request())
        recovery = body.get("recovery")
        return RecoveryCode.from_dict(
            recovery if isinstance(recovery, dict) else {}, request_id=rid
        )


class AsyncOrgSso(_Async):
    async def get(self) -> SsoSettings:
        body, rid = await self._object(req.get_sso_request())
        return SsoSettings.from_dict(body, request_id=rid)

    async def set(self, sso_required: bool) -> SsoPolicySet:
        body, rid = await self._object(req.put_sso_request(sso_required))
        return SsoPolicySet.from_dict(body, request_id=rid)

    async def recovery_code(self) -> RecoveryCode:
        body, rid = await self._object(req.mint_sso_recovery_code_request())
        recovery = body.get("recovery")
        return RecoveryCode.from_dict(
            recovery if isinstance(recovery, dict) else {}, request_id=rid
        )


class OrgDomains(_Sync):
    """`/api/org/domains`: the email domains the org claims. **Owner or
    admin by membership row.** The claim decides whose address gets the SSO
    button and where a JIT-provisioned user lands, so proving it is a DNS
    check, not a form field: claim, publish the challenge's TXT record,
    verify."""

    def list(self) -> DomainClaimList:
        body, rid = self._object(req.list_domains_request())
        return DomainClaimList.from_dict(body, request_id=rid)

    def claim(self, domain: str) -> DomainClaim:
        """`POST /api/org/domains` (201): claimed, unverified. A claim
        another org holds is a 409 with no hint of whose."""
        body, rid = self._object(req.claim_domain_request(domain))
        return DomainClaim.from_dict(body, request_id=rid)

    def challenge(self, domain: str) -> DomainChallenge:
        """`GET /api/org/domains/{domain}/challenge`: the TXT record to
        publish. Stable once minted, so a mid-propagation re-open shows the
        same one."""
        body, rid = self._object(req.domain_challenge_request(domain))
        return DomainChallenge.from_dict(body, request_id=rid)

    def verify(self, domain: str) -> DomainVerification:
        """`POST /api/org/domains/{domain}/verify`: one DNS check. Three
        answers, because an admin needs to know which is which: `verified`
        True (200, proven), `verified` False with `detail` (202 -- the
        record is not there yet; publish it or wait), and the 503 the
        resolver being down raises as."""
        body, rid = self._object(req.verify_domain_request(domain))
        return DomainVerification.from_dict(body, request_id=rid)

    def release(self, domain: str) -> None:
        """`DELETE /api/org/domains/{domain}`."""
        self._none(req.release_domain_request(domain))


class AsyncOrgDomains(_Async):
    async def list(self) -> DomainClaimList:
        body, rid = await self._object(req.list_domains_request())
        return DomainClaimList.from_dict(body, request_id=rid)

    async def claim(self, domain: str) -> DomainClaim:
        body, rid = await self._object(req.claim_domain_request(domain))
        return DomainClaim.from_dict(body, request_id=rid)

    async def challenge(self, domain: str) -> DomainChallenge:
        body, rid = await self._object(req.domain_challenge_request(domain))
        return DomainChallenge.from_dict(body, request_id=rid)

    async def verify(self, domain: str) -> DomainVerification:
        body, rid = await self._object(req.verify_domain_request(domain))
        return DomainVerification.from_dict(body, request_id=rid)

    async def release(self, domain: str) -> None:
        await self._none(req.release_domain_request(domain))


class OrgIdp(_Sync):
    """`/api/org/idp`: the identity providers sign-in is offered through.
    **Owner or admin by membership row.** Never the client secret: it went
    to the IdP and is not stored here."""

    def list(self) -> IdpConfigList:
        body, rid = self._object(req.list_idps_request())
        return IdpConfigList.from_dict(body, request_id=rid)

    def attach(
        self,
        name: str,
        issuer: str,
        client_id: str,
        client_secret: str,
        *,
        scopes: Sequence[str] | None = None,
    ) -> IdpConfig:
        """`POST /api/org/idp` (201): attach an OIDC provider. The issuer
        must be https; the IdP is asked first, so a refusal there (a 422
        naming the issuer) attaches nothing."""
        body, rid = self._object(
            req.attach_idp_request(name, issuer, client_id, client_secret, scopes)
        )
        return IdpConfig.from_dict(body, request_id=rid)

    def detach(self, provider: str) -> None:
        """`DELETE /api/org/idp/{provider}`: the row first, then the IdP's,
        so nobody is sent to a provider that is about to disappear."""
        self._none(req.detach_idp_request(provider))


class AsyncOrgIdp(_Async):
    async def list(self) -> IdpConfigList:
        body, rid = await self._object(req.list_idps_request())
        return IdpConfigList.from_dict(body, request_id=rid)

    async def attach(
        self,
        name: str,
        issuer: str,
        client_id: str,
        client_secret: str,
        *,
        scopes: Sequence[str] | None = None,
    ) -> IdpConfig:
        body, rid = await self._object(
            req.attach_idp_request(name, issuer, client_id, client_secret, scopes)
        )
        return IdpConfig.from_dict(body, request_id=rid)

    async def detach(self, provider: str) -> None:
        await self._none(req.detach_idp_request(provider))


class OrgMfaPolicy(_Sync):
    """`/api/org/mfa`: whether every member must have an authenticator.
    Read by any admin; the switch is the owner's in both directions.
    Applies to password sign-in -- a member arriving through the org's own
    SSO provider has that provider's factors."""

    def get(self) -> MfaPolicy:
        body, rid = self._object(req.get_org_mfa_request())
        return MfaPolicy.from_dict(body, request_id=rid)

    def set(self, mfa_required: bool) -> MfaPolicy:
        body, rid = self._object(req.put_org_mfa_request(mfa_required))
        return MfaPolicy.from_dict(body, request_id=rid)


class AsyncOrgMfaPolicy(_Async):
    async def get(self) -> MfaPolicy:
        body, rid = await self._object(req.get_org_mfa_request())
        return MfaPolicy.from_dict(body, request_id=rid)

    async def set(self, mfa_required: bool) -> MfaPolicy:
        body, rid = await self._object(req.put_org_mfa_request(mfa_required))
        return MfaPolicy.from_dict(body, request_id=rid)


class Org(_Sync):
    """`/api/org`: the organisation itself, its sign-in policy and its
    model policy.

    **Scoping.** Reads of the org and the caller's role are any member's.
    Everything else here is **owner or admin by membership row** -- the
    `organization_members` row decides, never a token claim, so an API key
    is refused on the admin routes outright and a signed-in session is what
    answers them (the org trail under `client.audit` is the same). Closing
    the org is the owner's alone. Each mutation writes an audit row."""

    def __init__(self, client: MatterWave) -> None:
        super().__init__(client)
        self.models = OrgModels(client)
        self.sharing = OrgSharing(client)
        self.shares = OrgShares(client)
        self.sso = OrgSso(client)
        self.domains = OrgDomains(client)
        self.idp = OrgIdp(client)
        self.mfa = OrgMfaPolicy(client)

    def get(self) -> OrgInfo:
        """`GET /api/org`: the org and the caller's place in it."""
        body, rid = self._object(req.get_org_request())
        return OrgInfo.from_dict(body, request_id=rid)

    def rename(self, name: str) -> OrgInfo:
        """`PATCH /api/org`: give the org a real name. Owner or admin."""
        body, rid = self._object(req.rename_org_request(name))
        return OrgInfo.from_dict(body, request_id=rid)

    def close(self, confirm: str) -> OrgClosed:
        """`POST /api/org/close`: **close the workspace, for everyone in
        it.** Owner only, and `confirm` must be the organisation's name
        typed exactly.

        The closure lands first: from that commit every request from the org
        is a 410, whatever happens next. The side effects follow and are
        reported on the answer rather than assumed -- API keys, invites and
        invite links revoked, the Stripe subscription cancelled at once, the
        members mailed, and last (never fatal) the IdP asked to delete the
        org. Conversations are soft-deleted and erased after
        `retention_days`. Idempotent: a repeat keeps the first closure and
        re-runs the side effects, which is how a Stripe or IdP failure is
        retried."""
        body, rid = self._object(req.close_org_request(confirm))
        return OrgClosed.from_dict(body, request_id=rid)


class AsyncOrg(_Async):
    """As `Org`; every method a coroutine. The same scoping: any member
    reads; owner or admin (by membership row) for the rest; owner to close
    or enforce SSO."""

    def __init__(self, client: AsyncMatterWave) -> None:
        super().__init__(client)
        self.models = AsyncOrgModels(client)
        self.sharing = AsyncOrgSharing(client)
        self.shares = AsyncOrgShares(client)
        self.sso = AsyncOrgSso(client)
        self.domains = AsyncOrgDomains(client)
        self.idp = AsyncOrgIdp(client)
        self.mfa = AsyncOrgMfaPolicy(client)

    async def get(self) -> OrgInfo:
        body, rid = await self._object(req.get_org_request())
        return OrgInfo.from_dict(body, request_id=rid)

    async def rename(self, name: str) -> OrgInfo:
        body, rid = await self._object(req.rename_org_request(name))
        return OrgInfo.from_dict(body, request_id=rid)

    async def close(self, confirm: str) -> OrgClosed:
        body, rid = await self._object(req.close_org_request(confirm))
        return OrgClosed.from_dict(body, request_id=rid)


# --- plans and billing (T-227) -------------------------------------------------------------------


class Billing(_Sync):
    """`/api/plans`, `/api/plan` and Stripe: the money surface. The catalog
    is public; moving the org to Free is owner or admin (paid plans move by
    confirmed Stripe subscription, so a paid code here is a 402 pointing at
    checkout); starting a subscription or opening the portal is owner or
    admin, because it spends the org's money; the status read is any
    member's, so a non-admin sees the plan and is told to ask an admin
    rather than meeting a 403 that looks like a bug."""

    def plans(self) -> PlanCatalog:
        """`GET /api/plans`: the public catalog, cheapest first, with the
        price table beside it -- the pricing page's data, straight from the
        `plans` rows."""
        body, rid = self._object(req.list_plans_request())
        return PlanCatalog.from_dict(body, request_id=rid)

    def switch_plan(self, plan: str, *, reason: str | None = None) -> Entitlements:
        """`PUT /api/plan`: move the org to Free. Owner or admin. No money
        moves here, so no paid plan is granted here -- that is a confirmed
        Stripe subscription's or an operator's act; a paid code is a 402
        pointing at `checkout`. The move to Free never deletes anything:
        past the Free conversation cap it succeeds, the data stays, and
        `writes_blocked` says the org is read-only over the limit."""
        body, rid = self._object(req.switch_plan_request(plan, reason))
        return Entitlements.from_dict(body, request_id=rid)

    def plan_history(self) -> PlanHistory:
        """`GET /api/plan/history`: the append-only billing trail, newest
        first. Owner or admin: the org's commercial history, not a per-user
        fact."""
        body, rid = self._object(req.plan_history_request())
        return PlanHistory.from_dict(body, request_id=rid)

    def checkout(self, plan: str, *, annual: bool = False) -> CheckoutLink:
        """`POST /api/billing/checkout`: the Stripe Checkout URL to
        redirect the browser to. Owner or admin, and the org comes from the
        membership rather than any body, so a caller cannot start a
        subscription for someone else."""
        body, rid = self._object(req.checkout_request(plan, annual))
        return CheckoutLink.from_dict(body, request_id=rid)

    def portal(self) -> CheckoutLink:
        """`POST /api/billing/portal`: a Stripe customer portal session --
        card update, plan change, cancel. Owner or admin."""
        body, rid = self._object(req.portal_request())
        return CheckoutLink.from_dict(body, request_id=rid)

    def status(self) -> BillingStatus:
        """`GET /api/billing/status`: the plan facts any member may read,
        `billing_enabled` (whether the buy/manage buttons should exist at
        all), how the org pays, and the late-payment schedule when it is
        late."""
        body, rid = self._object(req.billing_status_request())
        return BillingStatus.from_dict(body, request_id=rid)

    def invoices(self) -> InvoiceList:
        """`GET /api/billing/invoices`: the org's invoices, newest first,
        with Stripe's hosted page and PDF links -- which is where an
        invoice is paid. Owner or admin."""
        body, rid = self._object(req.invoices_request())
        return InvoiceList.from_dict(body, request_id=rid)


class AsyncBilling(_Async):
    """As `Billing`; every method a coroutine."""

    async def plans(self) -> PlanCatalog:
        body, rid = await self._object(req.list_plans_request())
        return PlanCatalog.from_dict(body, request_id=rid)

    async def switch_plan(self, plan: str, *, reason: str | None = None) -> Entitlements:
        body, rid = await self._object(req.switch_plan_request(plan, reason))
        return Entitlements.from_dict(body, request_id=rid)

    async def plan_history(self) -> PlanHistory:
        body, rid = await self._object(req.plan_history_request())
        return PlanHistory.from_dict(body, request_id=rid)

    async def checkout(self, plan: str, *, annual: bool = False) -> CheckoutLink:
        body, rid = await self._object(req.checkout_request(plan, annual))
        return CheckoutLink.from_dict(body, request_id=rid)

    async def portal(self) -> CheckoutLink:
        body, rid = await self._object(req.portal_request())
        return CheckoutLink.from_dict(body, request_id=rid)

    async def status(self) -> BillingStatus:
        body, rid = await self._object(req.billing_status_request())
        return BillingStatus.from_dict(body, request_id=rid)

    async def invoices(self) -> InvoiceList:
        body, rid = await self._object(req.invoices_request())
        return InvoiceList.from_dict(body, request_id=rid)


# --- passkeys (T-228) ------------------------------------------------------------------------------


class Passkeys(_Sync):
    """Passkeys (WebAuthn), relayed through the identity provider: the
    names and the list are the platform's, the credential is the provider's.
    Registration and listing are the signed-in person's (a session -- the
    routes take `session_principal`, and an API key is refused); the login
    half is the browser ceremony, anonymous by nature, kept for parity."""

    def list(self) -> PasskeyList:
        """`GET /api/me/passkeys`: the account's passkeys, the
        relying-party id and the per-account maximum."""
        body, rid = self._object(req.list_passkeys_request())
        return PasskeyList.from_dict(body, request_id=rid)

    def start(self) -> PasskeyStart:
        """`POST /api/me/passkeys/start`: the creation options for
        `navigator.credentials.create`. Nothing is live until `verify`
        takes the attestation back."""
        body, rid = self._object(req.start_passkey_request())
        return PasskeyStart.from_dict(body, request_id=rid)

    def verify(self, passkey_id: str, credential: Mapping[str, Any], name: str) -> Passkey:
        """`POST /api/me/passkeys/{id}/verify` (201): the browser's
        attestation, back to the provider; on acceptance, the row that
        names it."""
        body, rid = self._object(req.verify_passkey_request(passkey_id, credential, name))
        return Passkey.from_dict(body, request_id=rid)

    def rename(self, passkey_id: str, name: str) -> Passkey:
        """`PATCH /api/me/passkeys/{id}`."""
        body, rid = self._object(req.rename_passkey_request(passkey_id, name))
        return Passkey.from_dict(body, request_id=rid)

    def remove(self, passkey_id: str) -> None:
        """`DELETE /api/me/passkeys/{id}`: the provider first, then the
        row, so the list never claims a credential that cannot sign in."""
        self._none(req.remove_passkey_request(passkey_id))

    def login_start(self, email: str, auth_request: str) -> PasskeyLoginStart:
        """`POST /api/auth/passkey/start`: the request options for
        `navigator.credentials.get` and the sealed `challenge`. An address
        with no account and one with no passkey are the same 404, so the
        answer says nothing about which addresses exist."""
        body, rid = self._object(req.start_passkey_login_request(email, auth_request))
        return PasskeyLoginStart.from_dict(body, request_id=rid)

    def login_finish(self, challenge: str, credential: Mapping[str, Any]) -> PasskeyLoginFinish:
        """`POST /api/auth/passkey/finish`: the assertion back; the answer
        is the redirect that completes the sign-in."""
        body, rid = self._object(req.finish_passkey_login_request(challenge, credential))
        return PasskeyLoginFinish.from_dict(body, request_id=rid)


class AsyncPasskeys(_Async):
    """As `Passkeys`; every method a coroutine."""

    async def list(self) -> PasskeyList:
        body, rid = await self._object(req.list_passkeys_request())
        return PasskeyList.from_dict(body, request_id=rid)

    async def start(self) -> PasskeyStart:
        body, rid = await self._object(req.start_passkey_request())
        return PasskeyStart.from_dict(body, request_id=rid)

    async def verify(self, passkey_id: str, credential: Mapping[str, Any], name: str) -> Passkey:
        body, rid = await self._object(req.verify_passkey_request(passkey_id, credential, name))
        return Passkey.from_dict(body, request_id=rid)

    async def rename(self, passkey_id: str, name: str) -> Passkey:
        body, rid = await self._object(req.rename_passkey_request(passkey_id, name))
        return Passkey.from_dict(body, request_id=rid)

    async def remove(self, passkey_id: str) -> None:
        await self._none(req.remove_passkey_request(passkey_id))

    async def login_start(self, email: str, auth_request: str) -> PasskeyLoginStart:
        body, rid = await self._object(req.start_passkey_login_request(email, auth_request))
        return PasskeyLoginStart.from_dict(body, request_id=rid)

    async def login_finish(self, challenge: str, credential: Mapping[str, Any]) -> PasskeyLoginFinish:
        body, rid = await self._object(req.finish_passkey_login_request(challenge, credential))
        return PasskeyLoginFinish.from_dict(body, request_id=rid)


# --- MFA (T-228) -------------------------------------------------------------------------------------


class Mfa(_Sync):
    """The second factor, signed-in side: enrol an authenticator, prove it,
    keep the recovery codes. The login-side code step (the sealed-challenge
    flow the password form drives) is browser ceremony and is not here.
    Every code check is rate-limited per person server-side: a six-digit
    code is guessable in a million tries, and nothing offers a million
    tries. These routes take a session; an API key is refused."""

    def state(self) -> MfaState:
        """`GET /api/me/mfa`: enrolled, codes left, and whether the org
        insists (which is why Remove is then unavailable)."""
        body, rid = self._object(req.mfa_state_request())
        return MfaState.from_dict(body, request_id=rid)

    def totp_start(self) -> TotpEnrolment:
        """`POST /api/me/mfa/totp/start`: the secret, the otpauth URI and
        its QR. Nothing is live until the first code confirms it."""
        body, rid = self._object(req.mfa_totp_start_request())
        return TotpEnrolment.from_dict(body, request_id=rid)

    def totp_confirm(self, code: str) -> TotpConfirmed:
        """`POST /api/me/mfa/totp/confirm`: the first code makes the
        authenticator live, and the ten recovery codes come back in the
        same answer -- shown once; only their hashes exist after this
        response."""
        body, rid = self._object(req.mfa_totp_confirm_request(code))
        return TotpConfirmed.from_dict(body, request_id=rid)

    def totp_remove(self, *, code: str | None = None, recovery_code: str | None = None) -> TotpRemoved:
        """`DELETE /api/me/mfa/totp`: remove the authenticator, on proof
        of it (a code) or of a recovery code -- the session cookie alone is
        not proof enough to remove the factor that protects it. Refused
        outright for a member of an org that requires one: the policy is
        the owner's to relax, not the member's to step around."""
        body, rid = self._object(req.mfa_totp_remove_request(code, recovery_code))
        return TotpRemoved.from_dict(body, request_id=rid)

    def recovery_reissue(self, code: str) -> TotpConfirmed:
        """`POST /api/me/mfa/recovery-codes`: ten new codes, the old ones
        retired, on a code from the app -- the authenticator only, because
        a lost sheet of codes must not be what authorises its own
        replacement."""
        body, rid = self._object(req.mfa_recovery_reissue_request(code))
        return TotpConfirmed.from_dict(body, request_id=rid)


class AsyncMfa(_Async):
    """As `Mfa`; every method a coroutine."""

    async def state(self) -> MfaState:
        body, rid = await self._object(req.mfa_state_request())
        return MfaState.from_dict(body, request_id=rid)

    async def totp_start(self) -> TotpEnrolment:
        body, rid = await self._object(req.mfa_totp_start_request())
        return TotpEnrolment.from_dict(body, request_id=rid)

    async def totp_confirm(self, code: str) -> TotpConfirmed:
        body, rid = await self._object(req.mfa_totp_confirm_request(code))
        return TotpConfirmed.from_dict(body, request_id=rid)

    async def totp_remove(
        self, *, code: str | None = None, recovery_code: str | None = None
    ) -> TotpRemoved:
        body, rid = await self._object(req.mfa_totp_remove_request(code, recovery_code))
        return TotpRemoved.from_dict(body, request_id=rid)

    async def recovery_reissue(self, code: str) -> TotpConfirmed:
        body, rid = await self._object(req.mfa_recovery_reissue_request(code))
        return TotpConfirmed.from_dict(body, request_id=rid)


# --- account switching (T-228) --------------------------------------------------------------------------


class BrowserAccounts(_Sync):
    """Several accounts signed in on one browser, one of them acting. A
    browser feature: the credentials are that browser's session cookies, no
    key is sent, and an API client has little use for any of it -- these
    four routes are here for parity, and answer account names only, never
    account data."""

    def list(self) -> BrowserAccountList:
        """`GET /api/accounts`: who is signed in, acting one first."""
        body, rid = self._object(req.list_browser_accounts_request())
        return BrowserAccountList.from_dict(body, request_id=rid)

    def switch(self, slot: int) -> AccountSwitched:
        """`POST /api/accounts/switch`: make a parked account the acting
        one. Both envelopes are verified before either cookie is written."""
        body, rid = self._object(req.switch_account_request(slot))
        return AccountSwitched.from_dict(body, request_id=rid)

    def logout(self, slot: int) -> LoggedOut:
        """`POST /api/accounts/{slot}/logout`: sign one *parked* account
        out; the acting account signs out with `logout_all`."""
        body, rid = self._object(req.logout_browser_account_request(slot))
        return LoggedOut.from_dict(body, request_id=rid)

    def logout_all(self) -> LoggedOut:
        """`POST /api/accounts/logout-all`: every account on this browser,
        acting and parked, each row revoked, each cookie cleared."""
        body, rid = self._object(req.logout_all_accounts_request())
        return LoggedOut.from_dict(body, request_id=rid)


class AsyncBrowserAccounts(_Async):
    """As `BrowserAccounts`; every method a coroutine."""

    async def list(self) -> BrowserAccountList:
        body, rid = await self._object(req.list_browser_accounts_request())
        return BrowserAccountList.from_dict(body, request_id=rid)

    async def switch(self, slot: int) -> AccountSwitched:
        body, rid = await self._object(req.switch_account_request(slot))
        return AccountSwitched.from_dict(body, request_id=rid)

    async def logout(self, slot: int) -> LoggedOut:
        body, rid = await self._object(req.logout_browser_account_request(slot))
        return LoggedOut.from_dict(body, request_id=rid)

    async def logout_all(self) -> LoggedOut:
        body, rid = await self._object(req.logout_all_accounts_request())
        return LoggedOut.from_dict(body, request_id=rid)


# --- speech (T-228) -----------------------------------------------------------------------------------------


class Speech(_Sync):
    """Dictation's server route: one short clip in, its text out, nothing
    kept. The audio is held in memory for the length of the request and
    nowhere else -- not logged, not stored, on the success path or any
    failure. Only where the deployment configured a transcriber; anywhere
    else the route is a 404 saying so."""

    def transcribe(
        self,
        file: bytes | IO[bytes],
        filename: str,
        *,
        content_type: str = "audio/webm",
        language: str | None = None,
    ) -> Transcription:
        """`POST /api/speech/transcribe`: up to 10 MiB of audio, 30 clips
        a minute per person, an optional BCP 47 `language` hint. The
        content type is checked here, with the route's own list."""
        body, rid = self._object(
            req.transcribe_request(file, filename, content_type, language)
        )
        return Transcription.from_dict(body, request_id=rid)


class AsyncSpeech(_Async):
    """As `Speech`; every method a coroutine."""

    async def transcribe(
        self,
        file: bytes | IO[bytes],
        filename: str,
        *,
        content_type: str = "audio/webm",
        language: str | None = None,
    ) -> Transcription:
        body, rid = await self._object(
            req.transcribe_request(file, filename, content_type, language)
        )
        return Transcription.from_dict(body, request_id=rid)


# --- edge pairing and sync (T-229) ----------------------------------------------------------------------------


class SyncPairings(_Sync):
    """Minting pairing codes and the device list. **Owner or admin by
    membership row, by session and never by key** -- pairing a box is a
    sign-in for a box, and a leaked key must not mint sign-ins."""

    def mint(self, name: str) -> PairingCode:
        """`POST /api/sync/pairings` (201): a twelve-character code the box
        redeems once for its key, ten minutes to live."""
        body, rid = self._object(req.mint_pairing_request(name))
        return PairingCode.from_dict(body, request_id=rid)

    def devices(self) -> EdgeDeviceList:
        """`GET /api/sync/devices`: the org's paired boxes -- their keys,
        never the values, revoked ones included (they are the record of an
        unpairing)."""
        body, rid = self._object(req.list_edge_devices_request())
        return EdgeDeviceList.from_dict(body, request_id=rid)

    def unpair(self, key_id: str) -> None:
        """`DELETE /api/sync/devices/{id}`: revoke a box's key. The box
        notices on its next push (401) and stops. Only an edge key:
        anything else is the API keys pane's to revoke, and a 404 here."""
        self._none(req.unpair_edge_device_request(key_id))


class AsyncSyncPairings(_Async):
    """As `SyncPairings`; every method a coroutine."""

    async def mint(self, name: str) -> PairingCode:
        body, rid = await self._object(req.mint_pairing_request(name))
        return PairingCode.from_dict(body, request_id=rid)

    async def devices(self) -> EdgeDeviceList:
        body, rid = await self._object(req.list_edge_devices_request())
        return EdgeDeviceList.from_dict(body, request_id=rid)

    async def unpair(self, key_id: str) -> None:
        await self._none(req.unpair_edge_device_request(key_id))


class Sync(_Sync):
    """Edge pairing and the mirror it buys: an admin mints a code, the box
    redeems it once for a device key holding `conversations:import` and
    nothing else, and the box pushes conversations with that key. **The box
    is its own client** -- build a `MatterWave` with the paired key and
    call `push`; this resource is the platform side, with the redeem route
    beside it for a box that pairs itself. A session is refused on the push
    route outright: it is a machine route, and the key is the containment --
    one that leaks from a box can add conversations to that one account and
    nothing else."""

    def __init__(self, client: MatterWave) -> None:
        super().__init__(client)
        self.pairings = SyncPairings(client)

    def pair(self, code: str, *, device_id: str | None = None) -> PairedKey:
        """`POST /api/sync/pair`: trade a code for the box's key, once.
        Anonymous -- the box has no session and the code is the credential.
        Unknown, expired and already used are one 404, so a guess learns
        nothing. The plaintext key is in this answer and nowhere else."""
        body, rid = self._object(req.redeem_pairing_request(code, device_id))
        return PairedKey.from_dict(body, request_id=rid)

    def status(self) -> EdgeSyncStatus:
        """`GET /api/sync/status`: what the box's sync job last did --
        counts, times and the stop reason, never content. `{"box": false,
        "paired": false}` on a process that is not an edge box at all,
        which is every platform pod."""
        body, rid = self._object(req.sync_status_request())
        return EdgeSyncStatus.from_dict(body, request_id=rid)

    def push(
        self,
        conversation: Mapping[str, Any],
        *,
        format_version: int = req.SYNC_FORMAT_VERSION,
        source: str = "edge",
        device_id: str | None = None,
    ) -> SyncOutcome:
        """`POST /api/sync/conversations`: upsert one conversation and
        insert its messages by id. The payload is the export's shape
        (`id`, `title`, `created_at`, `updated_at`, `messages`: each with
        `id`, `conversation_id`, `role`, `parts`, `created_at`). 200 with
        the counts is the answer even when some messages were rejected --
        read `rejected` per id -- while the whole request is refused when
        the conversation id is not the caller's to write (409) or was
        deleted on the platform (410). Needs the device key, not a
        session."""
        body, rid = self._object(
            req.push_conversation_request(
                conversation,
                format_version=format_version,
                source=source,
                device_id=device_id,
            )
        )
        return SyncOutcome.from_dict(body, request_id=rid)


class AsyncSync(_Async):
    """As `Sync`; every method a coroutine."""

    def __init__(self, client: AsyncMatterWave) -> None:
        super().__init__(client)
        self.pairings = AsyncSyncPairings(client)

    async def pair(self, code: str, *, device_id: str | None = None) -> PairedKey:
        body, rid = await self._object(req.redeem_pairing_request(code, device_id))
        return PairedKey.from_dict(body, request_id=rid)

    async def status(self) -> EdgeSyncStatus:
        body, rid = await self._object(req.sync_status_request())
        return EdgeSyncStatus.from_dict(body, request_id=rid)

    async def push(
        self,
        conversation: Mapping[str, Any],
        *,
        format_version: int = req.SYNC_FORMAT_VERSION,
        source: str = "edge",
        device_id: str | None = None,
    ) -> SyncOutcome:
        body, rid = await self._object(
            req.push_conversation_request(
                conversation,
                format_version=format_version,
                source=source,
                device_id=device_id,
            )
        )
        return SyncOutcome.from_dict(body, request_id=rid)
