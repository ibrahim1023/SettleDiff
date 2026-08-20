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
    assert config.timeout_seconds == 60


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://otel.example.invalid/v1/traces",
        "http://127.0.0.1:4318",
        "http://localhost:4318",
        "http://[::1]:4318",
    ],
)
def test_otlp_endpoint_accepts_https_or_loopback_http(endpoint: str) -> None:
    assert offline_settings(otlp_endpoint=endpoint).otlp_endpoint == endpoint


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://otel.example.invalid:4318",
        "ftp://otel.example.invalid",
        "https://user:secret@otel.example.invalid",
        "https://otel.example.invalid/path?token=secret",
        "otel.example.invalid:4318",
    ],
)
def test_otlp_endpoint_rejects_insecure_or_credentialed_urls(endpoint: str) -> None:
    with pytest.raises(ValueError, match="OTLP"):
        offline_settings(otlp_endpoint=endpoint)


def test_contextdev_base_url_can_be_replaced_for_contract_testing() -> None:
    config = offline_settings(
        contextdev_base_url="https://contextdev.example.invalid/v1",
        contextdev_api_key=SecretStr("syn-key"),
    ).require_contextdev()
    assert config.base_url == "https://contextdev.example.invalid/v1"
