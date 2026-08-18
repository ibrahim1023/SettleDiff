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
