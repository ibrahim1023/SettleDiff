from __future__ import annotations

from collections.abc import Mapping
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
    ContextDevProtocolError,
    ContextDevUnavailableError,
    ContextEvidence,
    ContextEvidenceRequest,
    ContextEvidenceState,
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

    collector = LiveEvidenceCollector(FakePerflo(), StubContextDev(evidence=CONTEXT_EVIDENCE))
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


def failing_collector(
    contextdev: StubContextDev, *, execution: JsonValue = FAILED_EXECUTION
) -> LiveEvidenceCollector:
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
            return _envelope(execution)

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
    assert artifact.redacted is True
    assert isinstance(artifact.data, dict)
    assert artifact.data["state"] == ContextEvidenceState.PRESENT
    assert artifact.data["diagnostic"] == "exact_claim_present"
    assert artifact.data["excerpt"] == "synthetic excerpt"


@pytest.mark.asyncio
async def test_collector_records_provider_unavailable_without_exception_details() -> None:
    contextdev = StubContextDev(error=ContextDevUnavailableError("secret synthetic detail"))
    collector = failing_collector(contextdev)

    report = await run_failing_collector(collector)

    assert report.verdict is Verdict.PAID_FAILURE
    assert contextdev.requests
    artifact = next(a for a in collector.artifacts if a.source == "contextdev")
    assert isinstance(artifact.data, dict)
    assert artifact.data["state"] == ContextEvidenceState.PROVIDER_UNAVAILABLE
    assert artifact.data["diagnostic"] == "provider_request_failed"
    assert artifact.data["error_class"] == "ContextDevUnavailableError"
    assert "secret synthetic detail" not in artifact.model_dump_json()


@pytest.mark.asyncio
async def test_collector_records_protocol_error_and_response_byte_count() -> None:
    contextdev = StubContextDev(
        error=ContextDevProtocolError("secret malformed detail", body_bytes=97)
    )
    collector = failing_collector(contextdev)

    await run_failing_collector(collector)

    artifact = next(a for a in collector.artifacts if a.source == "contextdev")
    assert isinstance(artifact.data, dict)
    assert artifact.data["state"] == ContextEvidenceState.PROTOCOL_ERROR
    assert artifact.data["diagnostic"] == "provider_response_invalid"
    assert artifact.data["error_class"] == "ContextDevProtocolError"
    assert artifact.data["body_bytes"] == 97
    assert "secret malformed detail" not in artifact.model_dump_json()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("evidence", "expected_state", "expected_diagnostic"),
    [
        (
            ContextEvidence(
                url="https://status.example.invalid/x",
                reachable=True,
                evidence_present=False,
                excerpt=None,
                fetched_at=datetime(2026, 8, 13, tzinfo=UTC),
                note=None,
                body_bytes=41,
            ),
            ContextEvidenceState.ABSENT,
            "exact_claim_absent",
        ),
        (
            ContextEvidence(
                url="https://status.example.invalid/x",
                reachable=False,
                evidence_present=None,
                excerpt=None,
                fetched_at=datetime(2026, 8, 13, tzinfo=UTC),
                note="source detail that must not become the diagnostic",
                body_bytes=73,
            ),
            ContextEvidenceState.SOURCE_UNREACHABLE,
            "source_scrape_failed",
        ),
    ],
)
async def test_collector_records_non_present_provider_results(
    evidence: ContextEvidence,
    expected_state: ContextEvidenceState,
    expected_diagnostic: str,
) -> None:
    collector = failing_collector(StubContextDev(evidence=evidence))

    await run_failing_collector(collector)

    artifact = next(a for a in collector.artifacts if a.source == "contextdev")
    assert isinstance(artifact.data, dict)
    assert artifact.data["state"] == expected_state
    assert artifact.data["diagnostic"] == expected_diagnostic
    assert artifact.data["body_bytes"] == evidence.body_bytes
    assert "source detail" not in artifact.model_dump_json()


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
    artifact = next(a for a in collector.artifacts if a.source == "contextdev")
    assert isinstance(artifact.data, dict)
    assert artifact.data["state"] == ContextEvidenceState.NOT_APPLICABLE
    assert artifact.data["diagnostic"] == "service_did_not_fail"


@pytest.mark.asyncio
async def test_failed_service_without_https_status_url_is_not_applicable() -> None:
    contextdev = StubContextDev(evidence=CONTEXT_EVIDENCE)
    execution = cast(dict[str, JsonValue], FAILED_EXECUTION).copy()
    execution["response_body"] = {
        "error": "synthetic outage",
        "status_url": "http://status.example.invalid/x",
    }
    collector = failing_collector(contextdev, execution=execution)

    await run_failing_collector(collector)

    assert contextdev.requests == []
    artifact = next(a for a in collector.artifacts if a.source == "contextdev")
    assert isinstance(artifact.data, dict)
    assert artifact.data["state"] == ContextEvidenceState.NOT_APPLICABLE
    assert artifact.data["diagnostic"] == "missing_eligible_https_status_url"
    assert artifact.data["error_class"] is None


@pytest.mark.asyncio
async def test_machine_report_bytes_do_not_depend_on_context_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixed_now = datetime(2026, 8, 14, tzinfo=UTC)

    class VariableDatetime(datetime):
        current = fixed_now

        @classmethod
        def now(cls, tz: object = None) -> datetime:
            del tz
            return cls.current

    class TimedStubContextDev(StubContextDev):
        def __init__(
            self,
            elapsed_seconds: int,
            *,
            evidence: ContextEvidence | None = None,
            error: Exception | None = None,
        ) -> None:
            super().__init__(evidence=evidence, error=error)
            self._elapsed_seconds = elapsed_seconds

        async def verify(self, request: ContextEvidenceRequest) -> ContextEvidence:
            VariableDatetime.current = fixed_now + timedelta(seconds=self._elapsed_seconds)
            return await super().verify(request)

    monkeypatch.setattr("settlediff.application.run.datetime", VariableDatetime)
    contexts = [
        TimedStubContextDev(1, evidence=CONTEXT_EVIDENCE),
        TimedStubContextDev(
            2,
            evidence=ContextEvidence(
                url=CONTEXT_EVIDENCE.url,
                reachable=True,
                evidence_present=False,
                excerpt=None,
                fetched_at=CONTEXT_EVIDENCE.fetched_at,
                note=None,
                body_bytes=17,
            ),
        ),
        TimedStubContextDev(
            3,
            evidence=ContextEvidence(
                url=CONTEXT_EVIDENCE.url,
                reachable=False,
                evidence_present=None,
                excerpt=None,
                fetched_at=CONTEXT_EVIDENCE.fetched_at,
                note="synthetic source failure",
                body_bytes=23,
            ),
        ),
        TimedStubContextDev(4, error=ContextDevUnavailableError("synthetic unavailable")),
        TimedStubContextDev(5, error=ContextDevProtocolError("synthetic malformed", body_bytes=29)),
    ]

    report_bytes: list[str] = []
    for contextdev in contexts:
        VariableDatetime.current = fixed_now
        report = await run_failing_collector(failing_collector(contextdev))
        report_bytes.append(report.model_dump_json())

    assert len(set(report_bytes)) == 1


class StubSpan:
    def __init__(self, names: list[str], name: str) -> None:
        self._names = names
        self._name = name

    def __enter__(self) -> None:
        self._names.append(self._name)

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: object,
    ) -> None:
        del exc_type, exc_value, traceback


class StubTelemetry:
    def __init__(self) -> None:
        self.spans: list[str] = []
        self.events: list[tuple[str, dict[str, object]]] = []
        self.counters: list[tuple[str, dict[str, object]]] = []

    def span(self, name: str, attributes: Mapping[str, object]) -> StubSpan:
        del attributes
        return StubSpan(self.spans, name)

    def event(self, name: str, attributes: Mapping[str, object]) -> None:
        self.events.append((name, dict(attributes)))

    def counter(self, name: str, attributes: Mapping[str, object]) -> None:
        self.counters.append((name, dict(attributes)))


@pytest.mark.asyncio
async def test_run_emits_safe_state_and_boundary_telemetry() -> None:
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
    telemetry = StubTelemetry()

    async def execute(
        _authorization: ConsumedPaidAuthorization, _request: PaidExecutionRequest
    ) -> None:
        pass

    async def verify() -> MachineReport:
        return report

    outcome = await RunInvestigation(execute, verify, telemetry=telemetry).execute(
        LiveRunCommand(request, capability)
    )

    assert outcome.report is report
    assert telemetry.spans == [
        "settlediff.run",
        "settlediff.perflo.execute",
        "settlediff.verify",
    ]
    assert [name for name, _attributes in telemetry.events] == [
        "run.preflight",
        "run.authorized",
        "run.executing",
        "run.verifying",
        "run.complete",
    ]
    assert all(attributes["run_id"] == report.run_id for _, attributes in telemetry.events)
    assert telemetry.counters[0] == (
        "settlediff.runs",
        {"mode": "live", "verdict": report.verdict.value},
    )
    assert telemetry.counters[1:] == [
        (
            "settlediff.checks",
            {"check_name": finding.check_id, "check_status": finding.status.value},
        )
        for finding in report.findings
    ]


@pytest.mark.asyncio
async def test_metric_failure_cannot_change_the_report() -> None:
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

    class FailingMetricTelemetry(StubTelemetry):
        def counter(self, name: str, attributes: Mapping[str, object]) -> None:
            del name, attributes
            raise RuntimeError("synthetic metric failure")

    async def execute(
        _authorization: ConsumedPaidAuthorization, _request: PaidExecutionRequest
    ) -> None:
        pass

    async def verify() -> MachineReport:
        return report

    outcome = await RunInvestigation(execute, verify, telemetry=FailingMetricTelemetry()).execute(
        LiveRunCommand(request, capability)
    )

    assert outcome.report is report
    assert outcome.report.verdict is report.verdict


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
