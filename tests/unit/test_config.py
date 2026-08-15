from __future__ import annotations

import pytest
from pydantic import SecretStr

from settlediff.config import Settings


def offline_settings(**values: object) -> Settings:
    return Settings(_env_file=None, **values)  # pyright: ignore[reportCallIssue]


def test_contextdev_configuration_is_required_only_at_the_live_boundary() -> None:
    settings = offline_settings()
    assert settings.contextdev_api_key is None
    with pytest.raises(ValueError, match="required for live investigations"):
        settings.require_contextdev()


def test_contextdev_rejects_a_blank_key() -> None:
    with pytest.raises(ValueError, match="required for live investigations"):
        offline_settings(contextdev_api_key=SecretStr(" ")).require_contextdev()


def test_contextdev_uses_the_documented_api_by_default() -> None:
    config = offline_settings(contextdev_api_key=SecretStr("syn-key")).require_contextdev()
    assert config.base_url == "https://api.context.dev/v1"
    assert config.api_key.get_secret_value() == "syn-key"


def test_contextdev_base_url_can_be_replaced_for_contract_testing() -> None:
    config = offline_settings(
        contextdev_base_url="https://contextdev.example.invalid/v1",
        contextdev_api_key=SecretStr("syn-key"),
    ).require_contextdev()
    assert config.base_url == "https://contextdev.example.invalid/v1"
