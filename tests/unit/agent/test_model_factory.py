from __future__ import annotations

import pytest
from pydantic import SecretStr, ValidationError
from pydantic_ai.models.groq import GroqModel

from settlediff.agent.model import build_chat_model
from settlediff.config import GroqConfig, Settings


def test_settings_remain_optional_for_offline_use() -> None:
    settings = Settings(_env_file=None)  # pyright: ignore[reportCallIssue]
    assert settings.groq_api_key is None
    assert settings.explanation_model_config() is None
    with pytest.raises(ValueError, match="Groq configuration is incomplete"):
        settings.require_groq()


def test_environment_backed_configuration_is_secret_safe(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SETTLEDIFF_GROQ_API_KEY", "syn-secret-key")
    monkeypatch.setenv("SETTLEDIFF_GROQ_MODEL", "syn-model")
    config = Settings(_env_file=None).require_groq()  # pyright: ignore[reportCallIssue]
    assert config.api_key.get_secret_value() == "syn-secret-key"
    assert "syn-secret-key" not in repr(config)


@pytest.mark.parametrize("field", ["api_key", "model_id"])
def test_groq_config_rejects_blank_required_fields(field: str) -> None:
    values = {"api_key": SecretStr("syn-key"), "model_id": "syn-model", field: ""}
    with pytest.raises(ValidationError):
        GroqConfig(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize("missing", ["key", "model"])
def test_groq_configuration_refuses_incomplete_live_setup(missing: str) -> None:
    settings = Settings(
        _env_file=None,  # pyright: ignore[reportCallIssue]
        groq_api_key=None if missing == "key" else SecretStr("syn-key"),
        groq_model=None if missing == "model" else "syn-model",
    )
    with pytest.raises(ValueError, match="Groq configuration is incomplete"):
        settings.require_groq()


@pytest.mark.asyncio
async def test_factory_builds_bounded_groq_tool_output_model() -> None:
    from settlediff.cli import _build_model_if_configured  # pyright: ignore[reportPrivateUsage]

    settings = Settings(
        _env_file=None,  # pyright: ignore[reportCallIssue]
        groq_api_key=SecretStr("syn-groq-key"),
        groq_model="openai/gpt-oss-120b",
        groq_timeout_seconds=12,
    )
    model = _build_model_if_configured(settings)
    assert isinstance(model, GroqModel)
    assert model.model_name == "openai/gpt-oss-120b"
    assert model.profile.default_structured_output_mode == "tool"
    assert model.profile.supports_tools
    assert model.settings == {"temperature": 0}
    assert model.provider is not None
    assert model.provider.base_url == "https://api.groq.com"
    assert model.provider.client.max_retries == 0
    assert model.provider.client.timeout == 12
    assert "syn-groq-key" not in repr(model)
    assert "syn-groq-key" not in repr(settings)
    await model.provider.client.close()


def test_unconfigured_model_uses_fallback() -> None:
    from settlediff.cli import _build_model_if_configured  # pyright: ignore[reportPrivateUsage]

    assert _build_model_if_configured(Settings(_env_file=None)) is None  # pyright: ignore[reportCallIssue]


@pytest.mark.asyncio
async def test_direct_factory_uses_the_official_endpoint() -> None:
    model = build_chat_model(GroqConfig(api_key=SecretStr("syn-key"), model_id="syn-model"))
    assert model.provider is not None
    assert model.provider.base_url == "https://api.groq.com"
    await model.provider.client.close()


@pytest.mark.asyncio
async def test_native_tool_error_output_is_validated_without_another_http_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import json
    from pathlib import Path
    from unittest.mock import Mock

    import httpx
    from groq import AsyncGroq
    from pydantic_ai import models

    from settlediff.agent.investigator import InvestigationState, investigate
    from settlediff.agent.tools import build_investigation_dependencies
    from settlediff.application.replay import replay_fixture

    report = replay_fixture(Path("fixtures/x402-independent-confirmed"))
    before = report.model_dump_json()
    calls = 0

    def respond(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        assert str(_request.url) == "https://api.groq.com/openai/v1/chat/completions"
        return httpx.Response(
            400,
            json={
                "error": {
                    "message": "Tool call validation failed",
                    "type": "invalid_request_error",
                    "code": "tool_use_failed",
                    "failed_generation": json.dumps(
                        {
                            "name": "final_result",
                            "arguments": {
                                "explanation": {
                                    "run_id": report.run_id,
                                    "summary": "Synthetic confirmed transfer.",
                                    "evidence_used": [],
                                    "finding_ids": [report.findings[0].finding_id],
                                    "deterministic_verdict": report.verdict.value,
                                    "recommended_next_step": None,
                                },
                                "tool_calls": 0,
                                "used_fallback": False,
                            },
                        }
                    ),
                }
            },
        )

    client = AsyncGroq(
        api_key="syn-key",
        max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(respond)),
    )
    monkeypatch.setattr("settlediff.agent.model.AsyncGroq", Mock(return_value=client))
    monkeypatch.setattr(models, "ALLOW_MODEL_REQUESTS", True)
    model = build_chat_model(GroqConfig(api_key=SecretStr("syn-key"), model_id="syn-model"))
    try:
        result = await investigate(
            InvestigationState(report, frozenset()),
            build_investigation_dependencies(report, ()),
            model,
        )
        assert not result.used_fallback
        assert result.explanation.deterministic_verdict == report.verdict
        assert report.model_dump_json() == before
        assert calls == 1
    finally:
        await client.close()
