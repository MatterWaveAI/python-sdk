"""The native clients against a RUNNING MatterWave API, not respx.

Skipped unless MATTERWAVE_LOCAL_URL names an API started with
MW_AUTH_MODE=none on Postgres (web/e2e/playwright.local.config.ts has the
stack). Under that mode an unauthenticated request is the dev principal,
which mints the key these tests then drive the SDK with -- the SDK itself
always sends a key.

A key acts for its owner within its scopes: webhooks need `webhooks:manage`
and the org usage report `usage:read`, which the key minted here does not
hold, so both answer it with 403 -- asserted so a change on either side is
seen.
"""

from __future__ import annotations

import os
import uuid

import httpx
import pytest

from matterwave_sdk import MatterWave
from matterwave_sdk.errors import NotFoundError, PermissionDeniedError

BASE = os.environ.get("MATTERWAVE_LOCAL_URL", "")

pytestmark = pytest.mark.skipif(
    not BASE, reason="MATTERWAVE_LOCAL_URL names a running local API (MW_AUTH_MODE=none)"
)

RUN = uuid.uuid4().hex[:8]


@pytest.fixture(scope="module")
def client() -> MatterWave:
    res = httpx.post(
        f"{BASE}/api/keys",
        json={"name": f"sdk-py-{RUN}", "scopes": ["chat:read", "chat:write", "keys:manage"]},
        timeout=30,
    )
    assert res.status_code == 201, res.text
    minted = res.json()
    yield MatterWave(api_key=minted["key"], base_url=BASE, max_retries=0)
    httpx.delete(f"{BASE}/api/keys/{minted['id']}", timeout=30)


def test_keys_list_create_update_rotate_revoke(client: MatterWave) -> None:
    made = client.keys.create(f"sdk-child-{RUN}", scopes=["chat:read"], expires_in_days=30)
    assert made.key and made.scopes == ["chat:read"] and made.expires_at
    assert any(k.id == made.id for k in client.keys.list().data)

    updated = client.keys.update(
        made.id, name=f"sdk-child-{RUN}-renamed", rpm_limit=30, expires_in_days=None
    )
    assert updated.name.endswith("-renamed") and updated.rpm_limit == 30
    assert updated.expires_at is None

    rotated = client.keys.rotate(made.id)
    assert rotated.key and rotated.key != made.key and rotated.previous_prefix == made.prefix
    # The new secret works, as the key it names.
    with MatterWave(api_key=rotated.key, base_url=BASE, max_retries=0) as child:
        assert child.me().subject

    client.keys.revoke(made.id)
    row = next(k for k in client.keys.list().data if k.id == made.id)
    assert row.revoked_at is not None
    with pytest.raises(NotFoundError):
        client.keys.revoke(str(uuid.uuid4()))


def test_usage_balance(client: MatterWave) -> None:
    balance = client.usage.get()
    assert balance.unit and balance.included >= 0 and balance.used >= 0


def test_org_usage_and_webhooks_refuse_a_key_without_their_scopes(client: MatterWave) -> None:
    with pytest.raises(PermissionDeniedError):
        client.usage.org()
    with pytest.raises(PermissionDeniedError):
        client.usage.export_csv()
    with pytest.raises(PermissionDeniedError):
        client.webhooks.list()
    with pytest.raises(PermissionDeniedError):
        client.webhooks.create("https://app.matterwave.ai/hook", ["message.completed"])


def test_attachments_upload_get_download_delete(client: MatterWave) -> None:
    limits = client.attachments.limits()
    assert limits.max_upload_bytes > 0
    body = f"notes for {RUN}\n".encode()
    doc = client.attachments.upload(body, f"notes-{RUN}.txt")
    assert doc.filename == f"notes-{RUN}.txt" and doc.size_bytes == len(body)
    fetched = client.attachments.get(doc.id)
    assert fetched.id == doc.id and fetched.download_url
    if fetched.scan_status == "clean":
        assert client.attachments.download(fetched) == body
    client.attachments.delete(doc.id)
    with pytest.raises(NotFoundError):
        client.attachments.get(doc.id)


def test_library_folders_tags_and_search(client: MatterWave) -> None:
    conv = client.conversations.create(title=f"sdk library {RUN}")
    folder = client.library.create_folder(f"Folder {RUN}")
    renamed = client.library.update_folder(folder.id, name=f"Folder {RUN} renamed")
    assert renamed.name.endswith("renamed")
    moved = client.library.move(conv.id, folder.id)
    assert moved.id == conv.id
    tag = client.library.create_tag(f"tag-{RUN}")
    tags = client.library.set_tags(conv.id, [tag.id])
    assert tags.tag_ids == [tag.id]
    library = client.library.get()
    assert any(f.id == folder.id for f in library.folders)
    assert any(t.id == tag.id for t in library.tags)
    assert client.library.search(f"sdk library {RUN}", limit=5) is not None
    client.library.move(conv.id, None)
    client.library.delete_tag(tag.id)
    client.library.delete_folder(folder.id)
    library = client.library.get()
    assert all(f.id != folder.id for f in library.folders)


def test_shares_create_list_view_revoke(client: MatterWave) -> None:
    conv = client.conversations.create(title=f"sdk share {RUN}")
    # Nothing to share until there is a message: one turn, answered by the
    # stack's fake upstream.
    assert "".join(client.conversations.send(conv.id, "Hello there.").text())
    made = client.shares.create(conv.id, expires_in_s=3600)
    assert made.token and made.share.conversation_id == conv.id
    assert any(s.id == made.share.id for s in client.shares.list(conv.id).data)
    assert any(s.id == made.share.id for s in client.shares.list_mine().data)
    seen = client.shares.view(made.token)
    assert seen.title == f"sdk share {RUN}"
    client.shares.revoke(made.share.id)
    listed = [s for s in client.shares.list(conv.id).data if s.id == made.share.id]
    assert not listed or listed[0].status == "revoked"
    with pytest.raises(NotFoundError):
        client.shares.view(made.token)
