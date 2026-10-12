from __future__ import annotations

from unittest.mock import Mock, patch

import pytest

from scripts.observability_trial import run_trial
from settlediff.config import Settings


def test_default_trial_stays_offline_despite_configured_export(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SETTLEDIFF_OTLP_ENDPOINT", "https://logfire-us.pydantic.dev")
    monkeypatch.setenv("SETTLEDIFF_OTLP_AUTHORIZATION", "syn-secret-token")
    with patch("requests.Session.post", side_effect=AssertionError("offline trial cannot export")):
        result = run_trial(export=False)
    assert result["verdict"] == "VERIFIED"
    assert result["export_requested"] is False
    assert result["spans"] == 2


def test_export_trial_uses_synthetic_data_and_explicit_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SETTLEDIFF_OTLP_ENDPOINT", "https://logfire-us.pydantic.dev")
    monkeypatch.setenv("SETTLEDIFF_OTLP_AUTHORIZATION", "syn-secret-token")
    with patch(
        "requests.Session.post",
        autospec=True,
        return_value=Mock(status_code=200, content=b"", ok=True),
    ) as post:
        result = run_trial(export=True)
    assert result["export_requested"] is True
    assert post.call_count >= 2
    for call in post.call_args_list:
        assert b"syn-secret-token" not in call.kwargs["data"]


def test_export_trial_refuses_missing_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SETTLEDIFF_OTLP_ENDPOINT", raising=False)
    monkeypatch.delenv("SETTLEDIFF_OTLP_AUTHORIZATION", raising=False)

    def isolated_settings() -> Settings:
        return Settings(_env_file=None)  # pyright: ignore[reportCallIssue]

    monkeypatch.setattr("scripts.observability_trial.Settings", isolated_settings)
    with pytest.raises(ValueError, match="SETTLEDIFF_OTLP_ENDPOINT"):
        run_trial(export=True)
