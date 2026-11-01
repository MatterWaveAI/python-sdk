"""The org's agent presets and its connectors, sync and async: `presets`,
and `connections` with `connections.routes`, `connections.identities` and
`connections.events`. Each method is one route, built in
`_connector_requests` and parsed into `_connector_models`.

A preset is a name, a model, instructions, tools and a welcome message that
a conversation answers with (`conversations.create(preset_id=...)`) and that
a connector's channel is routed to. Any member reads the presets -- an API
key with `chat:read`, so a terminal's picker can offer them -- and an owner
or admin writes them, a key with `presets:manage` whose owner is one.

A connection joins the org to an outside workspace (Slack's): events come
in at its ingress URL, signed by the provider, and the channels it is routed
to are answered with a preset at a member's expense. Connecting a workspace
is a person's decision, so these routes take no key scope at all: they need
a person's token (`access_token=`, `client.auth.person`) from an owner or
admin, and refuse every API key 403 "Membership is managed by a person, not
an API key." Credentials go in on `create` and `update`, are checked with
the provider there, and never come back out. A deployment without
connectors answers every connection route 503, raised as `ServerError`
whose message names the setting to make (`MW_CONNECTOR_SECRET_KEY`).
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator, Mapping, Sequence
from datetime import datetime
from typing import TYPE_CHECKING, Any

from . import _connector_requests as req
from ._base import UNSET
from ._connector_models import (
    Connection,
    ConnectionEvent,
    ConnectionEventPage,
    ConnectionIdentity,
    ConnectionIdentityList,
    ConnectionList,
    ConnectionRoute,
    ConnectionRouteList,
    Preset,
    PresetListing,
    SlackManifest,
)
from ._pagination import async_items_of, async_pages_of, items_of, pages_of
from ._resources import _Async, _Sync

if TYPE_CHECKING:
    from ._client import AsyncMatterWave, MatterWave

# --- presets -------------------------------------------------------------------


class Presets(_Sync):
    """`/api/org/presets`: the org's agent presets. Any member reads them (a
    key with `chat:read`); an owner or admin writes them (a key with
    `presets:manage`)."""

    def list(self) -> PresetListing:
        """The live presets, the tools a preset may name, the limits, and
        whether the caller may change them (`can_manage`)."""
        body, rid = self._object(req.list_presets_request())
        return PresetListing.from_dict(body, request_id=rid)

    def get(self, preset_id: str) -> Preset:
        """One preset, a deleted one included (`deleted_at` says so)."""
        body, rid = self._object(req.get_preset_request(preset_id))
        return Preset.from_dict(body, request_id=rid)

    def create(
        self,
        name: str,
        model: str,
        *,
        description: str | None = None,
        instructions: str | None = None,
        tools: Sequence[str] | None = None,
        welcome_message: str | None = None,
    ) -> Preset:
        """A new preset. `tools` left out is the platform's default set and
        an empty list none. The model is checked against the plan and the
        org's allow-list (a 422 on `model`); a name the org already uses,
        ignoring case, or one preset past the limit is a `ConflictError`."""
        body, rid = self._object(
            req.create_preset_request(
                name,
                model,
                description=description,
                instructions=instructions,
                tools=tools,
                welcome_message=welcome_message,
            )
        )
        return Preset.from_dict(body, request_id=rid)

    def update(
        self,
        preset_id: str,
        *,
        name: str | None = None,
        description: str | None = None,
        model: str | None = None,
        instructions: str | None = None,
        tools: Sequence[str] | None = UNSET,
        welcome_message: str | None = UNSET,
    ) -> Preset:
        """Change what was passed. `tools=None` goes back to the default set
        and `welcome_message=None` drops the message; leaving either out
        leaves it alone. A deleted preset is a `NotFoundError`: it stays as
        its conversations answer with it."""
        body, rid = self._object(
            req.update_preset_request(
                preset_id,
                name=name,
                description=description,
                model=model,
                instructions=instructions,
                tools=tools,
                welcome_message=welcome_message,
            )
        )
        return Preset.from_dict(body, request_id=rid)

    def delete(self, preset_id: str) -> None:
        """Soft: the conversations on it keep answering with it, and nobody
        is offered it again."""
        self._none(req.delete_preset_request(preset_id))


class AsyncPresets(_Async):
    async def list(self) -> PresetListing:
        body, rid = await self._object(req.list_presets_request())
        return PresetListing.from_dict(body, request_id=rid)

    async def get(self, preset_id: str) -> Preset:
        body, rid = await self._object(req.get_preset_request(preset_id))
        return Preset.from_dict(body, request_id=rid)

    async def create(
        self,
        name: str,
        model: str,
        *,
        description: str | None = None,
        instructions: str | None = None,
        tools: Sequence[str] | None = None,
        welcome_message: str | None = None,
    ) -> Preset:
        body, rid = await self._object(
            req.create_preset_request(
                name,
                model,
                description=description,
                instructions=instructions,
                tools=tools,
                welcome_message=welcome_message,
            )
        )
        return Preset.from_dict(body, request_id=rid)

    async def update(
        self,
        preset_id: str,
        *,
        name: str | None = None,
        description: str | None = None,
        model: str | None = None,
        instructions: str | None = None,
        tools: Sequence[str] | None = UNSET,
        welcome_message: str | None = UNSET,
    ) -> Preset:
        body, rid = await self._object(
            req.update_preset_request(
                preset_id,
                name=name,
                description=description,
                model=model,
                instructions=instructions,
                tools=tools,
                welcome_message=welcome_message,
            )
        )
        return Preset.from_dict(body, request_id=rid)

    async def delete(self, preset_id: str) -> None:
        await self._none(req.delete_preset_request(preset_id))


# --- a connection's routes -----------------------------------------------------


class ConnectionRoutes(_Sync):
    """`/api/org/connections/{id}/routes`: the channels a connection
    answers, each with its preset and paying member. Owner or admin, with a
    person's token."""

    def list(self, connection_id: str) -> ConnectionRouteList:
        body, rid = self._object(req.list_routes_request(connection_id))
        return ConnectionRouteList.from_dict(body, request_id=rid)

    def create(
        self,
        connection_id: str,
        external_channel_id: str,
        preset_id: str,
        paying_subject: str,
        *,
        channel_label: str | None = None,
        enabled: bool | None = None,
    ) -> ConnectionRoute:
        """Answer `external_channel_id` with `preset_id`, charged to the
        member `paying_subject`. A preset not the org's or a payer not a
        current member is a 422; a channel that already has a route a
        `ConflictError`."""
        body, rid = self._object(
            req.create_route_request(
                connection_id,
                external_channel_id,
                preset_id,
                paying_subject,
                channel_label=channel_label,
                enabled=enabled,
            )
        )
        return ConnectionRoute.from_dict(body, request_id=rid)

    def update(
        self,
        connection_id: str,
        route_id: str,
        *,
        channel_label: str | None = None,
        preset_id: str | None = None,
        paying_subject: str | None = None,
        enabled: bool | None = None,
    ) -> ConnectionRoute:
        body, rid = self._object(
            req.update_route_request(
                connection_id,
                route_id,
                channel_label=channel_label,
                preset_id=preset_id,
                paying_subject=paying_subject,
                enabled=enabled,
            )
        )
        return ConnectionRoute.from_dict(body, request_id=rid)

    def delete(self, connection_id: str, route_id: str) -> None:
        self._none(req.delete_route_request(connection_id, route_id))


class AsyncConnectionRoutes(_Async):
    async def list(self, connection_id: str) -> ConnectionRouteList:
        body, rid = await self._object(req.list_routes_request(connection_id))
        return ConnectionRouteList.from_dict(body, request_id=rid)

    async def create(
        self,
        connection_id: str,
        external_channel_id: str,
        preset_id: str,
        paying_subject: str,
        *,
        channel_label: str | None = None,
        enabled: bool | None = None,
    ) -> ConnectionRoute:
        body, rid = await self._object(
            req.create_route_request(
                connection_id,
                external_channel_id,
                preset_id,
                paying_subject,
                channel_label=channel_label,
                enabled=enabled,
            )
        )
        return ConnectionRoute.from_dict(body, request_id=rid)

    async def update(
        self,
        connection_id: str,
        route_id: str,
        *,
        channel_label: str | None = None,
        preset_id: str | None = None,
        paying_subject: str | None = None,
        enabled: bool | None = None,
    ) -> ConnectionRoute:
        body, rid = await self._object(
            req.update_route_request(
                connection_id,
                route_id,
                channel_label=channel_label,
                preset_id=preset_id,
                paying_subject=paying_subject,
                enabled=enabled,
            )
        )
        return ConnectionRoute.from_dict(body, request_id=rid)

    async def delete(self, connection_id: str, route_id: str) -> None:
        await self._none(req.delete_route_request(connection_id, route_id))


# --- a connection's identities -------------------------------------------------


class ConnectionIdentities(_Sync):
    """`/api/org/connections/{id}/identities`: everyone a connection has
    heard from, and which member each is. An admin's `map`, `unmap`,
    `block` or `unblock` stands over the email match from then on. Owner or
    admin, with a person's token."""

    def list(self, connection_id: str, *, status: str | None = None) -> ConnectionIdentityList:
        """Filtered to one status (`mapped`, `unmapped`, `guest`, `blocked`) when given."""
        body, rid = self._object(req.list_identities_request(connection_id, status))
        return ConnectionIdentityList.from_dict(body, request_id=rid)

    def map(self, connection_id: str, identity_id: str, subject: str) -> ConnectionIdentity:
        """This identity is the member `subject`."""
        return self._update(connection_id, identity_id, "map", subject)

    def unmap(self, connection_id: str, identity_id: str) -> ConnectionIdentity:
        return self._update(connection_id, identity_id, "unmap")

    def block(self, connection_id: str, identity_id: str) -> ConnectionIdentity:
        """Refuse this identity's messages."""
        return self._update(connection_id, identity_id, "block")

    def unblock(self, connection_id: str, identity_id: str) -> ConnectionIdentity:
        return self._update(connection_id, identity_id, "unblock")

    def for_member(self, subject: str) -> ConnectionIdentityList:
        """`GET /api/org/connector-identities`: the provider accounts mapped
        to one member across every connection, each with its `connector`
        and `connection_name`."""
        body, rid = self._object(req.identities_for_member_request(subject))
        return ConnectionIdentityList.from_dict(body, request_id=rid)

    def _update(
        self, connection_id: str, identity_id: str, action: str, subject: str | None = None
    ) -> ConnectionIdentity:
        body, rid = self._object(
            req.update_identity_request(connection_id, identity_id, action, subject)
        )
        return ConnectionIdentity.from_dict(body, request_id=rid)


class AsyncConnectionIdentities(_Async):
    async def list(
        self, connection_id: str, *, status: str | None = None
    ) -> ConnectionIdentityList:
        body, rid = await self._object(req.list_identities_request(connection_id, status))
        return ConnectionIdentityList.from_dict(body, request_id=rid)

    async def map(self, connection_id: str, identity_id: str, subject: str) -> ConnectionIdentity:
        return await self._update(connection_id, identity_id, "map", subject)

    async def unmap(self, connection_id: str, identity_id: str) -> ConnectionIdentity:
        return await self._update(connection_id, identity_id, "unmap")

    async def block(self, connection_id: str, identity_id: str) -> ConnectionIdentity:
        return await self._update(connection_id, identity_id, "block")

    async def unblock(self, connection_id: str, identity_id: str) -> ConnectionIdentity:
        return await self._update(connection_id, identity_id, "unblock")

    async def for_member(self, subject: str) -> ConnectionIdentityList:
        body, rid = await self._object(req.identities_for_member_request(subject))
        return ConnectionIdentityList.from_dict(body, request_id=rid)

    async def _update(
        self, connection_id: str, identity_id: str, action: str, subject: str | None = None
    ) -> ConnectionIdentity:
        body, rid = await self._object(
            req.update_identity_request(connection_id, identity_id, action, subject)
        )
        return ConnectionIdentity.from_dict(body, request_id=rid)


# --- a connection's event log --------------------------------------------------


class ConnectionEvents(_Sync):
    """`/api/org/connections/{id}/events`: what a connection received and
    what became of each event, newest first -- ids and outcomes, never what
    anyone wrote. Owner or admin, with a person's token."""

    def list(
        self,
        connection_id: str,
        *,
        outcome: str | None = None,
        since: datetime | str | None = None,
        cursor: str | None = None,
        limit: int | None = None,
    ) -> ConnectionEventPage:
        """One page. `outcome` is one of `ConnectionList.outcomes`; `since`
        carries a timezone; `cursor` is a previous page's `next_cursor`;
        `limit` is 1 to 200, 50 when omitted."""
        body, rid = self._object(
            req.list_events_request(
                connection_id, outcome=outcome, since=since, cursor=cursor, limit=limit
            )
        )
        return ConnectionEventPage.from_dict(body, request_id=rid)

    def pages(
        self,
        connection_id: str,
        *,
        outcome: str | None = None,
        since: datetime | str | None = None,
        limit: int | None = None,
    ) -> Iterator[ConnectionEventPage]:
        """Every page of `list` under the same filters, each fetched as the
        previous one is consumed."""
        return pages_of(
            lambda cursor: self.list(
                connection_id, outcome=outcome, since=since, cursor=cursor, limit=limit
            )
        )

    def items(
        self,
        connection_id: str,
        *,
        outcome: str | None = None,
        since: datetime | str | None = None,
        limit: int | None = None,
    ) -> Iterator[ConnectionEvent]:
        """Every matching event, newest first, one at a time across the pages."""
        return items_of(self.pages(connection_id, outcome=outcome, since=since, limit=limit))


class AsyncConnectionEvents(_Async):
    async def list(
        self,
        connection_id: str,
        *,
        outcome: str | None = None,
        since: datetime | str | None = None,
        cursor: str | None = None,
        limit: int | None = None,
    ) -> ConnectionEventPage:
        body, rid = await self._object(
            req.list_events_request(
                connection_id, outcome=outcome, since=since, cursor=cursor, limit=limit
            )
        )
        return ConnectionEventPage.from_dict(body, request_id=rid)

    def pages(
        self,
        connection_id: str,
        *,
        outcome: str | None = None,
        since: datetime | str | None = None,
        limit: int | None = None,
    ) -> AsyncIterator[ConnectionEventPage]:
        return async_pages_of(
            lambda cursor: self.list(
                connection_id, outcome=outcome, since=since, cursor=cursor, limit=limit
            )
        )

    def items(
        self,
        connection_id: str,
        *,
        outcome: str | None = None,
        since: datetime | str | None = None,
        limit: int | None = None,
    ) -> AsyncIterator[ConnectionEvent]:
        return async_items_of(self.pages(connection_id, outcome=outcome, since=since, limit=limit))


# --- connections ---------------------------------------------------------------


class Connections(_Sync):
    """`/api/org/connections`: the org's connections to outside workspaces.
    Owner or admin, with a person's token; an API key is refused 403
    "Membership is managed by a person, not an API key." Credentials go in
    and never come out: no `Connection` carries them."""

    def __init__(self, client: MatterWave) -> None:
        super().__init__(client)
        self.routes = ConnectionRoutes(client)
        self.identities = ConnectionIdentities(client)
        self.events = ConnectionEvents(client)

    def list(self) -> ConnectionList:
        """The connections, the connectors this deployment offers, and the
        vocabularies of notices, outcomes and identity statuses."""
        body, rid = self._object(req.list_connections_request())
        return ConnectionList.from_dict(body, request_id=rid)

    def get(self, connection_id: str) -> Connection:
        body, rid = self._object(req.get_connection_request(connection_id))
        return Connection.from_dict(body, request_id=rid)

    def create(
        self,
        connector: str,
        display_name: str,
        *,
        credentials: Mapping[str, Any] | None = None,
        config: Mapping[str, Any] | None = None,
        rate_limit_per_minute: int | None = None,
    ) -> Connection:
        """Connect a workspace through `connector` (one of
        `ConnectionList.connectors`). The credentials are checked with the
        provider first: refused is a 422 on `credentials`, unreachable a
        502. Without them the connection is `pending` -- for Slack, whose
        app is built around the ingress URL (`slack_manifest`) -- and an
        `update` carrying them activates it."""
        body, rid = self._object(
            req.create_connection_request(
                connector,
                display_name,
                credentials=credentials,
                config=config,
                rate_limit_per_minute=rate_limit_per_minute,
            )
        )
        return Connection.from_dict(body, request_id=rid)

    def update(
        self,
        connection_id: str,
        *,
        display_name: str | None = None,
        status: str | None = None,
        rate_limit_per_minute: int | None = None,
        config: Mapping[str, Any] | None = None,
        credentials: Mapping[str, Any] | None = None,
    ) -> Connection:
        """Change what was passed. `status` switches a connection `active`
        or `disabled`; new `credentials` are checked with the provider, as
        on `create`, and activate a pending connection."""
        body, rid = self._object(
            req.update_connection_request(
                connection_id,
                display_name=display_name,
                status=status,
                rate_limit_per_minute=rate_limit_per_minute,
                config=config,
                credentials=credentials,
            )
        )
        return Connection.from_dict(body, request_id=rid)

    def delete(self, connection_id: str) -> None:
        """The connection with its routes, identities and log. The
        conversations it opened stay with their paying members."""
        self._none(req.delete_connection_request(connection_id))

    def rotate_url(self, connection_id: str) -> Connection:
        """A new ingress URL; the old one stops routing now."""
        body, rid = self._object(req.rotate_connection_url_request(connection_id))
        return Connection.from_dict(body, request_id=rid)

    def slack_manifest(self, connection_id: str) -> SlackManifest:
        """The Slack app manifest for a Slack connection, written around its
        ingress URL, and the link that creates the app from it. Any other
        connection is a `NotFoundError`."""
        body, rid = self._object(req.slack_manifest_request(connection_id))
        return SlackManifest.from_dict(body, request_id=rid)


class AsyncConnections(_Async):
    def __init__(self, client: AsyncMatterWave) -> None:
        super().__init__(client)
        self.routes = AsyncConnectionRoutes(client)
        self.identities = AsyncConnectionIdentities(client)
        self.events = AsyncConnectionEvents(client)

    async def list(self) -> ConnectionList:
        body, rid = await self._object(req.list_connections_request())
        return ConnectionList.from_dict(body, request_id=rid)

    async def get(self, connection_id: str) -> Connection:
        body, rid = await self._object(req.get_connection_request(connection_id))
        return Connection.from_dict(body, request_id=rid)

    async def create(
        self,
        connector: str,
        display_name: str,
        *,
        credentials: Mapping[str, Any] | None = None,
        config: Mapping[str, Any] | None = None,
        rate_limit_per_minute: int | None = None,
    ) -> Connection:
        body, rid = await self._object(
            req.create_connection_request(
                connector,
                display_name,
                credentials=credentials,
                config=config,
                rate_limit_per_minute=rate_limit_per_minute,
            )
        )
        return Connection.from_dict(body, request_id=rid)

    async def update(
        self,
        connection_id: str,
        *,
        display_name: str | None = None,
        status: str | None = None,
        rate_limit_per_minute: int | None = None,
        config: Mapping[str, Any] | None = None,
        credentials: Mapping[str, Any] | None = None,
    ) -> Connection:
        body, rid = await self._object(
            req.update_connection_request(
                connection_id,
                display_name=display_name,
                status=status,
                rate_limit_per_minute=rate_limit_per_minute,
                config=config,
                credentials=credentials,
            )
        )
        return Connection.from_dict(body, request_id=rid)

    async def delete(self, connection_id: str) -> None:
        await self._none(req.delete_connection_request(connection_id))

    async def rotate_url(self, connection_id: str) -> Connection:
        body, rid = await self._object(req.rotate_connection_url_request(connection_id))
        return Connection.from_dict(body, request_id=rid)

    async def slack_manifest(self, connection_id: str) -> SlackManifest:
        body, rid = await self._object(req.slack_manifest_request(connection_id))
        return SlackManifest.from_dict(body, request_id=rid)
