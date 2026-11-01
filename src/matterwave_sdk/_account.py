"""The account and data clients, sync and async: `account` (the caller's
name, terms and enforcement state), `preferences`, `sessions`, `exports`,
`imports`, `speech`, `billing`, `plans` and `policy`. Each method is one
route, built in `_account_requests` and parsed into `_account_models`.

Every route here takes an API key, acting as its owner, or a person's
token: exports need a key with `chat:read`, imports one with `chat:write`.
`plans.list` and `policy.stats` are public and send no credential.
"""

from __future__ import annotations

import asyncio
import time
from typing import IO, Any

from . import _account_requests as req
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
from ._resources import _Async, _Sync
from .errors import APITimeoutError, MatterWaveError

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
    """The caller's own account: their name, the terms they accepted, and
    whether moderation holds them."""

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
    """The org's billing as any member sees it."""

    def status(self) -> BillingStatus:
        """`GET /api/billing/status`: the plan and its limits, whether
        billing is on, how the org pays, and the dunning schedule when a
        payment is late."""
        body, rid = self._object(req.billing_status_request())
        return BillingStatus.from_dict(body, request_id=rid)


class AsyncBilling(_Async):
    async def status(self) -> BillingStatus:
        body, rid = await self._object(req.billing_status_request())
        return BillingStatus.from_dict(body, request_id=rid)


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
