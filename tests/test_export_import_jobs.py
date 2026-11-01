"""The export and import job resources (`client.exports`, `client.imports`)
against the fake API: the Data pane's account export leaving and a
ChatGPT/Claude history arriving. Each test answers with the body the route
in matterwave-app sends and checks the request the client made.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import httpx
import pytest
from conftest import API_KEY, REQUEST_ID

from matterwave_sdk import (
    APP_OPERATIONS,
    AsyncMatterWave,
    MatterWave,
    NotFoundError,
)

EXPORT_ID = "exp-0001"
IMPORT_ID = "imp-0001"
H = {"X-Request-Id": REQUEST_ID}


def sent(route) -> httpx.Request:
    return route.calls.last.request


def body_of(route) -> dict:
    return json.loads(sent(route).content)


def _family_operations() -> dict[str, tuple[str, str]]:
    keep = ("exports", "imports")
    return {k: v for k, v in APP_OPERATIONS.items() if k.split(".")[0] in keep}


def test_every_job_operation_names_a_method_on_both_clients() -> None:
    for client in (
        MatterWave(api_key="k", base_url="https://matterwave.test"),
        AsyncMatterWave(api_key="k", base_url="https://matterwave.test"),
    ):
        for dotted in _family_operations():
            target: object = client
            for part in dotted.split("."):
                assert hasattr(target, part), f"{type(client).__name__} has no {dotted}"
                target = getattr(target, part)
            assert callable(target)


def _api_routes() -> set[tuple[str, str]]:
    found: set[tuple[str, str]] = set()
    for prefix_width in (2, 3, 4):
        base = Path(__file__).resolve().parents[prefix_width]
        for candidate in (
            base / "matterwave-app" / "api" / "matterwave",
            base / "api" / "matterwave",
        ):
            if candidate.is_dir():
                for source in candidate.glob("*.py"):
                    text = source.read_text(encoding="utf-8")
                    prefixes = dict(
                        re.findall(r'(\w+)\s*=\s*APIRouter\([^)]*prefix="([^"]*)"', text)
                    )
                    for router, method, path in re.findall(
                        r'@(\w+)\.(get|post|put|patch|delete)\(\s*"([^"]*)"', text
                    ):
                        found.add((method.upper(), prefixes.get(router, "") + path))
    return found


def test_every_job_operation_is_a_route_the_api_declares() -> None:
    routes = _api_routes()
    if not routes:
        pytest.skip("matterwave-app/api/matterwave is not beside the SDK in this checkout")
    for key, (method, path) in _family_operations().items():
        assert (method, path) in routes, f"{key}: {method} {path} is not an API route"


# --- exports ---------------------------------------------------------------------


def export_body(**extra) -> dict:
    return {
        "id": EXPORT_ID,
        "status": "pending",
        "size_bytes": None,
        "created_at": "2026-10-31T09:00:00+00:00",
        "completed_at": None,
        "expires_at": None,
        **extra,
    }


READY_URL = (
    f"/api/me/export/{EXPORT_ID}/download?expires=1799000000&sig=abc123"
)


def test_export_start_answers_at_once_with_a_pending_job(api, client: MatterWave) -> None:
    route = api.post("/api/me/export").respond(202, json=export_body(), headers=H)
    job = client.exports.start()
    assert sent(route).method == "POST"
    assert sent(route).headers["authorization"] == f"Bearer {API_KEY}"
    assert job.status == "pending" and job.download_url is None
    assert job.request_id == REQUEST_ID


def test_export_get_reports_readiness_with_a_fresh_link(api, client: MatterWave) -> None:
    route = api.get(f"/api/me/export/{EXPORT_ID}").respond(
        200,
        json=export_body(
            status="ready",
            size_bytes=8_432,
            completed_at="2026-10-31T09:01:12+00:00",
            expires_at="2026-11-07T09:01:12+00:00",
            download_url=READY_URL,
        ),
        headers=H,
    )
    job = client.exports.get(EXPORT_ID)
    assert route.called
    assert job.status == "ready" and job.size_bytes == 8_432
    assert job.download_url == READY_URL
    with pytest.raises(ValueError, match="export_id"):
        client.exports.get("../keys")
    # Another person's export is the same 404 as none.
    api.get("/api/me/export/exp-9999").respond(
        404, json={"detail": "No such export."}, headers=H
    )
    with pytest.raises(NotFoundError):
        client.exports.get("exp-9999")


def test_export_download_sends_the_signature_and_the_callers_own_key(
    api, client: MatterWave
) -> None:
    payload = b'{"conversations": []}'
    route = api.get(f"/api/me/export/{EXPORT_ID}/download").respond(
        200,
        content=payload,
        headers={
            **H,
            "content-disposition": 'attachment; filename="matterwave-export-2026-10-31.json"',
        },
    )
    job = export_body(status="ready", download_url=READY_URL)
    from matterwave_sdk import AccountExport

    data = client.exports.download(AccountExport.from_dict(job))
    request = sent(route)
    assert request.headers["authorization"] == f"Bearer {API_KEY}"
    assert request.url.params["expires"] == "1799000000"
    assert request.url.params["sig"] == "abc123"
    assert data == payload
    # A bare URL works too.
    assert client.exports.download(READY_URL) == payload
    with pytest.raises(ValueError, match="download_url"):
        client.exports.download(AccountExport.from_dict(export_body()))
    with pytest.raises(ValueError, match="not an export download URL"):
        client.exports.download("/api/attachments/att-1/content?sig=x")


# --- imports ------------------------------------------------------------------------


def import_body(**extra) -> dict:
    return {
        "id": IMPORT_ID,
        "status": "pending",
        "source": "chatgpt",
        "filename": "conversations.json",
        "size_bytes": 12_345,
        "conversations_total": 7,
        "conversations_done": 0,
        "conversations_created": 0,
        "conversations_existing": 0,
        "conversations_skipped": 0,
        "messages_created": 0,
        "attachments_skipped": 0,
        "branches_dropped": 0,
        "error": None,
        "created_at": "2026-10-31T09:00:00+00:00",
        "started_at": None,
        "completed_at": None,
        **extra,
    }


def test_import_start_uploads_the_file_and_counts_what_was_found(
    api, client: MatterWave
) -> None:
    route = api.post("/api/me/imports").respond(
        202,
        json=import_body(status="pending", conversations_total=7),
        headers=H,
    )
    job = client.imports.start(b'{"conversations": []}', "conversations.json")
    request = sent(route)
    assert request.headers["content-type"].startswith("multipart/form-data")
    assert b"conversations.json" in request.content
    assert job.source == "chatgpt" and job.conversations_total == 7
    with pytest.raises(ValueError, match="filename"):
        client.imports.start(b"x", "")


def test_import_list_is_newest_first(api, client: MatterWave) -> None:
    route = api.get("/api/me/imports").respond(
        200,
        json={
            "data": [
                import_body(id="imp-0002", status="completed",
                            completed_at="2026-10-31T09:05:00+00:00",
                            started_at="2026-10-31T09:04:00+00:00"),
                import_body(id="imp-0003", status="failed", error="Malformed export"),
            ]
        },
        headers=H,
    )
    jobs = client.imports.list()
    assert route.called
    assert [job.id for job in jobs] == ["imp-0002", "imp-0003"]
    assert jobs.data[0].completed_at.endswith("+00:00")
    assert jobs.data[1].error == "Malformed export"


def test_import_get_tracks_progress(api, client: MatterWave) -> None:
    route = api.get(f"/api/me/imports/{IMPORT_ID}").respond(
        200,
        json=import_body(
            status="running",
            conversations_done=4,
            conversations_created=3,
            conversations_existing=1,
            messages_created=52,
        ),
        headers=H,
    )
    job = client.imports.get(IMPORT_ID)
    assert route.called
    assert job.status == "running" and job.conversations_done == 4
    assert job.messages_created == 52
    with pytest.raises(ValueError, match="import_id"):
        client.imports.get("../../keys")


# --- async -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_async_job_resources_make_the_same_requests(
    api, async_client: AsyncMatterWave
) -> None:
    api.post("/api/me/export").respond(202, json=export_body(), headers=H)
    api.get(f"/api/me/export/{EXPORT_ID}").respond(
        200, json=export_body(status="ready", download_url=READY_URL), headers=H
    )
    api.get(f"/api/me/export/{EXPORT_ID}/download").respond(200, content=b"[]")
    api.post("/api/me/imports").respond(202, json=import_body(), headers=H)
    api.get("/api/me/imports").respond(200, json={"data": [import_body()]}, headers=H)
    api.get(f"/api/me/imports/{IMPORT_ID}").respond(
        200, json=import_body(status="completed", conversations_created=7), headers=H
    )
    async with async_client as c:
        started = await c.exports.start()
        assert started.status == "pending"
        ready = await c.exports.get(EXPORT_ID)
        assert ready.download_url == READY_URL
        assert await c.exports.download(ready) == b"[]"
        imported = await c.imports.start(b"{}", "conversations.json")
        assert imported.filename == "conversations.json"
        assert len(await c.imports.list()) == 1
        assert (await c.imports.get(IMPORT_ID)).conversations_created == 7
