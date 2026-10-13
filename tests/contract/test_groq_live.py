"""Explicitly gated Groq compatibility probe using only synthetic prompts and tools."""

from __future__ import annotations

import os

import pytest
from pydantic_ai import models

from settlediff.config import Settings
from settlediff.telemetry.setup import configure_telemetry
from tests.contract.model_compatibility import check_chat_compatibility

pytestmark = [pytest.mark.live, pytest.mark.live_groq]


@pytest.mark.asyncio
async def test_groq_supports_structured_output_and_tool_continuation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if os.getenv("SETTLEDIFF_LIVE_GROQ") != "1":
        pytest.skip("set SETTLEDIFF_LIVE_GROQ=1 to run the live compatibility probe")
    monkeypatch.setattr(models, "ALLOW_MODEL_REQUESTS", True)
    settings = Settings()
    settings.require_observability()
    telemetry = configure_telemetry(settings)
    try:
        with telemetry.span(
            "settlediff.model.compatibility", {"component": "agent", "provider": "groq"}
        ):
            usage = await check_chat_compatibility(settings.require_groq())
            telemetry.model_requests(usage.requests, "groq")
            telemetry.tool_calls(usage.tool_calls, "groq")
            telemetry.token_usage(usage.input_tokens, usage.output_tokens, "groq")
    finally:
        telemetry.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fixture", ["perflo-v8-provider-only-success", "x402-independent-confirmed"]
)
async def test_groq_production_investigator_preserves_grounded_report(
    monkeypatch: pytest.MonkeyPatch,
    fixture: str,
) -> None:
    import json
    from datetime import UTC, datetime
    from pathlib import Path

    from settlediff.agent.investigator import InvestigationState, investigate
    from settlediff.agent.model import build_chat_model
    from settlediff.agent.tools import build_investigation_dependencies
    from settlediff.application.replay import replay_fixture
    from settlediff.domain.models import ArtifactType, EvidenceArtifact

    if os.getenv("SETTLEDIFF_LIVE_GROQ") != "1":
        pytest.skip("set SETTLEDIFF_LIVE_GROQ=1 for the synthetic production-investigator check")
    monkeypatch.setattr(models, "ALLOW_MODEL_REQUESTS", True)
    settings = Settings()
    settings.require_observability()
    telemetry = configure_telemetry(settings)
    model = build_chat_model(settings.require_groq())
    try:
        root = Path(__file__).resolve().parents[2] / "fixtures" / fixture
        report = replay_fixture(root)
        before = report.model_dump_json()
        artifacts = tuple(
            EvidenceArtifact(
                artifact_id=f"{fixture}:{filename}",
                artifact_type=kind,
                source="synthetic.fixture",
                collected_at=datetime.now(UTC),
                redacted=True,
                data=json.loads((root / filename).read_text()),
            )
            for filename, kind in (
                ("contract.json", ArtifactType.SERVICE_CONTRACT),
                ("activity.json", ArtifactType.ACTIVITY),
            )
        )
        with telemetry.span(
            "settlediff.agent.live_eval", {"component": "agent", "provider": "groq"}
        ):
            result = await investigate(
                InvestigationState(report, frozenset(a.artifact_id for a in artifacts)),
                build_investigation_dependencies(report, artifacts),
                model,
            )
        assert report.model_dump_json() == before
        assert result.explanation.deterministic_verdict == report.verdict
        assert not result.used_fallback
        assert result.rejected_output is None
    finally:
        assert model.provider is not None
        await model.provider.client.close()
        telemetry.shutdown()
