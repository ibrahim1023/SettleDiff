from __future__ import annotations

from pathlib import Path

import pytest
from pydantic_ai.models.test import TestModel

from settlediff.agent.investigator import InvestigationState, build_investigator, investigate
from settlediff.agent.tools import EvidenceSummary, InvestigationDependencies
from settlediff.application.replay import replay_fixture


async def evidence() -> EvidenceSummary:
    return EvidenceSummary(artifact_id="fixture:evidence", summary="synthetic")


def test_investigator_has_a_constant_evidence_only_tool_set() -> None:
    agent = build_investigator(TestModel())
    assert agent is not None


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
