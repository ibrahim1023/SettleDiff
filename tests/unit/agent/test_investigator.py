from __future__ import annotations

from pathlib import Path

import pytest
from pydantic_ai.exceptions import ModelAPIError
from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.test import TestModel
from pydantic_ai.usage import RequestUsage

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
            raise ModelAPIError("synthetic-model", "synthetic provider failure")

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


@pytest.mark.asyncio
async def test_untyped_runtime_error_is_not_hidden_by_fallback() -> None:
    class BrokenTestModel(TestModel):
        async def request(self, *args: object, **kwargs: object):
            del args, kwargs
            raise RuntimeError("synthetic programming defect")

    report = replay_fixture(Path("fixtures/clean-success"))
    with pytest.raises(RuntimeError, match="programming defect"):
        await investigate(
            InvestigationState(report=report, artifact_ids=frozenset()),
            InvestigationDependencies(evidence, evidence, evidence),
            BrokenTestModel(),
        )


@pytest.mark.asyncio
async def test_limit_fallback_preserves_completed_model_and_tool_usage() -> None:
    report = replay_fixture(Path("fixtures/clean-success"))

    def looping_model(_messages: list[ModelMessage], _info: AgentInfo) -> ModelResponse:
        return ModelResponse(
            parts=[ToolCallPart("get_activity", {})],
            usage=RequestUsage(input_tokens=10, output_tokens=5),
        )

    result = await investigate(
        InvestigationState(report=report, artifact_ids=frozenset({"fixture:evidence"})),
        InvestigationDependencies(evidence, evidence, evidence),
        FunctionModel(looping_model),
    )

    assert result.used_fallback
    assert result.model_requests == 4
    assert result.tool_calls == 4
    assert result.input_tokens == 40
    assert result.output_tokens == 20


@pytest.mark.asyncio
async def test_token_overrun_returns_fallback_with_truthful_observed_usage() -> None:
    report = replay_fixture(Path("fixtures/clean-success"))

    def oversized_model(_messages: list[ModelMessage], _info: AgentInfo) -> ModelResponse:
        return ModelResponse(
            parts=[ToolCallPart("get_activity", {})],
            usage=RequestUsage(input_tokens=100_001, output_tokens=10_001),
        )

    result = await investigate(
        InvestigationState(report=report, artifact_ids=frozenset({"fixture:evidence"})),
        InvestigationDependencies(evidence, evidence, evidence),
        FunctionModel(oversized_model),
    )
    assert result.used_fallback
    assert result.model_requests == 1
    assert result.input_tokens == 100_001
    assert result.output_tokens == 10_001
    assert result.explanation.deterministic_verdict is report.verdict

    from settlediff.domain.models import ExplanationRecord, ExplanationSource

    record = ExplanationRecord(
        explanation=result.explanation,
        source=ExplanationSource.FALLBACK,
        tool_calls=result.tool_calls,
        model_requests=result.model_requests,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
    )
    assert record.input_tokens == 100_001
