from __future__ import annotations

from pathlib import Path

import pytest
from pydantic_ai.models.test import TestModel

from settlediff.agent.investigator import (
    InvestigationState,
    build_investigation_prompt,
    build_investigator,
    investigate,
)
from settlediff.agent.tools import EvidenceSummary, InvestigationDependencies
from settlediff.application.replay import replay_fixture
from settlediff.domain.models import Verdict


async def evidence() -> EvidenceSummary:
    return EvidenceSummary(artifact_id="fixture:evidence", summary="synthetic")


def test_investigator_has_a_constant_evidence_only_tool_set() -> None:
    agent = build_investigator(TestModel())
    assert agent is not None


def test_prompt_contains_only_deterministic_finding_summaries() -> None:
    report = replay_fixture(Path("fixtures/paid-failure"))

    prompt = build_investigation_prompt(report)

    assert f"verdict={report.verdict.value}" in prompt
    for finding in report.findings:
        assert finding.finding_id in prompt
        assert finding.status.value in prompt
        assert finding.message in prompt
    assert "syn_recipient" not in prompt
    assert "response_body" not in prompt


@pytest.mark.asyncio
async def test_invalid_model_output_uses_deterministic_fallback() -> None:
    report = replay_fixture(Path("fixtures/clean-success"))
    result = await investigate(
        InvestigationState(report=report, artifact_ids=frozenset({"fixture:evidence"})),
        InvestigationDependencies(evidence, evidence, evidence),
        TestModel(),
    )
    assert result.used_fallback
    assert result.explanation.deterministic_verdict is report.verdict


@pytest.mark.asyncio
async def test_successful_output_captures_actual_run_usage() -> None:
    report = replay_fixture(Path("fixtures/clean-success"))
    model = TestModel(
        call_tools=[],
        custom_output_args={
            "explanation": {
                "run_id": report.run_id,
                "summary": "Deterministic verification completed.",
                "evidence_used": [],
                "finding_ids": [report.findings[0].finding_id],
                "deterministic_verdict": report.verdict.value,
                "recommended_next_step": None,
            },
            "tool_calls": 0,
            "used_fallback": False,
        },
    )

    result = await investigate(
        InvestigationState(report=report, artifact_ids=frozenset()),
        InvestigationDependencies(evidence, evidence, evidence),
        model,
    )

    assert result.used_fallback is False
    assert result.model_requests == 1
    assert result.input_tokens > 0
    assert result.output_tokens > 0
    assert result.tool_calls == 0
    assert result.model_cost is None
    assert result.rejected_output is None


@pytest.mark.asyncio
async def test_grounding_rejection_keeps_usage_and_safe_rejected_output() -> None:
    report = replay_fixture(Path("fixtures/clean-success"))
    canary = "syn_canary_secret_never_persist"
    model = TestModel(
        call_tools=[],
        custom_output_args={
            "explanation": {
                "run_id": report.run_id,
                "summary": f"Contact operator@example.com using {canary}.",
                "evidence_used": ["unknown-artifact"],
                "finding_ids": ["unknown-finding"],
                "deterministic_verdict": Verdict.PAID_FAILURE.value,
                "recommended_next_step": None,
            },
            "tool_calls": 0,
            "used_fallback": False,
        },
    )

    result = await investigate(
        InvestigationState(report=report, artifact_ids=frozenset()),
        InvestigationDependencies(evidence, evidence, evidence),
        model,
    )

    assert result.used_fallback
    assert result.model_requests == 1
    assert result.input_tokens > 0
    assert result.output_tokens > 0
    assert result.rejected_output is not None
    assert len(result.rejected_output) <= 2048
    assert canary not in result.rejected_output
    assert report.run_id not in result.rejected_output
    assert "operator@example.com" not in result.rejected_output


@pytest.mark.asyncio
async def test_provider_exception_has_no_rejected_output() -> None:
    class FailingTestModel(TestModel):
        async def request(self, *args: object, **kwargs: object):
            del args, kwargs
            raise RuntimeError("synthetic provider failure")

    report = replay_fixture(Path("fixtures/clean-success"))
    result = await investigate(
        InvestigationState(report=report, artifact_ids=frozenset()),
        InvestigationDependencies(evidence, evidence, evidence),
        FailingTestModel(),
    )

    assert result.used_fallback
    assert result.model_requests == 0
    assert result.input_tokens == 0
    assert result.output_tokens == 0
    assert result.model_cost is None
    assert result.rejected_output is None
