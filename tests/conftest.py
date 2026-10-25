"""Fixtures: a client aimed at a stand-in origin, with respx answering, and
the sleeps replaced so a retry test measures the delays it asked for rather
than waiting through them."""

from __future__ import annotations

import asyncio
import time

import pytest
import respx

from matterwave_sdk import AsyncMatterWave, MatterWave

BASE_URL = "https://matterwave.test"
API_KEY = "mw_test_key"
REQUEST_ID = "req-0123456789abcdef"


@pytest.fixture()
def api():
    with respx.mock(base_url=BASE_URL, assert_all_called=False) as mock:
        yield mock


@pytest.fixture()
def client() -> MatterWave:
    return MatterWave(api_key=API_KEY, base_url=BASE_URL, max_retries=2)


@pytest.fixture()
def async_client() -> AsyncMatterWave:
    return AsyncMatterWave(api_key=API_KEY, base_url=BASE_URL, max_retries=2)


@pytest.fixture()
def sleeps(monkeypatch) -> list[float]:
    """Every delay the clients asked for, sync and async, without waiting."""
    waited: list[float] = []

    def fake_sleep(seconds: float) -> None:
        waited.append(seconds)

    async def fake_asleep(seconds: float) -> None:
        waited.append(seconds)

    monkeypatch.setattr(time, "sleep", fake_sleep)
    monkeypatch.setattr(asyncio, "sleep", fake_asleep)
    return waited


def completion_body(text: str = "Hello there, friend of mine.") -> dict:
    return {
        "id": "chatcmpl-01",
        "object": "chat.completion",
        "created": 1790621573,
        "model": "grt-nano",
        "choices": [
            {"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}
        ],
        "usage": {"prompt_tokens": 41, "completion_tokens": 9, "total_tokens": 50},
    }


def chunk(delta: dict | None, finish: str | None = None, usage: dict | None = None) -> str:
    """One SSE frame the way routes_openai.py writes it: `data:` + one-line JSON."""
    import json

    body: dict = {
        "id": "chatcmpl-01",
        "object": "chat.completion.chunk",
        "created": 1790621573,
        "model": "grt-nano",
        "choices": [],
    }
    if delta is not None:
        body["choices"] = [{"index": 0, "delta": delta, "finish_reason": finish}]
    if usage is not None or delta is None:
        body["usage"] = usage
    return f"data: {json.dumps(body, separators=(',', ':'))}\n\n"


def error_body(message: str, error_type: str, code: str | None, param: str | None = None) -> dict:
    return {"error": {"message": message, "type": error_type, "param": param, "code": code}}
