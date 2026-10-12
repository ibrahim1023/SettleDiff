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


def test_otlp_authorization_is_secret_and_requires_an_explicit_endpoint() -> None:
    token = "syn-logfire-write-token"
    settings = offline_settings(
        otlp_endpoint="https://logfire-us.pydantic.dev", otlp_authorization=token
    )
    assert settings.otlp_authorization is not None
    assert settings.otlp_authorization.get_secret_value() == token
    assert token not in repr(settings)
    with pytest.raises(ValueError, match="endpoint"):
        offline_settings(otlp_authorization=token)


@pytest.mark.parametrize("token", [" ", "syn-token\r\nX-Injected: value"])
def test_otlp_authorization_rejects_blank_and_header_injection(token: str) -> None:
    with pytest.raises(ValueError):
        offline_settings(otlp_endpoint="https://logfire-us.pydantic.dev", otlp_authorization=token)


@pytest.mark.parametrize(
    "url",
    [
        "http://rpc.example.invalid/path",
        "https://user:secret@rpc.example.invalid",
        "https://rpc.example.invalid/path?token=secret",
    ],
)
def test_perflo_rpc_requires_safe_opt_in_url_without_leaking_it(url: str) -> None:
    with pytest.raises(ValueError) as error:
        offline_settings(perflo_rpc_url=url)
    assert "secret" not in str(error.value)
    assert "token=secret" not in str(error.value)


def test_perflo_rpc_is_optional_and_hidden_in_settings() -> None:
    assert offline_settings().perflo_rpc_url is None
    settings = offline_settings(perflo_rpc_url="https://rpc.example.invalid/syn-key")
    assert settings.perflo_rpc_url is not None
    assert "syn-key" not in repr(settings)


def test_x402_configuration_is_required_only_for_selected_live_rail() -> None:
    settings = offline_settings()

    assert settings.x402_signer_command is None
    assert settings.x402_rpc_url is None
    assert not hasattr(settings, "x402_private_key")
    with pytest.raises(ValueError, match="x402 configuration is incomplete"):
        settings.require_x402()


def test_x402_configuration_loads_json_command_from_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SETTLEDIFF_X402_SIGNER_COMMAND", '["/opt/syn-x402-signer"]')
    monkeypatch.setenv("SETTLEDIFF_X402_RPC_URL", "https://rpc.example.invalid")
    monkeypatch.setenv("SETTLEDIFF_X402_TESTNET_ENABLED", "true")

    config = offline_settings().require_x402()

    assert config.signer_command == ("/opt/syn-x402-signer",)
    assert config.testnet_enabled is True


def test_x402_configuration_contains_only_non_secret_process_and_rpc_settings() -> None:
    settings = offline_settings(
        x402_signer_command=("/opt/syn-x402-signer", "--profile", "testnet"),
        x402_rpc_url="https://rpc.example.invalid/syn-rpc-key",
        x402_testnet_enabled=True,
    )

    config = settings.require_x402()
    assert config.signer_command == ("/opt/syn-x402-signer", "--profile", "testnet")
    assert config.rpc_url.get_secret_value() == "https://rpc.example.invalid/syn-rpc-key"
    assert config.testnet_enabled is True
    assert "syn-rpc-key" not in repr(config)
    assert "syn-rpc-key" not in repr(settings)


@pytest.mark.parametrize(
    "url",
    [
        "http://rpc.example.invalid",
        "ftp://rpc.example.invalid",
        "https://user:secret@rpc.example.invalid",
        "https://rpc.example.invalid/path?token=secret",
    ],
)
def test_x402_rpc_rejects_insecure_or_credentialed_urls(url: str) -> None:
    with pytest.raises(ValueError, match="x402 RPC"):
        offline_settings(
            x402_signer_command=("/opt/syn-x402-signer",), x402_rpc_url=url
        ).require_x402()


def test_invalid_x402_rpc_diagnostic_masks_credential_bearing_url() -> None:
    with pytest.raises(ValueError) as error:
        offline_settings(
            x402_signer_command=("/opt/syn-x402-signer",),
            x402_rpc_url="http://rpc.example.invalid/syn-rpc-secret",
        ).require_x402()

    assert "syn-rpc-secret" not in str(error.value)


@pytest.mark.parametrize(
    "command",
    [
        (),
        ("/opt/signer", "--private-key", "syn-secret"),
        ("/opt/signer", "--mnemonic=synthetic"),
        ("/opt/signer", "--api-key", "syn-secret"),
        ("/opt/signer", "--token=synthetic"),
        ("/opt/signer", "--client-secret", "synthetic"),
        ("/opt/signer", ""),
    ],
)
def test_x402_signer_command_rejects_empty_or_secret_bearing_arguments(
    command: tuple[str, ...],
) -> None:
    with pytest.raises(ValueError, match="x402 signer"):
        offline_settings(
            x402_signer_command=command,
            x402_rpc_url="https://rpc.example.invalid",
        ).require_x402()


def test_contextdev_base_url_can_be_replaced_for_contract_testing() -> None:
    config = offline_settings(
        contextdev_base_url="https://contextdev.example.invalid/v1",
        contextdev_api_key=SecretStr("syn-key"),
    ).require_contextdev()
    assert config.base_url == "https://contextdev.example.invalid/v1"


@pytest.mark.parametrize("missing", ["endpoint", "authorization"])
def test_live_observability_requires_endpoint_and_authorization(missing: str) -> None:
    values: dict[str, object] = {
        "otlp_endpoint": "https://otel.example.invalid",
        "otlp_authorization": SecretStr("syn-write-token"),
    }
    if missing == "endpoint":
        values = {}
    else:
        values.pop("otlp_authorization")
    with pytest.raises(ValueError, match="Observability.*required for live investigations"):
        offline_settings(**values).require_observability()


def test_live_observability_accepts_complete_configuration() -> None:
    offline_settings(
        otlp_endpoint="https://otel.example.invalid",
        otlp_authorization=SecretStr("syn-write-token"),
    ).require_observability()
