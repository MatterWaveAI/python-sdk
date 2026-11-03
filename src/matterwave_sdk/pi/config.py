"""Pi 1.0 environment and provider configuration helpers for MatterWave."""

from __future__ import annotations

import inspect
import os
from collections.abc import Mapping
from typing import Any, Literal, TypedDict

from .. import _base
from ..errors import MatterWaveConfigError, MissingAPIKeyError

PRESET_ID_ENV = "MATTERWAVE_PRESET_ID"
TEMPORARY_ENV = "MATTERWAVE_TEMPORARY"
DEFAULT_MATTERWAVE_ORIGIN = "https://api.matterwave.ai"


class PiModelCost(TypedDict):
    input: float
    output: float
    cacheRead: float
    cacheWrite: float


class PiModelConfig(TypedDict, total=False):
    """A model entry in a Pi 1.0 provider configuration (`models` list)."""

    id: str
    name: str
    api: str
    reasoning: bool
    input: list[Literal["text", "image"]]
    cost: PiModelCost
    contextWindow: int
    maxTokens: int
    headers: dict[str, str]
    compat: dict[str, Any]


DEFAULT_PI_MODELS: tuple[PiModelConfig, ...] = (
    {
        "id": "grt-1",
        "name": "MatterWave GRT-1 (Reasoning & Coding)",
        "reasoning": True,
        "input": ["text", "image"],
        "cost": {"input": 2.0, "output": 8.0, "cacheRead": 0.5, "cacheWrite": 2.0},
        "contextWindow": 262144,
        "maxTokens": 32768,
    },
    {
        "id": "grt-1-mini",
        "name": "MatterWave GRT-1 Mini",
        "reasoning": True,
        "input": ["text", "image"],
        "cost": {"input": 0.4, "output": 1.6, "cacheRead": 0.1, "cacheWrite": 0.4},
        "contextWindow": 131072,
        "maxTokens": 16384,
    },
    {
        "id": "grt-1-fast",
        "name": "MatterWave GRT-1 Fast",
        "reasoning": False,
        "input": ["text", "image"],
        "cost": {"input": 0.15, "output": 0.6, "cacheRead": 0.04, "cacheWrite": 0.15},
        "contextWindow": 131072,
        "maxTokens": 16384,
    },
    {
        "id": "matterwave-code",
        "name": "MatterWave Code",
        "reasoning": True,
        "input": ["text", "image"],
        "cost": {"input": 1.5, "output": 6.0, "cacheRead": 0.375, "cacheWrite": 1.5},
        "contextWindow": 262144,
        "maxTokens": 32768,
    },
    {
        "id": "matterwave-default",
        "name": "MatterWave Default Router",
        "reasoning": True,
        "input": ["text", "image"],
        "cost": {"input": 1.0, "output": 4.0, "cacheRead": 0.25, "cacheWrite": 1.0},
        "contextWindow": 131072,
        "maxTokens": 16384,
    },
)

_DEFAULTS_BY_ID: dict[str, PiModelConfig] = {
    m["id"]: m for m in DEFAULT_PI_MODELS if "id" in m
}


def _extract_api_key(client_or_api_key: Any) -> str | None:
    if isinstance(client_or_api_key, str):
        key = client_or_api_key.strip()
        return key or None
    if client_or_api_key is None:
        return _base.resolve_api_key(None)

    direct_key = getattr(client_or_api_key, "api_key", None)
    if isinstance(direct_key, str) and direct_key.strip():
        return direct_key.strip()

    config = getattr(client_or_api_key, "_config", None)
    if config is not None:
        cfg_key = getattr(config, "api_key", None)
        if isinstance(cfg_key, str) and cfg_key.strip():
            return cfg_key.strip()
        token = getattr(config, "access_token", None)
        if isinstance(token, str) and token.strip():
            return token.strip()
        if callable(token):
            resolved = token()
            if inspect.isawaitable(resolved):
                if inspect.iscoroutine(resolved):
                    resolved.close()
                raise MatterWaveConfigError(
                    "access_token callable returned an awaitable; pass a resolved token string"
                )
            if isinstance(resolved, str) and resolved.strip():
                return resolved.strip()

    return _base.resolve_api_key(None)


def _extract_base_url(client_or_api_key: Any, explicit_base_url: str | None) -> str:
    candidate = explicit_base_url
    if candidate is None and not isinstance(client_or_api_key, str) and client_or_api_key is not None:
        direct_url = getattr(client_or_api_key, "base_url", None)
        if isinstance(direct_url, str) and direct_url.strip():
            candidate = direct_url.strip()
        else:
            config = getattr(client_or_api_key, "_config", None)
            cfg_url = getattr(config, "base_url", None) if config is not None else None
            if isinstance(cfg_url, str) and cfg_url.strip():
                candidate = cfg_url.strip()

    if candidate is None:
        env_url = os.environ.get(_base.BASE_URL_ENV, "").strip()
        candidate = env_url or DEFAULT_MATTERWAVE_ORIGIN

    return _base.resolve_base_url(candidate)


def _extract_default_headers(client: Any) -> dict[str, str]:
    headers: dict[str, str] = {"User-Agent": _base.USER_AGENT}
    config = getattr(client, "_config", None)
    cfg_headers = getattr(config, "default_headers", None) if config is not None else None
    if isinstance(cfg_headers, Mapping):
        for k, v in cfg_headers.items():
            if isinstance(k, str) and isinstance(v, str):
                headers[k] = v
    direct_headers = getattr(client, "default_headers", None)
    if isinstance(direct_headers, Mapping):
        for k, v in direct_headers.items():
            if isinstance(k, str) and isinstance(v, str):
                headers[k] = v
    return headers


def build_pi_env(
    client_or_api_key: Any = None,
    *,
    base_url: str | None = None,
    preset_id: str | None = None,
    temporary: bool = False,
    extra_env: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Build the environment dictionary for a `pi` subprocess.

    Populates `MATTERWAVE_API_KEY`, `MATTERWAVE_BASE_URL`, `MATTERWAVE_PRESET_ID`
    (when a `preset_id` is supplied or empty string otherwise), and
    `MATTERWAVE_TEMPORARY` from a `MatterWave` / `AsyncMatterWave` client or
    explicit API key string.
    """
    api_key = _extract_api_key(client_or_api_key)
    if not api_key:
        raise MissingAPIKeyError(
            f"No API key: pass a MatterWave client, an api_key string, or set {_base.API_KEY_ENV}."
        )
    resolved_url = _extract_base_url(client_or_api_key, base_url)

    env: dict[str, str] = {
        _base.API_KEY_ENV: api_key,
        _base.BASE_URL_ENV: resolved_url,
        PRESET_ID_ENV: preset_id if preset_id is not None else "",
        TEMPORARY_ENV: "1" if temporary else "0",
    }
    if extra_env:
        for k, v in extra_env.items():
            env[str(k)] = str(v)
    return env


def _model_to_pi_config(item: Any) -> PiModelConfig | None:
    model_id: str | None = None
    model_name: str | None = None
    if isinstance(item, Mapping):
        raw_id = item.get("id")
        if isinstance(raw_id, str) and raw_id.strip():
            model_id = raw_id.strip()
        raw_name = item.get("name")
        if isinstance(raw_name, str) and raw_name.strip():
            model_name = raw_name.strip()
    else:
        raw_id = getattr(item, "id", None)
        if isinstance(raw_id, str) and raw_id.strip():
            model_id = raw_id.strip()
        raw_name = getattr(item, "name", None)
        if isinstance(raw_name, str) and raw_name.strip():
            model_name = raw_name.strip()

    if not model_id:
        return None

    if model_id in _DEFAULTS_BY_ID:
        base = dict(_DEFAULTS_BY_ID[model_id])
        if model_name:
            base["name"] = model_name
        return base  # type: ignore[return-value]

    is_reasoning = any(
        token in model_id.lower() for token in ("grt", "reason", "o1", "o3", "r1", "thinking", "code")
    )
    return {
        "id": model_id,
        "name": model_name or model_id,
        "reasoning": is_reasoning,
        "input": ["text", "image"],
        "cost": {"input": 1.0, "output": 4.0, "cacheRead": 0.25, "cacheWrite": 1.0},
        "contextWindow": 131072,
        "maxTokens": 16384,
    }


def build_pi_provider_config(
    client: Any,
    *,
    preset_id: str | None = None,
    temporary: bool = False,
) -> dict[str, Any]:
    """Build a Pi 1.0 provider configuration dictionary from a MatterWave client.

    Queries `client.models.list()` when available and falls back gracefully to
    the default MatterWave & GRT model catalog on network/auth errors or async
    clients.
    """
    api_key = _extract_api_key(client) or f"${_base.API_KEY_ENV}"
    base_url = _extract_base_url(client, None)
    headers = _extract_default_headers(client)
    if preset_id:
        headers["X-MatterWave-Preset-Id"] = preset_id
    if temporary:
        headers["X-MatterWave-Temporary"] = "true"

    models: list[PiModelConfig] = []
    models_ns = getattr(client, "models", None)
    list_fn = getattr(models_ns, "list", None) if models_ns is not None else None
    if callable(list_fn):
        try:
            listed = list_fn()
            if inspect.isawaitable(listed):
                if inspect.iscoroutine(listed):
                    listed.close()
            else:
                items = getattr(listed, "data", listed)
                for item in items:
                    converted = _model_to_pi_config(item)
                    if converted is not None:
                        models.append(converted)
        except Exception:
            models = []

    if not models:
        models = [dict(m) for m in DEFAULT_PI_MODELS]  # type: ignore[misc]

    return {
        "name": "MatterWave",
        "baseUrl": f"{base_url}{_base.API_PREFIX}",
        "apiKey": api_key,
        "api": "openai-completions",
        "authHeader": True,
        "headers": headers,
        "models": models,
    }


async def async_build_pi_provider_config(
    client: Any,
    *,
    preset_id: str | None = None,
    temporary: bool = False,
) -> dict[str, Any]:
    """Asynchronously build a Pi 1.0 provider configuration dictionary from an `AsyncMatterWave` client."""
    api_key = _extract_api_key(client) or f"${_base.API_KEY_ENV}"
    base_url = _extract_base_url(client, None)
    headers = _extract_default_headers(client)
    if preset_id:
        headers["X-MatterWave-Preset-Id"] = preset_id
    if temporary:
        headers["X-MatterWave-Temporary"] = "true"

    models: list[PiModelConfig] = []
    models_ns = getattr(client, "models", None)
    list_fn = getattr(models_ns, "list", None) if models_ns is not None else None
    if callable(list_fn):
        try:
            listed = list_fn()
            if inspect.isawaitable(listed):
                listed = await listed
            items = getattr(listed, "data", listed)
            for item in items:
                converted = _model_to_pi_config(item)
                if converted is not None:
                    models.append(converted)
        except Exception:
            models = []

    if not models:
        models = [dict(m) for m in DEFAULT_PI_MODELS]  # type: ignore[misc]

    return {
        "name": "MatterWave",
        "baseUrl": f"{base_url}{_base.API_PREFIX}",
        "apiKey": api_key,
        "api": "openai-completions",
        "authHeader": True,
        "headers": headers,
        "models": models,
    }
