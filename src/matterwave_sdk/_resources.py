"""The native API clients: `keys`, `usage`, `webhooks`, `attachments`,
`library`, `shares` and `budget`, sync and async. Each method is one route;
the request is built in `_native` and the answer parsed into `models`, so the
two halves here differ only in `await`.

These are the routes the web app's settings panes use, open to an API key
as its owner. Managing keys needs a key with `keys:manage` (or a session),
webhooks a key with `webhooks:manage`, the org usage report
(`usage.org`, `usage.export_csv`) a key with `usage:read`, and moving a
budget cap an owner or admin. The scope never widens the owner's role:
webhooks still need the owner to be an org owner or admin, and a member's
`usage:read` key sees only their own row.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import IO, TYPE_CHECKING, Any

from . import _base
from . import _native as req
from ._base import UNSET, Request
from .models import (
    ApiKey,
    ApiKeyList,
    Attachment,
    AttachmentLimits,
    Budget,
    Conversation,
    ConversationTags,
    CreatedShare,
    Folder,
    Library,
    MemberBudget,
    MemberBudgetList,
    OrgUsage,
    SearchResults,
    SharedConversation,
    ShareList,
    Tag,
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
