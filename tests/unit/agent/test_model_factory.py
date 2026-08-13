from __future__ import annotations

import pytest
from pydantic import SecretStr, ValidationError
from pydantic_ai.models.openai import OpenAIChatModel

from settlediff.agent.model import build_hyperfusion_model
from settlediff.config import HyperfusionConfig, Settings


def test_settings_remain_optional_for_offline_use() -> None:
    settings = Settings(_env_file=None)  # pyright: ignore[reportCallIssue]

    assert settings.hyperfusion_base_url is None
    with pytest.raises(ValueError, match="Hyperfusion configuration is incomplete"):
        settings.require_hyperfusion()


def test_environment_backed_configuration_is_secret_safe(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SETTLEDIFF_HYPERFUSION_BASE_URL", "https://example.invalid/v1")
    monkeypatch.setenv("SETTLEDIFF_HYPERFUSION_API_KEY", "syn_secret_key_value")
    monkeypatch.setenv("SETTLEDIFF_HYPERFUSION_MODEL", "syn-model")

    config = Settings(_env_file=None).require_hyperfusion()  # pyright: ignore[reportCallIssue]

    assert config.api_key.get_secret_value() == "syn_secret_key_value"
    assert "syn_secret_key_value" not in repr(config)
    assert "**********" in repr(config)


@pytest.mark.parametrize("field", ["base_url", "api_key", "model_id"])
def test_hyperfusion_config_rejects_blank_required_fields(field: str) -> None:
    values = {
        "base_url": "https://example.invalid/v1",
        "api_key": SecretStr("syn_secret"),
        "model_id": "syn-model",
        field: "",
    }
    with pytest.raises(ValidationError):
        HyperfusionConfig(**values)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_factory_builds_explicit_chat_model_without_leaking_secret() -> None:
    secret = "syn_secret_factory_value"
    config = HyperfusionConfig(
        base_url="https://example.invalid/v1",
        api_key=SecretStr(secret),
        model_id="syn-model",
        timeout_seconds=12,
    )

    model = build_hyperfusion_model(config)

    assert isinstance(model, OpenAIChatModel)
    assert model.model_name == "syn-model"
    assert model.provider is not None
    assert model.provider.base_url == "https://example.invalid/v1/"
    assert model.provider.client.max_retries == 0
    assert model.provider.client.timeout == 12
    assert model.profile.supports_tools is True
    assert model.profile.default_structured_output_mode == "prompted"
    assert model.profile.openai_supports_strict_tool_definition is False
    assert secret not in repr(model)
    assert secret not in repr(model.provider)
    await model.provider.client.close()
