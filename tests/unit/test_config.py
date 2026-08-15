from __future__ import annotations

import pytest
from pydantic import SecretStr

from settlediff.config import Settings


def test_contextdev_is_absent_when_unconfigured() -> None:
    settings = Settings(_env_file=None)  # pyright: ignore[reportCallIssue]
    assert settings.contextdev() is None


def test_contextdev_requires_base_url_and_key_together() -> None:
    with pytest.raises(ValueError, match="incomplete"):
        settings = Settings(
            _env_file=None,  # pyright: ignore[reportCallIssue]
            contextdev_base_url="https://contextdev.example.invalid",
        )
        settings.contextdev()
    with pytest.raises(ValueError, match="incomplete"):
        settings = Settings(
            _env_file=None,  # pyright: ignore[reportCallIssue]
            contextdev_api_key=SecretStr("syn-key"),
        )
        settings.contextdev()


def test_contextdev_rejects_blank_values() -> None:
    with pytest.raises(ValueError, match="incomplete"):
        settings = Settings(
            _env_file=None,  # pyright: ignore[reportCallIssue]
            contextdev_base_url="  ",
            contextdev_api_key=SecretStr("syn-key"),
        )
        settings.contextdev()
    with pytest.raises(ValueError, match="must not be blank"):
        settings = Settings(
            _env_file=None,  # pyright: ignore[reportCallIssue]
            contextdev_base_url="https://contextdev.example.invalid",
            contextdev_api_key=SecretStr(" "),
        )
        settings.contextdev()


def test_contextdev_returns_a_complete_configuration() -> None:
    settings = Settings(
        _env_file=None,  # pyright: ignore[reportCallIssue]
        contextdev_base_url="https://contextdev.example.invalid/v1/evidence",
        contextdev_api_key=SecretStr("syn-key"),
    )
    config = settings.contextdev()
    assert config is not None
    assert config.base_url == "https://contextdev.example.invalid/v1/evidence"
    assert config.api_key.get_secret_value() == "syn-key"
