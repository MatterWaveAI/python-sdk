"""Python and TypeScript offer the same client. This SDK's public surface
-- every method under the client, the streams' methods, the constructor's
and `with_options`' settings, the error classes -- is written to
`tests/surface.json` in the TypeScript SDK's spelling (camelCase), and
compared with the TypeScript SDK's own `tests/surface.json` when that
checkout is beside this one (CI clones it there; `MATTERWAVE_SIBLING_SDK`
names another place). Every name one side has and the other lacks must be
listed, with its reason, in `tests/parity.json`, which both SDKs carry
word for word.

After a change to the surface, `MATTERWAVE_UPDATE_SURFACE=1 uv run pytest
tests/test_parity.py` rewrites `tests/surface.json`."""

from __future__ import annotations

import inspect
import json
import os
from pathlib import Path
from typing import Any

import pytest

from matterwave_sdk import errors
from matterwave_sdk._client import ConversationStream, MatterWave, Stream

REPO = Path(__file__).resolve().parent.parent
SURFACE = REPO / "tests" / "surface.json"
PARITY = REPO / "tests" / "parity.json"
SIBLING = Path(os.environ.get("MATTERWAVE_SIBLING_SDK") or REPO.parent / "typescript-sdk")


def camel(name: str) -> str:
    head, *rest = name.split("_")
    return head + "".join(part[:1].upper() + part[1:] for part in rest)


def _is_namespace(value: Any) -> bool:
    module = type(value).__module__
    return module.startswith("matterwave_sdk") and hasattr(value, "__dict__") and not callable(value)


def _walk(obj: Any, prefix: str, out: set[str]) -> None:
    for name in dir(obj):
        if name.startswith("_"):
            continue
        path = f"{prefix}.{camel(name)}"
        if isinstance(inspect.getattr_static(type(obj), name, None), property):
            out.add(path)
            continue
        value = getattr(obj, name)
        if inspect.ismethod(value):
            out.add(path)
        elif _is_namespace(value):
            _walk(value, path, out)
        else:
            out.add(path)


def _parameters(function: Any) -> list[str]:
    return [camel(p) for p in inspect.signature(function).parameters if p != "self"]


def surface() -> list[str]:
    out: set[str] = set()
    _walk(MatterWave(api_key="k", base_url="https://matterwave.test"), "client", out)
    for label, cls in (("Stream", Stream), ("ConversationStream", ConversationStream)):
        for name, member in inspect.getmembers(cls):
            if not name.startswith("_") and (callable(member) or isinstance(member, property)):
                out.add(f"{label}.{camel(name)}")
        for name in ("request_id",):
            out.add(f"{label}.{camel(name)}")
    out.update(f"options.{name}" for name in _parameters(MatterWave.__init__))
    out.update(f"withOptions.{name}" for name in _parameters(MatterWave.with_options))
    for name, value in vars(errors).items():
        if (
            isinstance(value, type)
            and issubclass(value, (Exception, Warning))
            and value.__module__ == errors.__name__
        ):
            out.add(f"errors.{name}")
    return sorted(out)


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def test_the_surface_file_is_current() -> None:
    current = surface()
    if os.environ.get("MATTERWAVE_UPDATE_SURFACE") == "1":
        SURFACE.write_text(json.dumps(current, indent=2) + "\n", encoding="utf-8", newline="\n")
    assert _load(SURFACE) == current, (
        "the public surface changed: run MATTERWAVE_UPDATE_SURFACE=1 uv run pytest tests/test_parity.py"
        " and list any new difference from the TypeScript SDK in tests/parity.json"
    )


def test_every_difference_listed_is_still_a_difference() -> None:
    parity = _load(PARITY)
    ours = set(surface())
    assert set(parity["python_only"]) <= ours, set(parity["python_only"]) - ours
    assert not set(parity["typescript_only"]) & ours, set(parity["typescript_only"]) & ours
    for side in ("python_only", "typescript_only"):
        assert all(reason.strip() for reason in parity[side].values()), f"{side}: a reason is empty"


@pytest.mark.skipif(
    not (SIBLING / "tests" / "surface.json").exists(),
    reason="the TypeScript SDK's checkout is not beside this one",
)
def test_the_typescript_sdk_differs_only_where_listed() -> None:
    parity = _load(PARITY)
    ours = set(_load(SURFACE))
    theirs = set(_load(SIBLING / "tests" / "surface.json"))
    assert ours - theirs == set(parity["python_only"]), (
        "python-only names not in tests/parity.json (or listed but shared): "
        f"{sorted((ours - theirs) ^ set(parity['python_only']))}"
    )
    assert theirs - ours == set(parity["typescript_only"]), (
        "typescript-only names not in tests/parity.json (or listed but shared): "
        f"{sorted((theirs - ours) ^ set(parity['typescript_only']))}"
    )
    assert _load(SIBLING / "tests" / "parity.json") == parity, "the two parity.json files differ"


# --- what was aligned ----------------------------------------------------------


def test_a_bad_setting_is_a_config_error_and_still_a_value_error() -> None:
    from matterwave_sdk import MatterWaveConfigError, MatterWaveError

    for make in (
        lambda: MatterWave(api_key="k", base_url="ftp://nope"),
        lambda: MatterWave(api_key="k", base_url="https://x.test", max_retries=-1),
        lambda: MatterWave(api_key="k", base_url="https://x.test").with_options(idempotency_key=""),
    ):
        with pytest.raises(MatterWaveConfigError) as caught:
            make()
        assert isinstance(caught.value, MatterWaveError)
        assert isinstance(caught.value, ValueError)


def test_extra_headers_and_query_reach_the_wire(api, client) -> None:
    import httpx
    from conftest import completion_body

    chat = api.post("/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=completion_body())
    )
    embed = api.post("/v1/embeddings").mock(
        return_value=httpx.Response(
            200, json={"object": "list", "data": [], "model": "e", "usage": {"prompt_tokens": 1, "total_tokens": 1}}
        )
    )
    client.chat.completions.create(
        messages=[{"role": "user", "content": "hi"}],
        extra_headers={"X-Trace": "t-1"},
        extra_query={"beta": "1"},
        extra_body={"seed": 7},
    )
    client.embeddings.create("hi", extra_headers={"X-Trace": "t-2"}, extra_query={"beta": "2"})
    sent = chat.calls.last.request
    assert sent.headers["x-trace"] == "t-1"
    assert sent.url.params["beta"] == "1"
    assert json.loads(sent.content)["seed"] == 7
    assert embed.calls.last.request.headers["x-trace"] == "t-2"
    assert embed.calls.last.request.url.params["beta"] == "2"


@pytest.mark.asyncio
async def test_the_async_client_takes_the_same_extras(api) -> None:
    import httpx
    from conftest import completion_body

    from matterwave_sdk import AsyncMatterWave

    route = api.post("/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=completion_body())
    )
    client = AsyncMatterWave(api_key="k", base_url="https://matterwave.test")
    await client.chat.completions.create(
        messages=[{"role": "user", "content": "hi"}], extra_headers={"X-Trace": "t-3"}
    )
    assert route.calls.last.request.headers["x-trace"] == "t-3"
