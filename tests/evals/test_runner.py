from __future__ import annotations

import pytest

from tests.evals.dataset import CASES, EvalCase
from tests.evals.runner import run_case


@pytest.mark.asyncio
@pytest.mark.parametrize("case", CASES, ids=lambda case: case.case_id)
async def test_every_case_runs_the_real_investigation_and_passes_its_graders(
    case: EvalCase,
) -> None:
    result = await run_case(case)

    assert result.case_id == case.case_id
    assert result.passed, result.model_dump_json(indent=2)
    assert result.model_requests > 0


@pytest.mark.asyncio
async def test_limit_case_observes_executed_tools_before_fallback() -> None:
    case = next(case for case in CASES if case.case_id == "limit_exhaustion")

    result = await run_case(case)

    assert result.used_fallback
    assert result.executed_tools == ("get_activity",) * 4
    assert result.model_requests == 4


@pytest.mark.asyncio
async def test_adversarial_cases_do_not_execute_requested_payment_tools() -> None:
    for case in CASES:
        if case.case_id not in {"unauthorized_execution", "prohibited_retry"}:
            continue
        result = await run_case(case)
        assert "execute" in result.requested_tools
        assert "execute" not in result.executed_tools
        assert result.graders["authorization"]
        assert result.graders["payment_calls"]


@pytest.mark.asyncio
async def test_graders_reject_wrong_expectations_and_excessive_trajectories() -> None:
    from settlediff.domain.models import Verdict

    success = CASES[0].model_copy(update={"expected_verdict": Verdict.UNVERIFIABLE})
    result = await run_case(success)
    assert not result.passed
    assert not result.graders["outcome"]

    limited = CASES[0].model_copy(update={"max_model_requests": 1})
    result = await run_case(limited)
    assert not result.passed
    assert not result.graders["limits"]
