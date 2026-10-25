"""Where the key and the origin come from."""

from __future__ import annotations

import pytest

from matterwave_sdk import MatterWave


def test_env_supplies_both(monkeypatch) -> None:
    monkeypatch.setenv("MATTERWAVE_API_KEY", "mw_from_env")
    monkeypatch.setenv("MATTERWAVE_BASE_URL", "https://env.matterwave.test/")
    client = MatterWave()
    assert client._config.api_key == "mw_from_env"
    assert client.base_url == "https://env.matterwave.test"


def test_arguments_win_over_env(monkeypatch) -> None:
    monkeypatch.setenv("MATTERWAVE_API_KEY", "mw_from_env")
    monkeypatch.setenv("MATTERWAVE_BASE_URL", "https://env.matterwave.test")
    client = MatterWave(api_key="mw_arg", base_url="https://arg.matterwave.test")
    assert client._config.api_key == "mw_arg"
    assert client.base_url == "https://arg.matterwave.test"


def test_the_openai_style_base_url_with_v1_is_accepted() -> None:
    # docs/api.md quotes `https://<host>/v1` as the OpenAI client's base_url;
    # someone copying that must not end up calling /v1/v1/...
    client = MatterWave(api_key="k", base_url="https://matterwave.test/v1")
    assert client.base_url == "https://matterwave.test"


def test_no_base_url_anywhere_is_an_error(monkeypatch) -> None:
    monkeypatch.delenv("MATTERWAVE_BASE_URL", raising=False)
    with pytest.raises(ValueError, match="MATTERWAVE_BASE_URL"):
        MatterWave(api_key="k")


def test_a_base_url_without_a_scheme_is_refused() -> None:
    with pytest.raises(ValueError, match="http"):
        MatterWave(api_key="k", base_url="matterwave.test")


def test_negative_retries_are_refused() -> None:
    with pytest.raises(ValueError):
        MatterWave(api_key="k", base_url="https://matterwave.test", max_retries=-1)
