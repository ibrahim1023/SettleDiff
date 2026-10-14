"""Explicitly gated Groq compatibility probe using only synthetic prompts and tools."""

from __future__ import annotations

import asyncio
import os

import pytest
from pydantic_ai import models

from settlediff.config import Settings
from settlediff.domain.models import Verdict
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
    ("fixture", "expected_verdict"),
    [
        ("perflo-v8-provider-only-success", Verdict.UNVERIFIABLE),
        ("x402-independent-confirmed", Verdict.VERIFIED),
        ("missing-activity", Verdict.UNVERIFIABLE),
        ("ambiguous-activity", Verdict.UNVERIFIABLE),
        ("x402-uncertain-submission", Verdict.UNVERIFIABLE),
        ("paid-failure", Verdict.PAID_FAILURE),
        ("recipient-diff", Verdict.VERIFIED_WITH_WARNINGS),
        ("x402-wrong-amount", Verdict.VERIFIED_WITH_WARNINGS),
    ],
)
@pytest.mark.parametrize("trial", range(1, 4))
async def test_groq_production_investigator_preserves_grounded_report(
    monkeypatch: pytest.MonkeyPatch,
    fixture: str,
    expected_verdict: Verdict,
    trial: int,
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
    # Space independent cases below the observed 8,000-token/minute provider bucket.
    await asyncio.sleep(20)
    monkeypatch.setattr(models, "ALLOW_MODEL_REQUESTS", True)
    settings = Settings()
    settings.require_observability()
    telemetry = configure_telemetry(settings)
    model = build_chat_model(settings.require_groq())
    try:
        root = Path(__file__).resolve().parents[2] / "fixtures" / fixture
        report = replay_fixture(root)
        assert report.verdict == expected_verdict
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
        telemetry.model_requests(result.model_requests, "groq")
        telemetry.tool_calls(result.tool_calls, "groq")
        telemetry.token_usage(result.input_tokens, result.output_tokens, "groq")
        assert 1 <= result.model_requests <= 4
        assert result.tool_calls <= 6
        assert result.input_tokens <= 8000
        assert result.output_tokens <= 1000
        assert not result.used_fallback, f"{fixture} trial {trial} fell back"
        assert result.rejected_output is None
    finally:
        assert model.provider is not None
        await model.provider.client.close()
        telemetry.shutdown()
