from __future__ import annotations

from pathlib import Path

import pytest

from settlediff.agent.grounding import (
    ExplanationGroundingError,
    fallback_explanation,
    validate_explanation,
)
from settlediff.application.replay import replay_fixture
from settlediff.domain.models import InvestigationExplanation, MachineReport, Verdict


@pytest.fixture
def report() -> MachineReport:
    return replay_fixture(Path("fixtures/clean-success"))


def explanation(report: MachineReport, **updates: object) -> InvestigationExplanation:
    value = InvestigationExplanation(
        run_id=report.run_id,
        summary="Verification used the cited evidence.",
        evidence_used=("artifact:one",),
        finding_ids=(report.findings[0].finding_id,),
        deterministic_verdict=report.verdict,
        recommended_next_step=None,
    )
    return value.model_copy(update=updates)


def test_validate_explanation_accepts_grounded_response(report: MachineReport) -> None:
    grounded = validate_explanation(explanation(report), report, {"artifact:one"})
    assert grounded.run_id == report.run_id


@pytest.mark.parametrize(
    "updates",
    [
        {"run_id": "other-run"},
        {"deterministic_verdict": Verdict.PAID_FAILURE},
        {"finding_ids": ("missing",)},
        {"evidence_used": ("missing",)},
        {"summary": "Contact a@b.example."},
    ],
)
def test_validate_explanation_rejects_ungrounded_response(
    report: MachineReport, updates: dict[str, object]
) -> None:
    with pytest.raises(ExplanationGroundingError):
        validate_explanation(explanation(report, **updates), report, {"artifact:one"})


def test_fallback_preserves_machine_verdict_and_known_citations(report: MachineReport) -> None:
    fallback = fallback_explanation(report, {"artifact:b", "artifact:a"})
    assert fallback.deterministic_verdict is report.verdict
    assert fallback.evidence_used == ("artifact:a", "artifact:b")
