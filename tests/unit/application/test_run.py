from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from settlediff.application.auth import (
    ConsumedPaidAuthorization,
    PaidExecutionCapability,
    PaidExecutionRequest,
)
from settlediff.application.replay import replay_fixture
from settlediff.application.run import (
    LiveRunCommand,
    RunInvestigation,
    RunState,
    RunTimeline,
    RunTransitionError,
)
from settlediff.domain.money import Money
from settlediff.perflo.client import PerfloMutationUncertainError


def test_uncertain_execution_enters_evidence_only_recovery() -> None:
    timeline = RunTimeline()
    timeline.transition(RunState.AUTHORIZED)
    timeline.transition(RunState.EXECUTING)
    timeline.transition(RunState.EVIDENCE_RECOVERY)
    timeline.transition(RunState.VERIFYING)
    timeline.transition(RunState.COMPLETE)
    assert [event.state for event in timeline.events][-3:] == [
        RunState.EVIDENCE_RECOVERY,
        RunState.VERIFYING,
        RunState.COMPLETE,
    ]


def test_invalid_transition_fails_closed() -> None:
    with pytest.raises(RunTransitionError):
        RunTimeline().transition(RunState.EXECUTING)


@pytest.mark.asyncio
async def test_uncertain_execution_verifies_without_a_second_paid_attempt() -> None:
    report = replay_fixture(Path("fixtures/clean-success"))
    request = PaidExecutionRequest(
        run_id=report.run_id,
        target="https://example.invalid",
        body={},
        budget=Money(amount=Decimal("0.01"), unit="USDC"),
    )
    capability = PaidExecutionCapability.issue(
        request, expires_at=datetime.now(UTC) + timedelta(minutes=1)
    )
    attempts = 0
    persisted = []

    async def execute(
        _authorization: ConsumedPaidAuthorization, _request: PaidExecutionRequest
    ) -> None:
        nonlocal attempts
        attempts += 1
        raise PerfloMutationUncertainError("synthetic")

    async def verify():
        return report

    async def persist(event):
        persisted.append(event.state)

    outcome = await RunInvestigation(execute, verify, persist).execute(
        LiveRunCommand(request, capability)
    )
    assert attempts == 1
    assert outcome.submission_uncertain
    assert outcome.events[-1].state is RunState.COMPLETE
    assert persisted == [event.state for event in outcome.events]
