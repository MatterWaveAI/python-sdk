"""The native API clients: `keys`, `usage`, `audit`, `webhooks`,
`attachments`, `library`, `shares`, `budget`, `notifications`,
`announcements`, `entitlements`, the `admin` namespace, and of the account
families `passkeys`, `mfa`, `accounts` and `sync` -- sync and async. (The
account, data, billing and organisation clients are in `_account` and
`_org`.) Each method is one route; the request is built in `_native` and the
answer parsed into `models`, so the two halves here differ only in `await`.

These are the routes the web app's settings panes use, most of them open to
an API key as its owner. Managing keys needs a key with `keys:manage` (or a
person's token), webhooks a key with `webhooks:manage`, and the org usage
report (`usage.org`, `usage.export_csv`) a key with `usage:read`. The audit
trail (`audit.list`, `audit.export_csv`), the member budgets and every
budget cap (`budget.members`, `set_overage_cap`, `set_member_default_cap`,
`set_member_cap`) gate on the caller's membership row rather than a key
scope: they refuse every API key with a 403 "Membership is managed by a
person, not an API key." and answer a person's token (`access_token=`,
`client.auth.person`) from an owner or admin. `CREDENTIALS` lists the
credential every operation needs. The scope never widens the owner's role:
webhooks still need the owner to be an org owner or admin, and a member's
`usage:read` key sees only their own row. Notifications are the caller's
own rows, announcements are the operator's banner anyone signed in reads,
and entitlements are the plan facts any member of the org may read. The
`admin` namespace is the exception to all of it: the operator's own
routes, the platform `admin` role, not an API key's.

The account families keep the routes' own scoping, documented on each
class: registering passkeys and managing the second factor refuse an API
key and take a person's token or a signed-in session; account switching is
the browser's own cookie jar; and the machine half of `sync` wants the
device key the box paired with, not a session.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator, Mapping, Sequence
from datetime import datetime
from typing import IO, TYPE_CHECKING, Any

from . import _account_requests as account_req
from . import _base
from . import _native as req
from ._account_models import AttachmentScan
from ._base import UNSET, Request
from ._pagination import async_items_of, async_pages_of, items_of, pages_of
from .models import (
    AccountSwitched,
    AdminAnnouncement,
    AdminAnnouncementList,
    AnnouncementList,
    ApiKey,
    ApiKeyList,
    Attachment,
    AttachmentLimits,
    AuditEvent,
    AuditPage,
    BrowserAccountList,
    Budget,
    Conversation,
    ConversationTags,
    CreatedShare,
    EdgeDeviceList,
    EdgeSyncStatus,
    Entitlements,
    Folder,
    Library,
    LoggedOut,
    MemberBudget,
    MemberBudgetList,
    MfaState,
    Notification,
    NotificationList,
    NotificationsReadAll,
    OrgUsage,
    PairedKey,
    PairingCode,
    Passkey,
    PasskeyList,
    PasskeyLoginFinish,
    PasskeyLoginStart,
    PasskeyStart,
    SearchHit,
    SearchResults,
    SharedConversation,
    ShareList,
    SyncOutcome,
    Tag,
    TotpConfirmed,
    TotpEnrolment,
    TotpRemoved,
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
    than a token claim: the platform refuses an API key 403 "Membership is
    managed by a person, not an API key.", so every method here needs a
    person's token (`access_token=`)."""

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
        cursor this API did not mint is the server's 422.

        An API key is refused 403 "Membership is managed by a person, not an
        API key."; this needs a person's token (`access_token=`)."""
        body, rid = self._object(
            req.org_audit_request(
                event=event, actor=actor, since=since, until=until, cursor=cursor, limit=limit
            )
        )
        return AuditPage.from_dict(body, request_id=rid)

    def pages(
        self,
        *,
        event: str | None = None,
        actor: str | None = None,
        since: datetime | str | None = None,
        until: datetime | str | None = None,
        limit: int | None = None,
    ) -> Iterator[AuditPage]:
        """Every page of `list` under the same filters, first to last, each
        fetched as the previous one is consumed.

        An API key is refused 403 "Membership is managed by a person, not an
        API key."; this needs a person's token (`access_token=`)."""
        return pages_of(
            lambda cursor: self.list(
                event=event, actor=actor, since=since, until=until, cursor=cursor, limit=limit
            )
        )

    def items(
        self,
        *,
        event: str | None = None,
        actor: str | None = None,
        since: datetime | str | None = None,
        until: datetime | str | None = None,
        limit: int | None = None,
    ) -> Iterator[AuditEvent]:
        """Every matching event, newest first, one at a time across the
        pages.

        An API key is refused 403 "Membership is managed by a person, not an
        API key."; this needs a person's token (`access_token=`)."""
        return items_of(self.pages(event=event, actor=actor, since=since, until=until, limit=limit))

    def export_csv(
        self,
        *,
        event: str | None = None,
        actor: str | None = None,
        since: datetime | str | None = None,
        until: datetime | str | None = None,
    ) -> str:
        """`GET /api/org/audit.csv`: every matching row, as the CSV text the
        server streamed (the columns of `AuditEvent`, `detail` as JSON).

        An API key is refused 403 "Membership is managed by a person, not an
        API key."; this needs a person's token (`access_token=`)."""
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

    def pages(
        self,
        *,
        event: str | None = None,
        actor: str | None = None,
        since: datetime | str | None = None,
        until: datetime | str | None = None,
        limit: int | None = None,
    ) -> AsyncIterator[AuditPage]:
        return async_pages_of(
            lambda cursor: self.list(
                event=event, actor=actor, since=since, until=until, cursor=cursor, limit=limit
            )
        )

    def items(
        self,
        *,
        event: str | None = None,
        actor: str | None = None,
        since: datetime | str | None = None,
        until: datetime | str | None = None,
        limit: int | None = None,
    ) -> AsyncIterator[AuditEvent]:
        return async_items_of(
            self.pages(event=event, actor=actor, since=since, until=until, limit=limit)
        )

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

    def scan(self, attachment_id: str) -> AttachmentScan:
        """`GET /api/attachments/{id}/scan`: where the malware scan is, the
        cheap read to poll after an upload until it is `clean`."""
        body, rid = self._object(account_req.attachment_scan_request(attachment_id))
        return AttachmentScan.from_dict(body, request_id=rid)

    def view(self, attachment_id: str) -> bytes:
        """The file's bytes by id: the route answers with a ten-minute
        signed link, which the client follows with its credential.
        `NotFoundError` until the file has scanned clean."""
        return self._client._send(account_req.view_attachment_request(attachment_id)).content


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

    async def scan(self, attachment_id: str) -> AttachmentScan:
        body, rid = await self._object(account_req.attachment_scan_request(attachment_id))
        return AttachmentScan.from_dict(body, request_id=rid)

    async def view(self, attachment_id: str) -> bytes:
        response = await self._client._send(account_req.view_attachment_request(attachment_id))
        return response.content


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

    def search_pages(self, q: str, *, limit: int | None = None) -> Iterator[SearchResults]:
        """Every page of `search` for `q`, best match first, each fetched as
        the previous one is consumed."""
        return pages_of(lambda cursor: self.search(q, cursor=cursor, limit=limit))

    def search_items(self, q: str, *, limit: int | None = None) -> Iterator[SearchHit]:
        """Every hit for `q`, one at a time across the pages."""
        return items_of(self.search_pages(q, limit=limit))

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

    def search_pages(self, q: str, *, limit: int | None = None) -> AsyncIterator[SearchResults]:
        return async_pages_of(lambda cursor: self.search(q, cursor=cursor, limit=limit))

    def search_items(self, q: str, *, limit: int | None = None) -> AsyncIterator[SearchHit]:
        return async_items_of(self.search_pages(q, limit=limit))

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
    and spend included), with a key or a person's token; the member list
    and moving a cap are an owner's or admin's, audited, and refuse an API
    key 403 "Membership is managed by a person, not an API key.": they need
    a person's token (`access_token=`)."""

    def get(self) -> Budget:
        """`GET /api/budget`: the period, the overage and the caps, with the
        caller's own `member_cap` and `member_spent` beside the org's."""
        body, rid = self._object(req.get_budget_request())
        return Budget.from_dict(body, request_id=rid)

    def set_overage_cap(self, overage_cap_micros: int) -> Budget:
        """`PUT /api/budget`: the most overage a period may run up, in
        micro-dollars at list price. 0 restores the plan's default (its
        monthly price). Owner or admin only.

        An API key is refused 403 "Membership is managed by a person, not an
        API key."; this needs a person's token (`access_token=`)."""
        body, rid = self._object(req.set_overage_cap_request(overage_cap_micros))
        return Budget.from_dict(body, request_id=rid)

    def members(self) -> MemberBudgetList:
        """`GET /api/budget/members`: every member's cap in force and spend
        this month. Owner or admin only.

        An API key is refused 403 "Membership is managed by a person, not an
        API key."; this needs a person's token (`access_token=`)."""
        body, rid = self._object(req.member_budgets_request())
        return MemberBudgetList.from_dict(body, request_id=rid)

    def set_member_default_cap(self, member_default_cap_micros: int | None) -> MemberBudgetList:
        """`PUT /api/budget/members`: the cap every member without one of
        their own is held to; None lifts it. Owner or admin only.

        An API key is refused 403 "Membership is managed by a person, not an
        API key."; this needs a person's token (`access_token=`)."""
        body, rid = self._object(req.set_member_default_cap_request(member_default_cap_micros))
        return MemberBudgetList.from_dict(body, request_id=rid)

    def set_member_cap(self, subject: str, cap_micros: int | None) -> MemberBudget:
        """`PUT /api/budget/members/{subject}`: one member's own cap, which
        beats the org's default up or down; None removes it so the default
        applies. Owner or admin only, and only for a current member.

        An API key is refused 403 "Membership is managed by a person, not an
        API key."; this needs a person's token (`access_token=`)."""
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

    async def set_member_default_cap(
        self, member_default_cap_micros: int | None
    ) -> MemberBudgetList:
        body, rid = await self._object(
            req.set_member_default_cap_request(member_default_cap_micros)
        )
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


# --- passkeys -----------------------------------------------------------------


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


# --- MFA ----------------------------------------------------------------------


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


# --- account switching --------------------------------------------------------


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


# --- edge pairing and sync ----------------------------------------------------


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
