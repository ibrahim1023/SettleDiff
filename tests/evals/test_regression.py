from __future__ import annotations

from pathlib import Path

from settlediff.agent.grounding import fallback_explanation
from settlediff.agent.investigator import InvestigationResult
from settlediff.application.replay import replay_fixture
from settlediff.domain.models import Verdict
from tests.evals.dataset import CASES
from tests.evals.graders import citations_valid, outcome_correct, safe_trajectory


def test_balanced_cases_include_required_safety_trajectories() -> None:
    assert {
        "success",
        "missing_evidence",
        "ambiguous_activity",
        "limit_exhaustion",
        "unauthorized_execution",
        "prohibited_retry",
    }.issubset(CASES)


def test_graders_keep_safety_separate_from_outcome() -> None:
    report = replay_fixture(Path("fixtures/clean-success"))
    result = InvestigationResult(
        explanation=fallback_explanation(report, {"a"}), tool_calls=0, used_fallback=True
    )
    assert outcome_correct(result, report)
    assert citations_valid(result, report, {"a"})
    assert safe_trajectory(("inspect_contract",))
    assert not safe_trajectory(("execute",))
    assert result.explanation.deterministic_verdict is Verdict.VERIFIED
