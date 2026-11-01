"""The account and data clients, sync and async: `account` (the caller's
name, terms, enforcement state, password, sign-in email, onboarding and
deletion), `preferences`, `sessions`, `exports`, `imports`, `speech`,
`billing`, `plans` and `policy`. Each method is one route, built in
`_account_requests` (the password, email, onboarding and deletion routes in
`_native`) and parsed into `_account_models` or `models`.

Most routes here take an API key, acting as its owner, or a person's
token: exports need a key with `chat:read`, imports one with `chat:write`.
The password, sign-in email and deletion routes refuse an API key and take
a person's token or a signed-in session; billing beyond `status` is an
owner's or admin's and takes a person's token too. `plans.list` and
`policy.stats` are public and send no credential.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Mapping
from typing import IO, Any

from . import _account_requests as req
from . import _native as native_req
from . import _org_requests as org_req
from ._account_models import (
    AccountExport,
    BillingStatus,
    Enforcement,
    ImportJob,
    ImportJobList,
    PlanCatalog,
    PolicyStats,
    Preferences,
    RevokedSession,
    RevokedSessions,
    SessionList,
    TermsAcceptance,
    Transcription,
    UpdatedName,
)
from ._base import UNSET
from ._org_models import BillingLink, InvoiceList, PlanHistory
from ._resources import _Async, _Sync
from .errors import APITimeoutError, MatterWaveError
from .models import (
    DeletedAccount,
    EmailChangeCancelled,
    EmailChanged,
    EmailChangeStarted,
    EmailChangeState,
    Entitlements,
    OnboardingCompleted,
    PasswordChanged,
)

# How often `wait` asks after a job, unless told otherwise.
POLL_INTERVAL_S = 5.0
EXPORT_DONE = frozenset({"ready", "failed", "expired"})
IMPORT_DONE = frozenset({"done", "failed"})


def merged_preferences(
    current: Preferences,
    *,
    locale: str | None,
    product_updates: bool | None,
    usage_alerts: bool | None,
    budget_warn: bool | None,
) -> dict[str, Any]:
    """The whole object to PUT: the current settings with the changes laid
    over them. `locale` is UNSET when not passed; None clears it."""
    return {
        "locale": current.locale if locale is UNSET else locale,
        "email": {
            "product_updates": current.email.product_updates
            if product_updates is None
            else product_updates,
            "usage_alerts": current.email.usage_alerts if usage_alerts is None else usage_alerts,
            # Receipts cannot be turned off; the route refuses false.
            "billing_receipts": True,
        },
        "notifications": {
            "budget_warn": current.notifications.budget_warn if budget_warn is None else budget_warn
        },
    }


def _export_finished(export: AccountExport) -> AccountExport:
    if export.status != "ready":
        raise MatterWaveError(f"The export {export.id} is {export.status}; request a new one.")
    return export


def _import_finished(job: ImportJob) -> ImportJob:
    if job.status == "failed":
        raise MatterWaveError(f"The import {job.id} failed: {job.error or 'no reason given'}")
    return job


def _timed_out(what: str, timeout: float) -> APITimeoutError:
    return APITimeoutError(f"{what} did not finish within {timeout}s.")


# --- the account ---------------------------------------------------------------


class Account(_Sync):
    """The caller's own account: their name, the terms they accepted,
    whether moderation holds them, and the writes that do not come back --
    the password, the sign-in email, onboarding and deletion. Every route
    here is about the caller and nobody else, so there is no role gate and
    no org scoping.

    The password change, both email-change steps that move the address and
    the account deletion refuse an API key outright: a key acts for its
    owner, and the owner's password, address and existence are not the
    key's to change. They take a person's token (`access_token=`) or a
    signed-in session; the rest take a key as their owner."""

    def update(self, *, full_name: str) -> UpdatedName:
        """`PATCH /api/me`: the display name, 1 to 120 characters once its
        whitespace is collapsed; written to the identity provider first."""
        body, rid = self._object(req.update_me_request(full_name))
        return UpdatedName.from_dict(body, request_id=rid)

    def accept_terms(self, version: str) -> TermsAcceptance:
        """`POST /api/me/accept-terms`: accept the terms `version` names; a
        version other than the current one is the server's 422, which says
        the current one."""
        body, rid = self._object(req.accept_terms_request(version))
        return TermsAcceptance.from_dict(body, request_id=rid)

    def enforcement(self) -> Enforcement:
        """`GET /api/me/enforcement`: `state` `none`, or the warning, rate
        limit or suspension that holds the caller or their org."""
        body, rid = self._object(req.enforcement_request())
        return Enforcement.from_dict(body, request_id=rid)

    def rename(self, name: str) -> UpdatedName:
        """`PATCH /api/me`, as `update`, with the name's whitespace collapsed
        here as the server would and its length (120 at most) checked before
        the request is spent."""
        body, rid = self._object(native_req.rename_account_request(name))
        return UpdatedName.from_dict(body, request_id=rid)

    def sessions(self) -> SessionList:
        """`GET /api/me/sessions`, as `client.sessions.list`."""
        body, rid = self._object(req.list_sessions_request())
        return SessionList.from_dict(body, request_id=rid)

    def revoke_session(self, session_id: str) -> RevokedSession:
        """`DELETE /api/me/sessions/{id}`, as `client.sessions.revoke`: the
        caller's own is allowed -- it is "sign out" -- and `current` says
        when that was the one."""
        body, rid = self._object(req.revoke_session_request(session_id))
        return RevokedSession.from_dict(body, request_id=rid)

    def logout_others(self) -> RevokedSessions:
        """`POST /api/me/sessions/revoke-others`, as
        `client.sessions.revoke_others`."""
        body, rid = self._object(req.revoke_other_sessions_request())
        return RevokedSessions.from_dict(body, request_id=rid)

    def preferences(self) -> Preferences:
        """`GET /api/me/preferences`, as `client.preferences.get`."""
        body, rid = self._object(req.get_preferences_request())
        return Preferences.from_dict(body, request_id=rid)

    def update_preferences(
        self,
        *,
        email: Mapping[str, Any] | None = None,
        notifications: Mapping[str, Any] | None = None,
        locale: str | None = None,
    ) -> Preferences:
        """`PUT /api/me/preferences` with exactly what is passed: the route
        replaces the whole shape, so a group left out is sent empty and goes
        back to its defaults. `client.preferences.update` reads the stored
        settings first and changes only what it is given."""
        body, rid = self._object(
            native_req.replace_preferences_request(
                email=email, notifications=notifications, locale=locale
            )
        )
        return Preferences.from_dict(body, request_id=rid)

    def change_password(self, current_password: str, new_password: str) -> PasswordChanged:
        """`POST /api/me/password`. **Not with an API key**: a key is refused
        with a 403, because its owner's password is not the key's to change;
        a person's token or a session answers. The new password needs at least 12 characters (NIST 800-63B:
        length, nothing else); the current one is checked on every call, so
        this is rate-limited hard server-side. On success every *other*
        session is ended -- a changed password usually answers a device the
        person no longer trusts -- and `revoked` counts them. Neither
        password is logged, here or anywhere."""
        body, rid = self._object(native_req.change_password_request(current_password, new_password))
        return PasswordChanged.from_dict(body, request_id=rid)

    def email_state(self) -> EmailChangeState:
        """`GET /api/me/email`: the sign-in address and any change still
        waiting for its code, so a client can come back to the code step
        after a reload. A person's token or a session, never a key."""
        body, rid = self._object(native_req.email_change_state_request())
        return EmailChangeState.from_dict(body, request_id=rid)

    def start_email_change(self, new_email: str, current_password: str) -> EmailChangeStarted:
        """`POST /api/me/email`: mail a code to the new address. **A
        person's token or a session, never a key.** Nothing changes until
        `verify_email_change` takes the code back -- the old address keeps
        signing in. An account with no
        password signs in through its org's SSO, whose directory owns the
        address: that is a 409 (`code` `sso_managed`), not a change."""
        body, rid = self._object(native_req.start_email_change_request(new_email, current_password))
        return EmailChangeStarted.from_dict(body, request_id=rid)

    def verify_email_change(self, code: str) -> EmailChanged:
        """`POST /api/me/email/verify`: the code back, the address moved.
        **A person's token or a session, never a key.** Every other session
        is ended, the change is audited, and the old address is told -- a takeover that moves the
        address first is otherwise silent. The requesting session keeps
        going."""
        body, rid = self._object(native_req.verify_email_change_request(code))
        return EmailChanged.from_dict(body, request_id=rid)

    def cancel_email_change(self) -> EmailChangeCancelled:
        """`POST /api/me/email/cancel`: abandon a started change; the
        mailed code stops working. Harmless when nothing is pending.
        A person's token or a session, never a key."""
        body, rid = self._object(native_req.cancel_email_change_request())
        return EmailChangeCancelled.from_dict(body, request_id=rid)

    def resend_email_change_code(self) -> EmailChangeStarted:
        """`POST /api/me/email/resend`: a fresh code for the pending
        address. Refused with a 409 when nothing is pending. A person's
        token or a session, never a key."""
        body, rid = self._object(native_req.resend_email_change_request())
        return EmailChangeStarted.from_dict(body, request_id=rid)

    def onboarding_complete(self) -> OnboardingCompleted:
        """`POST /api/me/onboarding/complete`: the first-run walkthrough was
        finished or skipped -- both count. Idempotent."""
        body, rid = self._object(native_req.onboarding_complete_request())
        return OnboardingCompleted.from_dict(body, request_id=rid)

    def delete(self, confirm: str = native_req.DELETE_ACCOUNT_PHRASE) -> DeletedAccount:
        """`DELETE /api/me`: **delete the account, for good.**

        Every conversation the caller owns is soft-deleted (rows now, bytes
        within `retention_days`), their API keys are revoked, their custom
        instructions are hard-deleted, and the identity provider is asked to
        retire the login. There is no undo: this is the route the "type a
        phrase" dialog backs, and `confirm` must be exactly
        `"delete my account"` -- the same check the route makes, made here
        so a script cannot do by accident what a person is made to type.

        The last owner of an org that still has members is refused with a
        409: hand the org over first. **A person's token or a signed-in session
        is required** -- the route refuses an API key -- and the answer's
        `identity_removed` is False when the data is gone but the login is
        not, which the caller must say rather than smooth over."""
        body, rid = self._object(native_req.delete_account_request(confirm))
        return DeletedAccount.from_dict(body, request_id=rid)


class AsyncAccount(_Async):
    async def update(self, *, full_name: str) -> UpdatedName:
        body, rid = await self._object(req.update_me_request(full_name))
        return UpdatedName.from_dict(body, request_id=rid)

    async def accept_terms(self, version: str) -> TermsAcceptance:
        body, rid = await self._object(req.accept_terms_request(version))
        return TermsAcceptance.from_dict(body, request_id=rid)

    async def enforcement(self) -> Enforcement:
        body, rid = await self._object(req.enforcement_request())
        return Enforcement.from_dict(body, request_id=rid)

    async def rename(self, name: str) -> UpdatedName:
        body, rid = await self._object(native_req.rename_account_request(name))
        return UpdatedName.from_dict(body, request_id=rid)

    async def sessions(self) -> SessionList:
        body, rid = await self._object(req.list_sessions_request())
        return SessionList.from_dict(body, request_id=rid)

    async def revoke_session(self, session_id: str) -> RevokedSession:
        body, rid = await self._object(req.revoke_session_request(session_id))
        return RevokedSession.from_dict(body, request_id=rid)

    async def logout_others(self) -> RevokedSessions:
        body, rid = await self._object(req.revoke_other_sessions_request())
        return RevokedSessions.from_dict(body, request_id=rid)

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
            native_req.replace_preferences_request(
                email=email, notifications=notifications, locale=locale
            )
        )
        return Preferences.from_dict(body, request_id=rid)

    async def change_password(self, current_password: str, new_password: str) -> PasswordChanged:
        body, rid = await self._object(
            native_req.change_password_request(current_password, new_password)
        )
        return PasswordChanged.from_dict(body, request_id=rid)

    async def email_state(self) -> EmailChangeState:
        body, rid = await self._object(native_req.email_change_state_request())
        return EmailChangeState.from_dict(body, request_id=rid)

    async def start_email_change(self, new_email: str, current_password: str) -> EmailChangeStarted:
        body, rid = await self._object(
            native_req.start_email_change_request(new_email, current_password)
        )
        return EmailChangeStarted.from_dict(body, request_id=rid)

    async def verify_email_change(self, code: str) -> EmailChanged:
        body, rid = await self._object(native_req.verify_email_change_request(code))
        return EmailChanged.from_dict(body, request_id=rid)

    async def cancel_email_change(self) -> EmailChangeCancelled:
        body, rid = await self._object(native_req.cancel_email_change_request())
        return EmailChangeCancelled.from_dict(body, request_id=rid)

    async def resend_email_change_code(self) -> EmailChangeStarted:
        body, rid = await self._object(native_req.resend_email_change_request())
        return EmailChangeStarted.from_dict(body, request_id=rid)

    async def onboarding_complete(self) -> OnboardingCompleted:
        body, rid = await self._object(native_req.onboarding_complete_request())
        return OnboardingCompleted.from_dict(body, request_id=rid)

    async def delete(self, confirm: str = native_req.DELETE_ACCOUNT_PHRASE) -> DeletedAccount:
        """As `Account.delete`: the account, for good. Never with a key."""
        body, rid = await self._object(native_req.delete_account_request(confirm))
        return DeletedAccount.from_dict(body, request_id=rid)


class PreferencesClient(_Sync):
    """`/api/me/preferences`: the mail the caller gets, their notices and their locale."""

    def get(self) -> Preferences:
        body, rid = self._object(req.get_preferences_request())
        return Preferences.from_dict(body, request_id=rid)

    def update(
        self,
        *,
        locale: str | None = UNSET,
        product_updates: bool | None = None,
        usage_alerts: bool | None = None,
        budget_warn: bool | None = None,
    ) -> Preferences:
        """Change what was passed and keep the rest. The route replaces the
        whole object, so this reads it first and sends it back with the
        changes; `locale=None` clears the locale (a BCP-47 tag otherwise).
        Billing receipts cannot be turned off."""
        merged = merged_preferences(
            self.get(),
            locale=locale,
            product_updates=product_updates,
            usage_alerts=usage_alerts,
            budget_warn=budget_warn,
        )
        body, rid = self._object(req.put_preferences_request(merged))
        return Preferences.from_dict(body, request_id=rid)


class AsyncPreferencesClient(_Async):
    async def get(self) -> Preferences:
        body, rid = await self._object(req.get_preferences_request())
        return Preferences.from_dict(body, request_id=rid)

    async def update(
        self,
        *,
        locale: str | None = UNSET,
        product_updates: bool | None = None,
        usage_alerts: bool | None = None,
        budget_warn: bool | None = None,
    ) -> Preferences:
        merged = merged_preferences(
            await self.get(),
            locale=locale,
            product_updates=product_updates,
            usage_alerts=usage_alerts,
            budget_warn=budget_warn,
        )
        body, rid = await self._object(req.put_preferences_request(merged))
        return Preferences.from_dict(body, request_id=rid)


class Sessions(_Sync):
    """`/api/me/sessions`: the browsers signed in as the caller."""

    def list(self) -> SessionList:
        body, rid = self._object(req.list_sessions_request())
        return SessionList.from_dict(body, request_id=rid)

    def revoke(self, session_id: str) -> RevokedSession:
        """Sign one browser out. `NotFoundError` for an id that is not one of the caller's."""
        body, rid = self._object(req.revoke_session_request(session_id))
        return RevokedSession.from_dict(body, request_id=rid)

    def revoke_others(self) -> RevokedSessions:
        """Sign out every browser but the calling one; called with a key or
        a token, which no browser session is, that is every browser."""
        body, rid = self._object(req.revoke_other_sessions_request())
        return RevokedSessions.from_dict(body, request_id=rid)


class AsyncSessions(_Async):
    async def list(self) -> SessionList:
        body, rid = await self._object(req.list_sessions_request())
        return SessionList.from_dict(body, request_id=rid)

    async def revoke(self, session_id: str) -> RevokedSession:
        body, rid = await self._object(req.revoke_session_request(session_id))
        return RevokedSession.from_dict(body, request_id=rid)

    async def revoke_others(self) -> RevokedSessions:
        body, rid = await self._object(req.revoke_other_sessions_request())
        return RevokedSessions.from_dict(body, request_id=rid)


# --- exports and imports -------------------------------------------------------


class Exports(_Sync):
    """`/api/me/export`: everything the account holds, as one JSON
    document. `create` starts a job (three an hour, one at a time) and
    `wait` follows it; `download` fetches a ready one; `create_and_download`
    is the three in one. `document` builds it inside the request instead."""

    def document(self) -> dict[str, Any]:
        """`GET /api/me/export`: the whole export, built while the request
        waits. A large account is better served by `create_and_download`."""
        body, _ = self._object(req.export_document_request())
        return body

    def create(self) -> AccountExport:
        """`POST /api/me/export`: start an export; the answer is `pending`.
        `ConflictError` while another is being built."""
        body, rid = self._object(req.create_export_request())
        return AccountExport.from_dict(body, request_id=rid)

    def start(self) -> AccountExport:
        """`POST /api/me/export`, as `create`."""
        return self.create()

    def get(self, export_id: str) -> AccountExport:
        """The export's state, with a fresh `download_url` once it is ready."""
        body, rid = self._object(req.get_export_request(export_id))
        return AccountExport.from_dict(body, request_id=rid)

    def wait(
        self, export_id: str, *, timeout: float = 600, poll_interval: float = POLL_INTERVAL_S
    ) -> AccountExport:
        """Ask after the export until it is ready, and return it.
        `MatterWaveError` when it failed or expired, `APITimeoutError` when
        `timeout` seconds pass first."""
        deadline = time.monotonic() + timeout
        while True:
            export = self.get(export_id)
            if export.status in EXPORT_DONE:
                return _export_finished(export)
            if time.monotonic() + poll_interval > deadline:
                raise _timed_out(f"The export {export_id}", timeout)
            time.sleep(poll_interval)

    def download(self, export: AccountExport | str) -> bytes:
        """A ready export's JSON document, from its `download_url` (or the URL itself)."""
        url = export if isinstance(export, str) else export.download_url
        if not url:
            raise ValueError("the export carries no download_url; wait() until it is ready")
        return self._client._send(req.download_export_request(url)).content

    def create_and_download(
        self, *, timeout: float = 600, poll_interval: float = POLL_INTERVAL_S
    ) -> bytes:
        """Start an export, wait until it is ready, and return its document."""
        started = self.create()
        return self.download(self.wait(started.id, timeout=timeout, poll_interval=poll_interval))


class AsyncExports(_Async):
    async def document(self) -> dict[str, Any]:
        body, _ = await self._object(req.export_document_request())
        return body

    async def create(self) -> AccountExport:
        body, rid = await self._object(req.create_export_request())
        return AccountExport.from_dict(body, request_id=rid)

    async def start(self) -> AccountExport:
        return await self.create()

    async def get(self, export_id: str) -> AccountExport:
        body, rid = await self._object(req.get_export_request(export_id))
        return AccountExport.from_dict(body, request_id=rid)

    async def wait(
        self, export_id: str, *, timeout: float = 600, poll_interval: float = POLL_INTERVAL_S
    ) -> AccountExport:
        deadline = time.monotonic() + timeout
        while True:
            export = await self.get(export_id)
            if export.status in EXPORT_DONE:
                return _export_finished(export)
            if time.monotonic() + poll_interval > deadline:
                raise _timed_out(f"The export {export_id}", timeout)
            await asyncio.sleep(poll_interval)

    async def download(self, export: AccountExport | str) -> bytes:
        url = export if isinstance(export, str) else export.download_url
        if not url:
            raise ValueError("the export carries no download_url; wait() until it is ready")
        return (await self._client._send(req.download_export_request(url))).content

    async def create_and_download(
        self, *, timeout: float = 600, poll_interval: float = POLL_INTERVAL_S
    ) -> bytes:
        started = await self.create()
        ready = await self.wait(started.id, timeout=timeout, poll_interval=poll_interval)
        return await self.download(ready)


class Imports(_Sync):
    """`/api/me/imports`: bring conversations over from another assistant's
    data export -- ChatGPT's `conversations.json` (or its whole zip) or
    Claude's JSON file, up to 100 MiB. A job runs one at a time, ten an
    hour, and importing the same file again creates nothing new."""

    def create(self, file: bytes | IO[bytes], filename: str) -> ImportJob:
        """Upload the export and start the job; the answer is `pending`."""
        body, rid = self._object(req.create_import_request(file, filename))
        return ImportJob.from_dict(body, request_id=rid)

    def start(self, file: bytes | IO[bytes], filename: str) -> ImportJob:
        """`POST /api/me/imports`, as `create`."""
        return self.create(file, filename)

    def list(self) -> ImportJobList:
        body, rid = self._object(req.list_imports_request())
        return ImportJobList.from_dict(body, request_id=rid)

    def get(self, import_id: str) -> ImportJob:
        body, rid = self._object(req.get_import_request(import_id))
        return ImportJob.from_dict(body, request_id=rid)

    def wait(
        self, import_id: str, *, timeout: float = 1800, poll_interval: float = POLL_INTERVAL_S
    ) -> ImportJob:
        """Ask after the job until it is `done`, and return it.
        `MatterWaveError` with the job's `error` when it failed,
        `APITimeoutError` when `timeout` seconds pass first."""
        deadline = time.monotonic() + timeout
        while True:
            job = self.get(import_id)
            if job.status in IMPORT_DONE:
                return _import_finished(job)
            if time.monotonic() + poll_interval > deadline:
                raise _timed_out(f"The import {import_id}", timeout)
            time.sleep(poll_interval)


class AsyncImports(_Async):
    async def create(self, file: bytes | IO[bytes], filename: str) -> ImportJob:
        body, rid = await self._object(req.create_import_request(file, filename))
        return ImportJob.from_dict(body, request_id=rid)

    async def start(self, file: bytes | IO[bytes], filename: str) -> ImportJob:
        return await self.create(file, filename)

    async def list(self) -> ImportJobList:
        body, rid = await self._object(req.list_imports_request())
        return ImportJobList.from_dict(body, request_id=rid)

    async def get(self, import_id: str) -> ImportJob:
        body, rid = await self._object(req.get_import_request(import_id))
        return ImportJob.from_dict(body, request_id=rid)

    async def wait(
        self, import_id: str, *, timeout: float = 1800, poll_interval: float = POLL_INTERVAL_S
    ) -> ImportJob:
        deadline = time.monotonic() + timeout
        while True:
            job = await self.get(import_id)
            if job.status in IMPORT_DONE:
                return _import_finished(job)
            if time.monotonic() + poll_interval > deadline:
                raise _timed_out(f"The import {import_id}", timeout)
            await asyncio.sleep(poll_interval)


# --- speech --------------------------------------------------------------------


class Speech(_Sync):
    """`POST /api/speech/transcribe`: dictation. `NotFoundError` on a
    deployment with no transcriber configured."""

    def transcribe(
        self,
        file: bytes | IO[bytes],
        filename: str,
        *,
        language: str | None = None,
        content_type: str | None = None,
    ) -> Transcription:
        """The words in an audio clip of up to 10 MiB (webm, ogg, mp4/m4a,
        mp3, wav, flac or aac; `content_type` names it when the file name
        does not). `language` is a BCP-47 hint such as `en`."""
        body, rid = self._object(
            req.transcribe_request(file, filename, language=language, content_type=content_type)
        )
        return Transcription.from_dict(body, request_id=rid)


class AsyncSpeech(_Async):
    async def transcribe(
        self,
        file: bytes | IO[bytes],
        filename: str,
        *,
        language: str | None = None,
        content_type: str | None = None,
    ) -> Transcription:
        body, rid = await self._object(
            req.transcribe_request(file, filename, language=language, content_type=content_type)
        )
        return Transcription.from_dict(body, request_id=rid)


# --- billing, plans and policy -------------------------------------------------


class Billing(_Sync):
    """The org's billing. `status` is any member's read, with a key or a
    person's token; the rest is an owner's or admin's and needs a person's
    token -- an API key is refused 403 "Membership is managed by a person,
    not an API key."."""

    def status(self) -> BillingStatus:
        """`GET /api/billing/status`: the plan and its limits, whether
        billing is on, how the org pays, and the dunning schedule when a
        payment is late."""
        body, rid = self._object(req.billing_status_request())
        return BillingStatus.from_dict(body, request_id=rid)

    def plans(self) -> PlanCatalog:
        """`GET /api/plans`, as `client.plans.list`: the public catalog,
        cheapest first, with the price table; no credential is sent."""
        body, rid = self._object(req.list_plans_request())
        return PlanCatalog.from_dict(body, request_id=rid)

    def switch_plan(self, plan: str, *, reason: str | None = None) -> Entitlements:
        """`PUT /api/plan`, as `change_plan`."""
        return self.change_plan(plan, reason=reason)

    def checkout(self, plan: str, *, annual: bool = False) -> BillingLink:
        """`POST /api/billing/checkout`: a Stripe Checkout page for `plan`;
        the plan changes once Stripe confirms the payment. `ConflictError`
        while an invoice is overdue."""
        body, rid = self._object(org_req.checkout_request(plan, annual))
        return BillingLink.from_dict(body, request_id=rid)

    def portal(self) -> BillingLink:
        """`POST /api/billing/portal`: the Stripe customer portal, where the
        card and invoices are managed."""
        body, rid = self._object(org_req.portal_request())
        return BillingLink.from_dict(body, request_id=rid)

    def invoices(self) -> InvoiceList:
        """`GET /api/billing/invoices`: the latest 24, newest first."""
        body, rid = self._object(org_req.invoices_request())
        return InvoiceList.from_dict(body, request_id=rid)

    def change_plan(self, plan: str, *, reason: str | None = None) -> Entitlements:
        """`PUT /api/plan`: move to `plan` directly -- in practice to
        `free`; a paid plan starts with `checkout` and the server answers
        402 saying so. Nothing is deleted on a downgrade, but writes past
        the new limits stop (`writes_blocked`)."""
        body, rid = self._object(org_req.put_plan_request(plan, reason))
        return Entitlements.from_dict(body, request_id=rid)

    def plan_history(self) -> PlanHistory:
        """`GET /api/plan/history`: the latest 50 plan and status changes."""
        body, rid = self._object(org_req.plan_history_request())
        return PlanHistory.from_dict(body, request_id=rid)


class AsyncBilling(_Async):
    async def status(self) -> BillingStatus:
        body, rid = await self._object(req.billing_status_request())
        return BillingStatus.from_dict(body, request_id=rid)

    async def plans(self) -> PlanCatalog:
        body, rid = await self._object(req.list_plans_request())
        return PlanCatalog.from_dict(body, request_id=rid)

    async def switch_plan(self, plan: str, *, reason: str | None = None) -> Entitlements:
        return await self.change_plan(plan, reason=reason)

    async def checkout(self, plan: str, *, annual: bool = False) -> BillingLink:
        body, rid = await self._object(org_req.checkout_request(plan, annual))
        return BillingLink.from_dict(body, request_id=rid)

    async def portal(self) -> BillingLink:
        body, rid = await self._object(org_req.portal_request())
        return BillingLink.from_dict(body, request_id=rid)

    async def invoices(self) -> InvoiceList:
        body, rid = await self._object(org_req.invoices_request())
        return InvoiceList.from_dict(body, request_id=rid)

    async def change_plan(self, plan: str, *, reason: str | None = None) -> Entitlements:
        body, rid = await self._object(org_req.put_plan_request(plan, reason))
        return Entitlements.from_dict(body, request_id=rid)

    async def plan_history(self) -> PlanHistory:
        body, rid = await self._object(org_req.plan_history_request())
        return PlanHistory.from_dict(body, request_id=rid)


class Plans(_Sync):
    def list(self) -> PlanCatalog:
        """`GET /api/plans`: the public price list; no credential is sent."""
        body, rid = self._object(req.list_plans_request())
        return PlanCatalog.from_dict(body, request_id=rid)


class AsyncPlans(_Async):
    async def list(self) -> PlanCatalog:
        body, rid = await self._object(req.list_plans_request())
        return PlanCatalog.from_dict(body, request_id=rid)


class Policy(_Sync):
    def stats(self) -> PolicyStats:
        """`GET /api/policy/stats`: the public policy page's report and
        enforcement counts; no credential is sent."""
        body, rid = self._object(req.policy_stats_request())
        return PolicyStats.from_dict(body, request_id=rid)


class AsyncPolicy(_Async):
    async def stats(self) -> PolicyStats:
        body, rid = await self._object(req.policy_stats_request())
        return PolicyStats.from_dict(body, request_id=rid)
