"""Every example in examples/ runs: each one's `run` is called against the
fake API with the answers its routes give, and what it prints and returns
is checked. Their PEP 723 blocks name only the SDK and their imports are the
stdlib plus the SDK, so `uv run examples/<name>.py` can install their world.
"""

from __future__ import annotations

import ast
import hashlib
import hmac
import importlib.util
import json
import re
import sys
import time
from pathlib import Path
from types import ModuleType

import httpx
import pytest
import respx
from conftest import API_KEY, BASE_URL, chunk, completion_body

from matterwave_sdk import MatterWave

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"
NAMES = (
    "chat",
    "streaming",
    "structured_output",
    "webhook_receiver",
    "device_sign_in",
    "pagination",
)


def load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(f"example_{name}", EXAMPLES / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _normal(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


@pytest.mark.parametrize("name", NAMES)
def test_each_example_needs_only_the_sdk(name: str) -> None:
    text = (EXAMPLES / f"{name}.py").read_text(encoding="utf-8")
    block = re.search(r"# /// script\n(.*?)# ///", text, re.DOTALL)
    assert block is not None, f"{name} carries no PEP 723 script block"
    match = re.search(r"dependencies\s*=\s*(\[.*\])", block.group(1))
    assert match is not None
    named = {_normal(re.split(r"[<>=;\[ ]", d)[0]) for d in ast.literal_eval(match.group(1))}
    assert named == {"matterwave-sdk"}
    imported: set[str] = set()
    for node in ast.walk(ast.parse(text)):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            imported.add(node.module.split(".")[0])
    assert imported - set(sys.stdlib_module_names) == {"matterwave_sdk"}


def test_chat(api, client: MatterWave, capsys) -> None:
    api.post("/v1/chat/completions").respond(200, json=completion_body("Hello there."))
    assert load("chat").run(client, "hi") == 0
    assert capsys.readouterr().out == "Hello there.\n[grt-nano: 50 tokens]\n"


def test_chat_reports_a_refusal(api, client: MatterWave, capsys) -> None:
    api.post("/v1/chat/completions").respond(
        401, json={"error": {"message": "bad key", "type": "invalid_request_error", "code": "x"}}
    )
    assert load("chat").run(client, "hi") == 1
    assert "failed: 401" in capsys.readouterr().err


def test_streaming(api, client: MatterWave, capsys) -> None:
    body = (
        chunk({"role": "assistant", "content": ""})
        + chunk({"content": "Rivers "})
        + chunk({"content": "run."})
        + chunk({}, "stop")
        + chunk(None, usage={"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10})
        + "data: [DONE]\n\n"
    )
    route = api.post("/v1/chat/completions").respond(
        200, headers={"Content-Type": "text/event-stream"}, content=body
    )
    assert load("streaming").run(client, "haiku") == 0
    assert capsys.readouterr().out == "Rivers run.\n[stop: 7 in, 3 out]\n"
    assert json.loads(route.calls.last.request.content)["stream_options"] == {"include_usage": True}


def test_structured_output(api, client: MatterWave, capsys) -> None:
    recipe = {"name": "Pancakes", "minutes": 20, "ingredients": ["flour", "milk", "eggs"]}
    api.post("/v1/chat/completions").respond(200, json=completion_body(json.dumps(recipe)))
    assert load("structured_output").run(client, "pancakes") == 0
    assert capsys.readouterr().out == "Pancakes (20 min): flour, milk, eggs\n"


def test_structured_output_refuses_an_answer_that_is_not_a_recipe(
    api, client: MatterWave, capsys
) -> None:
    api.post("/v1/chat/completions").respond(200, json=completion_body('{"name": 3}'))
    assert load("structured_output").run(client, "pancakes") == 1
    assert "not a recipe" in capsys.readouterr().err


def test_webhook_receiver_handles_a_signed_delivery_once() -> None:
    receiver = load("webhook_receiver")
    secret = "whsec_example"
    body = json.dumps({"id": "evt_9", "type": "ping", "data": {"n": 1}}).encode()
    now = int(time.time())
    mac = hmac.new(secret.encode(), f"{now}.".encode() + body, hashlib.sha256).hexdigest()
    headers = {"MatterWave-Signature": f"t={now},v1={mac}"}
    assert receiver.handle_delivery(body, headers, secret) == (204, "evt_9: ping {'n': 1}")
    assert receiver.handle_delivery(body, headers, secret) == (204, "evt_9: already handled")
    status, line = receiver.handle_delivery(body + b" ", headers, secret)
    assert status == 400 and line.startswith("refused:")


def test_device_sign_in_with_a_key(api, sleeps, capsys) -> None:
    api.post("/api/auth/device").respond(
        200,
        json={
            "device_code": "dc",
            "user_code": "ABCD-1234",
            "verification_uri": f"{BASE_URL}/device",
            "expires_in": 600,
            "interval": 5,
        },
    )
    api.post("/api/auth/device/token").respond(
        200, json={"access_token": "mw_new_key", "token_type": "Bearer", "scopes": ["chat:read"]}
    )
    me = api.get("/api/me").respond(
        200,
        json={"subject": "u-1", "kind": "user", "email": "a@example.test", "org_role": "member"},
    )
    client = MatterWave(base_url=BASE_URL)
    assert load("device_sign_in").run(client, "key") == 0
    out = capsys.readouterr().out
    assert "enter ABCD-1234" in out and "signed in as a@example.test (member)" in out
    assert me.calls.last.request.headers["authorization"] == "Bearer mw_new_key"


def test_device_sign_in_as_a_person(sleeps, capsys) -> None:
    issuer = "https://auth.matterwave.test"
    with respx.mock(assert_all_called=False) as idp:
        idp.get(f"{BASE_URL}/api/auth-config").respond(
            200,
            json={
                "auth_mode": "oidc",
                "token_endpoint": f"{issuer}/oauth/v2/token",
                "scopes": ["openid"],
                "native_client_id": "native",
                "issuer_device_authorization_endpoint": f"{issuer}/oauth/v2/device_authorization",
            },
        )
        idp.post(f"{issuer}/oauth/v2/device_authorization").respond(
            200,
            json={
                "device_code": "dc",
                "user_code": "WXYZ-9876",
                "verification_uri": f"{issuer}/device",
                "expires_in": 300,
                "interval": 5,
            },
        )
        idp.post(f"{issuer}/oauth/v2/token").respond(
            200, json={"access_token": "person-at", "expires_in": 3600, "refresh_token": "rt"}
        )
        me = idp.get(f"{BASE_URL}/api/me").respond(
            200,
            json={"subject": "u-1", "kind": "user", "email": "o@example.test", "org_role": "owner"},
        )
        assert load("device_sign_in").run(MatterWave(base_url=BASE_URL), "person") == 0
    assert "signed in as o@example.test (owner)" in capsys.readouterr().out
    assert me.calls.last.request.headers["authorization"] == "Bearer person-at"


def test_device_sign_in_reports_a_declined_code(api, sleeps, capsys) -> None:
    api.post("/api/auth/device").respond(
        200, json={"device_code": "dc", "user_code": "C", "verification_uri": "u", "interval": 5}
    )
    api.post("/api/auth/device/token").respond(400, json={"error": "access_denied"})
    assert load("device_sign_in").run(MatterWave(base_url=BASE_URL), "key") == 1
    assert "not signed in: access_denied" in capsys.readouterr().err


def test_pagination(api, capsys) -> None:
    def page(ids: list[str], cursor: str | None, filter: str) -> dict:
        return {
            "data": [{"id": i, "title": f"T{i}"} for i in ids],
            "filter": filter,
            "next_cursor": cursor,
        }

    def conversations(request: httpx.Request) -> httpx.Response:
        params = request.url.params
        if params["filter"] == "archived":
            return httpx.Response(
                200,
                json=page(["a1", "a2"], "c2", "archived")
                if "cursor" not in params
                else page(["a3"], None, "archived"),
            )
        return httpx.Response(200, json=page(["p1"], None, "pinned"))

    api.get("/api/conversations").mock(side_effect=conversations)
    api.get("/api/search").respond(
        200,
        json={
            "data": [{"id": f"s{i}", "title": f"Invoice {i}", "snippet": ""} for i in range(8)],
            "query": "invoice",
            "next_cursor": "more",
        },
    )
    client = MatterWave(api_key=API_KEY, base_url=BASE_URL)
    assert load("pagination").run(client, "invoice") == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0] == "archived conversations: 3"
    assert out[1:6] == [f"match: Invoice {i}" for i in range(5)]
    assert out[6] == "pinned: Tp1"
    assert api.routes[1].call_count == 1  # five hits needed no second search page
