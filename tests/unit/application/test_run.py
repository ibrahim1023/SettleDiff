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
from settlediff.contextdev.client import (
    ContextDevUnavailableError,
    ContextEvidence,
    ContextEvidenceRequest,
)
from settlediff.domain.models import ArtifactType, MachineReport, Verdict
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


class StubContextDev:
    def __init__(
        self, *, evidence: ContextEvidence | None = None, error: Exception | None = None
    ) -> None:
        self._evidence = evidence
        self._error = error
        self.requests: list[ContextEvidenceRequest] = []

    async def verify(self, request: ContextEvidenceRequest) -> ContextEvidence:
        self.requests.append(request)
        if self._error is not None:
            raise self._error
        assert self._evidence is not None
        return self._evidence


CONTEXT_EVIDENCE = ContextEvidence(
    url="https://status.example.invalid/x",
    reachable=True,
    evidence_present=True,
    excerpt="synthetic excerpt",
    fetched_at=datetime(2026, 8, 13, tzinfo=UTC),
    note=None,
)

FAILED_EXECUTION: JsonValue = {
    "vendor_slug": "synthetic-search",
    "upstream_http_status": 503,
    "charge": {"amount": "0.01", "unit": "USDC"},
    "asset": "USDC",
    "protocol": "mpp",
    "chain": "tempo",
    "recipient": "syn_recipient",
    "settlement_status": "settled",
    "transaction_id": "syn_tx_context",
    "session_id": None,
    "transaction_hash": None,
    "response_body": {
        "error": "synthetic outage",
        "status_url": "https://status.example.invalid/x",
    },
    "executed_at": "2026-08-12T00:00:00Z",
}


def failing_collector(contextdev: StubContextDev) -> LiveEvidenceCollector:
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
            return _envelope(FAILED_EXECUTION)

        async def get_activity(self) -> PerfloSuccessEnvelope:
            return _envelope(_fixture_data("activity.json"))

    return LiveEvidenceCollector(FakePerflo(), contextdev=contextdev)


async def run_failing_collector(collector: LiveEvidenceCollector) -> MachineReport:
    request = PaidExecutionRequest(
        run_id="syn_run_context",
        target="https://example.invalid/search",
        body={},
        budget=Money(amount=Decimal("0.01"), unit="USDC"),
    )
    await collector.preflight(request)
    authorization = await PaidExecutionCapability.issue(
        request, expires_at=datetime.now(UTC) + timedelta(minutes=1)
    ).consume(request)
    await collector.execute(authorization, request)
    return await collector.verify(request)


@pytest.mark.asyncio
async def test_collector_records_contextdev_evidence_for_a_failed_service() -> None:
    contextdev = StubContextDev(evidence=CONTEXT_EVIDENCE)
    collector = failing_collector(contextdev)

    report = await run_failing_collector(collector)

    assert report.verdict is Verdict.PAID_FAILURE
    assert [request.claim for request in contextdev.requests] == ["HTTP 503"]
    artifact = next(a for a in collector.artifacts if a.source == "contextdev")
    assert artifact.artifact_type is ArtifactType.CONTEXT_EVIDENCE
    assert artifact.artifact_id == "syn_run_context:contextdev"
    assert isinstance(artifact.data, dict)
    assert artifact.data["evidence_present"] is True


@pytest.mark.asyncio
async def test_collector_keeps_verifying_when_contextdev_is_unavailable() -> None:
    contextdev = StubContextDev(error=ContextDevUnavailableError("synthetic"))
    collector = failing_collector(contextdev)

    report = await run_failing_collector(collector)

    assert report.verdict is Verdict.PAID_FAILURE
    assert contextdev.requests
    assert all(artifact.source != "contextdev" for artifact in collector.artifacts)


@pytest.mark.asyncio
async def test_collector_never_calls_contextdev_for_a_successful_service() -> None:
    contextdev = StubContextDev(evidence=CONTEXT_EVIDENCE)

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

    collector = LiveEvidenceCollector(FakePerflo(), contextdev=contextdev)
    request = PaidExecutionRequest(
        run_id="syn_run_clean",
        target="https://example.invalid/search",
        body={},
        budget=Money(amount=Decimal("0.01"), unit="USDC"),
    )
    await collector.preflight(request)
    authorization = await PaidExecutionCapability.issue(
        request, expires_at=datetime.now(UTC) + timedelta(minutes=1)
    ).consume(request)
    await collector.execute(authorization, request)
    await collector.verify(request)

    assert contextdev.requests == []
    assert all(artifact.source != "contextdev" for artifact in collector.artifacts)


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
