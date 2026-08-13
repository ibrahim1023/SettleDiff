from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import cast

import pytest
from pydantic import JsonValue

from settlediff.application.auth import (
    ConsumedPaidAuthorization,
    PaidExecutionCapability,
    PaidExecutionRequest,
)
from settlediff.application.replay import replay_fixture
from settlediff.application.run import (
    LiveEvidenceCollector,
    LiveRunCommand,
    RunEvent,
    RunInvestigation,
    RunState,
    RunTimeline,
    RunTransitionError,
)
from settlediff.domain.money import Money
from settlediff.perflo.client import PerfloMutationUncertainError
from settlediff.perflo.parser import PerfloSuccessEnvelope


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
    persisted: list[RunState] = []

    async def execute(
        _authorization: ConsumedPaidAuthorization, _request: PaidExecutionRequest
    ) -> None:
        nonlocal attempts
        attempts += 1
        raise PerfloMutationUncertainError("synthetic")

    async def verify():
        return report

    async def persist(event: RunEvent) -> None:
        persisted.append(event.state)

    outcome = await RunInvestigation(execute, verify, persist).execute(
        LiveRunCommand(request, capability)
    )
    assert attempts == 1
    assert outcome.submission_uncertain
    assert outcome.events[-1].state is RunState.COMPLETE
    assert persisted == [event.state for event in outcome.events]


@pytest.mark.asyncio
async def test_live_evidence_collector_builds_a_deterministic_report() -> None:
    report = replay_fixture(Path("fixtures/clean-success"))
    request = PaidExecutionRequest(
        run_id=report.run_id,
        target=report.contract.url if report.contract else "https://example.invalid",
        body={},
        budget=Money(amount=Decimal("0.01"), unit="USDC"),
    )

    class FakePerflo:
        async def inspect_service(self, target: str) -> PerfloSuccessEnvelope:
            del target
            return _envelope(_fixture_data("contract.json"))

        async def get_schema(self, slug: str) -> PerfloSuccessEnvelope:
            del slug
            return _envelope({"request_schema": {}})

        async def execute(
            self, authorization: ConsumedPaidAuthorization, request: PaidExecutionRequest
        ) -> PerfloSuccessEnvelope:
            del authorization, request
            return _envelope(_fixture_data("execution.json"))

        async def get_activity(self) -> PerfloSuccessEnvelope:
            return _envelope(_fixture_data("activity.json"))

    collector = LiveEvidenceCollector(FakePerflo())
    await collector.preflight(request)
    authorization = await PaidExecutionCapability.issue(
        request, expires_at=datetime.now(UTC) + timedelta(minutes=1)
    ).consume(request)
    await collector.execute(authorization, request)
    collected = await collector.verify(request)

    assert collected.verdict == report.verdict
    assert collected.ledger == report.ledger
    assert {artifact.artifact_type.value for artifact in collector.artifacts} == {
        "service_contract",
        "execution",
        "activity",
        "context_evidence",
    }


def _fixture_data(filename: str) -> JsonValue:
    return cast(
        JsonValue, __import__("json").loads((Path("fixtures/clean-success") / filename).read_text())
    )


def _envelope(result: JsonValue) -> PerfloSuccessEnvelope:
    return PerfloSuccessEnvelope(
        ok=True,
        payload={"ok": True, "result": result},
        stdout_bytes=0,
        stderr_bytes=0,
        returncode=0,
    )
