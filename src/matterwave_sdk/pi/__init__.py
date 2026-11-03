"""Pi 1.0 (`pi --mode rpc`) Python driver and provider configuration helpers."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .config import (
    DEFAULT_PI_MODELS,
    PiModelConfig,
    PiModelCost,
    async_build_pi_provider_config,
    build_pi_env,
    build_pi_provider_config,
)
from .rpc import AsyncPiRpcSession, PiEvent, PiRpcSession


class PiResource:
    """Pi 1.0 (`pi --mode rpc`) configuration and session helpers bound to `MatterWave`."""

    def __init__(self, client: Any) -> None:
        self._client = client

    def build_env(
        self,
        *,
        preset_id: str | None = None,
        temporary: bool = False,
        extra_env: Mapping[str, str] | None = None,
    ) -> dict[str, str]:
        """Build the environment dictionary for a `pi` subprocess from this client."""
        return build_pi_env(
            self._client,
            preset_id=preset_id,
            temporary=temporary,
            extra_env=extra_env,
        )

    def build_provider_config(
        self,
        *,
        preset_id: str | None = None,
        temporary: bool = False,
    ) -> dict[str, Any]:
        """Build a Pi 1.0 provider configuration dictionary from this client."""
        return build_pi_provider_config(
            self._client,
            preset_id=preset_id,
            temporary=temporary,
        )

    def create_rpc_session(self, **kwargs: Any) -> PiRpcSession:
        """Create a synchronous `PiRpcSession` pre-configured with `client=self._client`."""
        kwargs.setdefault("client", self._client)
        return PiRpcSession(**kwargs)


class AsyncPiResource:
    """Pi 1.0 (`pi --mode rpc`) configuration and async session helpers bound to `AsyncMatterWave`."""

    def __init__(self, client: Any) -> None:
        self._client = client

    def build_env(
        self,
        *,
        preset_id: str | None = None,
        temporary: bool = False,
        extra_env: Mapping[str, str] | None = None,
    ) -> dict[str, str]:
        """Build the environment dictionary for a `pi` subprocess from this client."""
        return build_pi_env(
            self._client,
            preset_id=preset_id,
            temporary=temporary,
            extra_env=extra_env,
        )

    async def build_provider_config(
        self,
        *,
        preset_id: str | None = None,
        temporary: bool = False,
    ) -> dict[str, Any]:
        """Asynchronously build a Pi 1.0 provider configuration dictionary from this client."""
        return await async_build_pi_provider_config(
            self._client,
            preset_id=preset_id,
            temporary=temporary,
        )

    def create_rpc_session(self, **kwargs: Any) -> AsyncPiRpcSession:
        """Create an `AsyncPiRpcSession` pre-configured with `client=self._client`."""
        kwargs.setdefault("client", self._client)
        return AsyncPiRpcSession(**kwargs)


__all__ = [
    "AsyncPiResource",
    "AsyncPiRpcSession",
    "DEFAULT_PI_MODELS",
    "PiEvent",
    "PiModelConfig",
    "PiModelCost",
    "PiResource",
    "PiRpcSession",
    "async_build_pi_provider_config",
    "build_pi_env",
    "build_pi_provider_config",
]
