"""The organisation clients, sync and async: `org` (the org, its model
allow-list, sharing, SSO and two-factor settings, and `org.domains` and
`org.idps`), `members`, `invites` and `invite_links`. Each method is one
route, built in `_org_requests` and parsed into `_org_models`.

These routes gate on the caller's membership row, not a key scope: they
need a person's token (`access_token=`, `client.auth.person`) and refuse
every API key 403 "Membership is managed by a person, not an API key."
Reading is open to any member where the web app shows it to any member
(the org, its members and invites); the rest is an owner's or admin's,
and some (closing the org, transferring ownership, requiring SSO or
two-factor) an owner's alone. `invites.lookup` and `invite_links.lookup`
are the exceptions: anonymous, the token being the credential.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

from . import _org_requests as req
from ._org_models import (
    ClosedOrg,
    Domain,
    DomainChallenge,
    DomainList,
    DomainVerification,
    IdentityProvider,
    IdentityProviderList,
    Invite,
    InviteLink,
    InviteLinkList,
    InviteList,
    InviteLookup,
    LinkLookup,
    Member,
    MemberList,
    MfaSettings,
    MintedRecoveryCode,
    ModelPolicy,
    Org,
    OrgShareList,
    RevokedOrgShare,
    SharingPolicy,
    SsoSettings,
)
from ._resources import _Async, _Sync

if TYPE_CHECKING:
    from ._client import AsyncMatterWave, MatterWave

# --- domains and identity providers --------------------------------------------


class Domains(_Sync):
    """`/api/org/domains`: the email domains the org claims, each proven by
    a DNS TXT record. Owner or admin."""

    def list(self) -> DomainList:
        body, rid = self._object(req.list_domains_request())
        return DomainList.from_dict(body, request_id=rid)

    def claim(self, domain: str) -> Domain:
        """Claim `domain` (unverified until its record is found).
        `ConflictError` when another org holds it."""
        body, rid = self._object(req.claim_domain_request(domain))
        return Domain.from_dict(body, request_id=rid)

    def challenge(self, domain: str) -> DomainChallenge:
        """The TXT record to publish, the same on every call."""
        body, rid = self._object(req.domain_challenge_request(domain))
        return DomainChallenge.from_dict(body, request_id=rid)

    def verify(self, domain: str) -> DomainVerification:
        """Look the record up now. Not finding it yet answers with
        `verified` False rather than an error; run it again once DNS has
        caught up."""
        body, rid = self._object(req.verify_domain_request(domain))
        return DomainVerification.from_dict(body, request_id=rid)

    def release(self, domain: str) -> None:
        self._none(req.release_domain_request(domain))


class AsyncDomains(_Async):
    async def list(self) -> DomainList:
        body, rid = await self._object(req.list_domains_request())
        return DomainList.from_dict(body, request_id=rid)

    async def claim(self, domain: str) -> Domain:
        body, rid = await self._object(req.claim_domain_request(domain))
        return Domain.from_dict(body, request_id=rid)

    async def challenge(self, domain: str) -> DomainChallenge:
        body, rid = await self._object(req.domain_challenge_request(domain))
        return DomainChallenge.from_dict(body, request_id=rid)

    async def verify(self, domain: str) -> DomainVerification:
        body, rid = await self._object(req.verify_domain_request(domain))
        return DomainVerification.from_dict(body, request_id=rid)

    async def release(self, domain: str) -> None:
        await self._none(req.release_domain_request(domain))


class IdentityProviders(_Sync):
    """`/api/org/idp`: the OIDC providers the org's members sign in
    through. Owner or admin."""

    def list(self) -> IdentityProviderList:
        body, rid = self._object(req.list_idps_request())
        return IdentityProviderList.from_dict(body, request_id=rid)

    def attach(
        self,
        *,
        name: str,
        issuer: str,
        client_id: str,
        client_secret: str,
        scopes: Sequence[str] | None = None,
    ) -> IdentityProvider:
        """Attach an OIDC provider by its https `issuer`. The secret goes
        once and is never read back; a provider that refuses the
        configuration is the server's 422."""
        body, rid = self._object(
            req.attach_idp_request(
                name=name,
                issuer=issuer,
                client_id=client_id,
                client_secret=client_secret,
                scopes=scopes,
            )
        )
        return IdentityProvider.from_dict(body, request_id=rid)

    def detach(self, provider: str) -> None:
        self._none(req.detach_idp_request(provider))


class AsyncIdentityProviders(_Async):
    async def list(self) -> IdentityProviderList:
        body, rid = await self._object(req.list_idps_request())
        return IdentityProviderList.from_dict(body, request_id=rid)

    async def attach(
        self,
        *,
        name: str,
        issuer: str,
        client_id: str,
        client_secret: str,
        scopes: Sequence[str] | None = None,
    ) -> IdentityProvider:
        body, rid = await self._object(
            req.attach_idp_request(
                name=name,
                issuer=issuer,
                client_id=client_id,
                client_secret=client_secret,
                scopes=scopes,
            )
        )
        return IdentityProvider.from_dict(body, request_id=rid)

    async def detach(self, provider: str) -> None:
        await self._none(req.detach_idp_request(provider))


# --- the org -------------------------------------------------------------------


class OrgClient(_Sync):
    """`/api/org`: the organisation and its settings."""

    def __init__(self, client: MatterWave) -> None:
        super().__init__(client)
        self.domains = Domains(client)
        self.idps = IdentityProviders(client)

    def get(self) -> Org:
        """The org, with the caller's own role in it. Any member."""
        body, rid = self._object(req.get_org_request())
        return Org.from_dict(body, request_id=rid)

    def rename(self, name: str) -> Org:
        """Rename the org (1 to 120 characters). Owner or admin; audited."""
        body, rid = self._object(req.rename_org_request(name))
        return Org.from_dict(body, request_id=rid)

    def close(self, *, confirm: str) -> ClosedOrg:
        """Close the org for good: `confirm` is its name, typed out. Every
        conversation is deleted (erased after the retention period), the
        keys, invites and links are revoked, the subscription is cancelled,
        the members are mailed, and every later request from the org is a
        410. Owner only; a retry repeats nothing."""
        body, rid = self._object(req.close_org_request(confirm))
        return ClosedOrg.from_dict(body, request_id=rid)

    def models(self) -> ModelPolicy:
        """The model allow-list: every model the plan offers and whether the
        org allows it. Owner or admin."""
        body, rid = self._object(req.get_model_policy_request())
        return ModelPolicy.from_dict(body, request_id=rid)

    def set_models(self, allowed: Sequence[str] | None) -> ModelPolicy:
        """Allow exactly these model ids (the house model always stays), or
        with None every model the plan offers. A model the plan does not
        offer is the server's 422."""
        body, rid = self._object(req.put_model_policy_request(allowed))
        return ModelPolicy.from_dict(body, request_id=rid)

    def sharing(self) -> SharingPolicy:
        body, rid = self._object(req.get_sharing_request())
        return SharingPolicy.from_dict(body, request_id=rid)

    def set_sharing(self, enabled: bool) -> SharingPolicy:
        """Allow or stop share links. Stopping silences every link at once
        without revoking it, so turning it back on restores them."""
        body, rid = self._object(req.put_sharing_request(enabled))
        return SharingPolicy.from_dict(body, request_id=rid)

    def shares(self) -> OrgShareList:
        """Every share link the org's members made, never a token."""
        body, rid = self._object(req.list_org_shares_request())
        return OrgShareList.from_dict(body, request_id=rid)

    def revoke_share(self, share_id: str) -> RevokedOrgShare:
        body, rid = self._object(req.revoke_org_share_request(share_id))
        return RevokedOrgShare.from_dict(body, request_id=rid)

    def sso(self) -> SsoSettings:
        """Whether SSO is required, and what blocks requiring it."""
        body, rid = self._object(req.get_sso_request())
        return SsoSettings.from_dict(body, request_id=rid)

    def set_sso(self, required: bool) -> SsoSettings:
        """Require SSO (owner only) or stop requiring it (owner or admin).
        Requiring it answers with a break-glass `recovery` code, shown this
        once; `ConflictError` while a blocker would lock the org out."""
        body, rid = self._object(req.put_sso_request(required))
        return SsoSettings.from_dict(body, request_id=rid)

    def mint_recovery_code(self) -> MintedRecoveryCode:
        """A new break-glass code, superseding any unused one. Owner only."""
        body, rid = self._object(req.mint_recovery_code_request())
        return MintedRecoveryCode.from_dict(body, request_id=rid)

    def mfa(self) -> MfaSettings:
        body, rid = self._object(req.get_mfa_request())
        return MfaSettings.from_dict(body, request_id=rid)

    def set_mfa(self, required: bool) -> MfaSettings:
        """Require a second factor of every member, or stop. Owner only."""
        body, rid = self._object(req.put_mfa_request(required))
        return MfaSettings.from_dict(body, request_id=rid)


class AsyncOrgClient(_Async):
    def __init__(self, client: AsyncMatterWave) -> None:
        super().__init__(client)
        self.domains = AsyncDomains(client)
        self.idps = AsyncIdentityProviders(client)

    async def get(self) -> Org:
        body, rid = await self._object(req.get_org_request())
        return Org.from_dict(body, request_id=rid)

    async def rename(self, name: str) -> Org:
        body, rid = await self._object(req.rename_org_request(name))
        return Org.from_dict(body, request_id=rid)

    async def close(self, *, confirm: str) -> ClosedOrg:
        body, rid = await self._object(req.close_org_request(confirm))
        return ClosedOrg.from_dict(body, request_id=rid)

    async def models(self) -> ModelPolicy:
        body, rid = await self._object(req.get_model_policy_request())
        return ModelPolicy.from_dict(body, request_id=rid)

    async def set_models(self, allowed: Sequence[str] | None) -> ModelPolicy:
        body, rid = await self._object(req.put_model_policy_request(allowed))
        return ModelPolicy.from_dict(body, request_id=rid)

    async def sharing(self) -> SharingPolicy:
        body, rid = await self._object(req.get_sharing_request())
        return SharingPolicy.from_dict(body, request_id=rid)

    async def set_sharing(self, enabled: bool) -> SharingPolicy:
        body, rid = await self._object(req.put_sharing_request(enabled))
        return SharingPolicy.from_dict(body, request_id=rid)

    async def shares(self) -> OrgShareList:
        body, rid = await self._object(req.list_org_shares_request())
        return OrgShareList.from_dict(body, request_id=rid)

    async def revoke_share(self, share_id: str) -> RevokedOrgShare:
        body, rid = await self._object(req.revoke_org_share_request(share_id))
        return RevokedOrgShare.from_dict(body, request_id=rid)

    async def sso(self) -> SsoSettings:
        body, rid = await self._object(req.get_sso_request())
        return SsoSettings.from_dict(body, request_id=rid)

    async def set_sso(self, required: bool) -> SsoSettings:
        body, rid = await self._object(req.put_sso_request(required))
        return SsoSettings.from_dict(body, request_id=rid)

    async def mint_recovery_code(self) -> MintedRecoveryCode:
        body, rid = await self._object(req.mint_recovery_code_request())
        return MintedRecoveryCode.from_dict(body, request_id=rid)

    async def mfa(self) -> MfaSettings:
        body, rid = await self._object(req.get_mfa_request())
        return MfaSettings.from_dict(body, request_id=rid)

    async def set_mfa(self, required: bool) -> MfaSettings:
        body, rid = await self._object(req.put_mfa_request(required))
        return MfaSettings.from_dict(body, request_id=rid)


# --- members, invites and links --------------------------------------------------


class Members(_Sync):
    """`/api/org/members`: who is in the org and in what role."""

    def list(self) -> MemberList:
        """Every member, the caller's own role, and the seats. Any member."""
        body, rid = self._object(req.list_members_request())
        return MemberList.from_dict(body, request_id=rid)

    def set_role(self, subject: str, role: str) -> Member:
        """`owner`, `admin`, `member` or `viewer`. Owner or admin, and only
        an owner grants or takes ownership; `ConflictError` when it would
        leave the org without an owner."""
        body, rid = self._object(req.set_member_role_request(subject, role))
        return Member.from_dict(body, request_id=rid)

    def transfer_ownership(self, subject: str) -> Member:
        """Make an active member the owner; the caller becomes an admin. Owner only."""
        body, rid = self._object(req.transfer_ownership_request(subject))
        return Member.from_dict(body, request_id=rid)

    def remove(self, subject: str) -> None:
        """Remove a member (owner or admin), or leave (anyone, as themselves).
        The seats are billed again."""
        self._none(req.remove_member_request(subject))


class AsyncMembers(_Async):
    async def list(self) -> MemberList:
        body, rid = await self._object(req.list_members_request())
        return MemberList.from_dict(body, request_id=rid)

    async def set_role(self, subject: str, role: str) -> Member:
        body, rid = await self._object(req.set_member_role_request(subject, role))
        return Member.from_dict(body, request_id=rid)

    async def transfer_ownership(self, subject: str) -> Member:
        body, rid = await self._object(req.transfer_ownership_request(subject))
        return Member.from_dict(body, request_id=rid)

    async def remove(self, subject: str) -> None:
        await self._none(req.remove_member_request(subject))


class Invites(_Sync):
    """`/api/org/invites`: invitations by email. The join link travels only
    in the mail; nothing here returns it."""

    def list(self) -> InviteList:
        """The open invites, newest first. Any member."""
        body, rid = self._object(req.list_invites_request())
        return InviteList.from_dict(body, request_id=rid)

    def create(self, email: str, *, role: str = "member") -> Invite:
        """Invite `email` and mail them the link. `mail` on the answer says
        whether the mail went. Owner or admin (an owner, to invite an
        owner); `ConflictError` for a member or a pending invite, and a
        402 when the plan's seats are full."""
        body, rid = self._object(req.create_invite_request(email, role))
        return Invite.from_dict(body, request_id=rid)

    def resend(self, invite_id: str) -> Invite:
        """Mail it again with a new link; the old link stops working.
        Three an hour per invite."""
        body, rid = self._object(req.resend_invite_request(invite_id))
        return Invite.from_dict(body, request_id=rid)

    def revoke(self, invite_id: str) -> None:
        self._none(req.revoke_invite_request(invite_id))

    def lookup(self, token: str) -> InviteLookup:
        """What the invite in a mail leads to. Anonymous: no credential is sent."""
        body, rid = self._object(req.lookup_invite_request(token))
        return InviteLookup.from_dict(body, request_id=rid)

    def accept(self, token: str) -> Member:
        """Join the org the invite is for, as the person it was sent to.
        A 410 once it has expired."""
        body, rid = self._object(req.accept_invite_request(token))
        return Member.from_dict(body, request_id=rid)


class AsyncInvites(_Async):
    async def list(self) -> InviteList:
        body, rid = await self._object(req.list_invites_request())
        return InviteList.from_dict(body, request_id=rid)

    async def create(self, email: str, *, role: str = "member") -> Invite:
        body, rid = await self._object(req.create_invite_request(email, role))
        return Invite.from_dict(body, request_id=rid)

    async def resend(self, invite_id: str) -> Invite:
        body, rid = await self._object(req.resend_invite_request(invite_id))
        return Invite.from_dict(body, request_id=rid)

    async def revoke(self, invite_id: str) -> None:
        await self._none(req.revoke_invite_request(invite_id))

    async def lookup(self, token: str) -> InviteLookup:
        body, rid = await self._object(req.lookup_invite_request(token))
        return InviteLookup.from_dict(body, request_id=rid)

    async def accept(self, token: str) -> Member:
        body, rid = await self._object(req.accept_invite_request(token))
        return Member.from_dict(body, request_id=rid)


class InviteLinks(_Sync):
    """`/api/org/invite-links` and `/api/join`: shareable links anyone with
    the link may join through, up to `max_uses` times before it expires."""

    def list(self) -> InviteLinkList:
        """The links not revoked. Owner or admin."""
        body, rid = self._object(req.list_invite_links_request())
        return InviteLinkList.from_dict(body, request_id=rid)

    def create(
        self, *, role: str = "member", max_uses: int = 10, expires_in_days: int = 7
    ) -> InviteLink:
        """A new link for `role` (never `owner`), 1 to 500 uses, 1 to 30
        days. Its `token` and `url` are on this answer only. Owner or admin."""
        body, rid = self._object(req.create_invite_link_request(role, max_uses, expires_in_days))
        return InviteLink.from_dict(body, request_id=rid)

    def revoke(self, link_id: str) -> None:
        self._none(req.revoke_invite_link_request(link_id))

    def lookup(self, token: str) -> LinkLookup:
        """What a link leads to. Anonymous: no credential is sent."""
        body, rid = self._object(req.lookup_link_request(token))
        return LinkLookup.from_dict(body, request_id=rid)

    def join(self, token: str) -> Member:
        """Join the org through a link. A 410 when it has expired or is used up."""
        body, rid = self._object(req.join_request(token))
        return Member.from_dict(body, request_id=rid)


class AsyncInviteLinks(_Async):
    async def list(self) -> InviteLinkList:
        body, rid = await self._object(req.list_invite_links_request())
        return InviteLinkList.from_dict(body, request_id=rid)

    async def create(
        self, *, role: str = "member", max_uses: int = 10, expires_in_days: int = 7
    ) -> InviteLink:
        body, rid = await self._object(
            req.create_invite_link_request(role, max_uses, expires_in_days)
        )
        return InviteLink.from_dict(body, request_id=rid)

    async def revoke(self, link_id: str) -> None:
        await self._none(req.revoke_invite_link_request(link_id))

    async def lookup(self, token: str) -> LinkLookup:
        body, rid = await self._object(req.lookup_link_request(token))
        return LinkLookup.from_dict(body, request_id=rid)

    async def join(self, token: str) -> Member:
        body, rid = await self._object(req.join_request(token))
        return Member.from_dict(body, request_id=rid)
