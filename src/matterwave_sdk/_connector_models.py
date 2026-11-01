"""The response types of the preset and connection routes: the org's agent
presets, its connections to outside workspaces (Slack's), the channels each
connection answers, the people it has heard from, and its event log.

They read their fields through `Wire`, so a field the platform adds later is
dropped rather than refused. No type here has a field for a connection's
credentials: they go in on `connections.create` and `connections.update`
and the API never sends them back, so a response that carried them would
still lose them here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ._wire import Wire

# --- presets -------------------------------------------------------------------


@dataclass(frozen=True)
class Preset(Wire):
    """One of the org's agent presets: the model, instructions, tools and
    welcome message a conversation on it answers with. `tools` None is the
    platform's default set and an empty list none. `deleted_at` is set on a
    deleted one, which `presets.get` still answers for -- a conversation on
    it keeps answering with it -- and the list never offers."""

    id: str
    name: str
    description: str
    model: str
    instructions: str
    tools: list[str] | None
    welcome_message: str | None
    created_by: str
    created_at: str
    updated_at: str
    deleted_at: str | None
    request_id: str | None = field(default=None, compare=False)


@dataclass(frozen=True)
class PresetTool(Wire):
    """A tool a preset may name, with what it does."""

    name: str
    description: str


@dataclass(frozen=True)
class PresetLimits(Wire):
    """The rules a preset is held to: how many the org may keep, and the
    longest name, description, instructions and welcome message."""

    max_presets: int
    name_max_len: int
    description_max_len: int
    instructions_max_len: int
    welcome_max_len: int


@dataclass(frozen=True)
class PresetListing(Wire):
    """`GET /api/org/presets`: the org's live presets, the tools a preset
    may name, the limits, and `can_manage`, whether the caller may change
    them (an owner or admin)."""

    data: list[Preset]
    tools: list[PresetTool]
    limits: PresetLimits
    can_manage: bool
    request_id: str | None = field(default=None, compare=False)

    def __iter__(self) -> Any:
        return iter(self.data)

    def __len__(self) -> int:
        return len(self.data)


# --- connections ---------------------------------------------------------------


@dataclass(frozen=True)
class Connection(Wire):
    """One connection to an outside workspace. `status` is `pending` (made
    without credentials, waiting for them), `active`, `disabled`, `revoked`
    (the workspace uninstalled the app) or `error` (the provider refused the
    credentials). `ingress_url` is where the provider posts its events;
    `connections.rotate_url` replaces it. Never the credentials."""

    id: str
    connector: str
    display_name: str
    status: str
    ingress_url: str
    config: dict[str, Any]
    external_workspace_id: str | None
    rate_limit_per_minute: int
    installed_by: str
    last_event_at: str | None
    last_error: str | None
    created_at: str
    updated_at: str
    request_id: str | None = field(default=None, compare=False)


@dataclass(frozen=True)
class ConnectionList(Wire):
    """`GET /api/org/connections`: the org's connections, the connectors
    this deployment can make one with, and the vocabularies a settings pane
    offers -- the events a connection may be notified of
    (`config["notify_events"]`), the outcomes the event log filters on, and
    the identity statuses."""

    data: list[Connection]
    connectors: list[str]
    notifiable_events: list[str]
    outcomes: list[str]
    identity_statuses: list[str]
    request_id: str | None = field(default=None, compare=False)

    def __iter__(self) -> Any:
        return iter(self.data)

    def __len__(self) -> int:
        return len(self.data)


@dataclass(frozen=True)
class SlackManifest(Wire):
    """`GET /api/org/connections/slack/manifest`: the Slack app manifest
    for one Slack connection, the request URL it names, Slack's
    create-from-manifest link carrying it, and whether that URL is one Slack
    can reach (https, and not a local name)."""

    connection_id: str
    status: str
    request_url: str
    publicly_reachable: bool
    manifest: dict[str, Any]
    create_url: str
    request_id: str | None = field(default=None, compare=False)


@dataclass(frozen=True)
class ConnectionRoute(Wire):
    """One channel a connection answers, with the preset it answers with and
    the member who pays. `preset_name` is None once the preset is deleted;
    the route then refuses with `preset_missing` until it names another."""

    id: str
    connection_id: str
    external_channel_id: str
    channel_label: str
    preset_id: str
    preset_name: str | None
    paying_subject: str
    enabled: bool
    created_by: str
    created_at: str
    updated_at: str
    request_id: str | None = field(default=None, compare=False)


@dataclass(frozen=True)
class ConnectionRouteList(Wire):
    """The channels one connection answers. A channel not listed is never answered."""

    data: list[ConnectionRoute]
    request_id: str | None = field(default=None, compare=False)

    def __iter__(self) -> Any:
        return iter(self.data)

    def __len__(self) -> int:
        return len(self.data)


@dataclass(frozen=True)
class ConnectionIdentity(Wire):
    """Someone a connection has heard from, and which member they are.
    `status` is `mapped`, `unmapped`, `guest` or `blocked`; `mapped_by`
    says whether the email matched (`auto`) or an admin decided
    (`manual`). `connector` and `connection_name` are set on the rows of
    `identities.for_member`, which span connections."""

    id: str
    connection_id: str
    external_user_id: str
    display_name: str | None
    email: str | None
    subject: str | None
    status: str
    mapped_by: str
    created_at: str
    updated_at: str
    connector: str | None
    connection_name: str | None
    request_id: str | None = field(default=None, compare=False)


@dataclass(frozen=True)
class ConnectionIdentityList(Wire):
    data: list[ConnectionIdentity]
    request_id: str | None = field(default=None, compare=False)

    def __iter__(self) -> Any:
        return iter(self.data)

    def __len__(self) -> int:
        return len(self.data)


@dataclass(frozen=True)
class ConnectionEvent(Wire):
    """One event a connection received and what became of it (`outcome`):
    ids, never what anyone wrote. `accepted` is in flight, `answered`
    handled to the end; every other outcome names a refusal or a failure."""

    id: str
    connection_id: str
    external_event_id: str | None
    received_at: str
    kind: str
    outcome: str
    external_user_id: str | None
    external_channel_id: str | None
    conversation_id: str | None
    paying_subject: str | None
    detail: str | None
    updated_at: str


@dataclass(frozen=True)
class ConnectionEventPage(Wire):
    """One page of a connection's event log, newest first. `next_cursor`
    fetches the page after this one and is None on the last."""

    data: list[ConnectionEvent]
    next_cursor: str | None
    request_id: str | None = field(default=None, compare=False)

    def __iter__(self) -> Any:
        return iter(self.data)

    def __len__(self) -> int:
        return len(self.data)
