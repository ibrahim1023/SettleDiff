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
    PerfloEvidencePort,
    RecoveryState,
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
    ContextEvidencePort,
    ContextEvidenceRequest,
    ContextEvidenceState,
)
from settlediff.domain.models import (
    ArtifactType,
    EvidenceArtifact,
    ExplanationRecord,
    ExplanationSource,
    InvestigationExplanation,
    MachineReport,
    Verdict,
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
    timeline.transition(RunState.EXPLAINING)
    timeline.transition(RunState.COMPLETE)
    assert [event.state for event in timeline.events][-4:] == [
        RunState.EVIDENCE_RECOVERY,
        RunState.VERIFYING,
        RunState.EXPLAINING,
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
    assert outcome.explanation.source is ExplanationSource.FALLBACK
    assert outcome.explanation.explanation.deterministic_verdict is report.verdict
    assert persisted == [event.state for event in outcome.events]


@pytest.mark.asyncio
async def test_uncertain_submission_without_handle_remains_unresolved() -> None:
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
    mutations = 0

    async def execute(
        _authorization: ConsumedPaidAuthorization, _request: PaidExecutionRequest
    ) -> None:
        nonlocal mutations
        mutations += 1
        raise PerfloMutationUncertainError("synthetic timeout")

    async def verify() -> MachineReport:
        return report

    async def recover(
        run_id: str, _transaction_hash: str | None
    ) -> tuple[RecoveryState, tuple[EvidenceArtifact, ...]]:
        assert run_id == request.run_id
        return (
            RecoveryState.UNRESOLVED,
            (
                EvidenceArtifact(
                    artifact_id=f"{request.run_id}:recovery",
                    artifact_type=ArtifactType.ACTIVITY,
                    source="perflo.activity",
                    collected_at=datetime.now(UTC),
                    redacted=False,
                    data={"records": 0},
                ),
            ),
        )

    outcome = await RunInvestigation(execute, verify, recover=recover).execute(
        LiveRunCommand(request, capability)
    )

    assert mutations == 1
    assert outcome.submission_uncertain
    assert outcome.recovery is not None
    assert outcome.recovery.state is RecoveryState.UNRESOLVED
    assert outcome.recovery.proof_of_non_submission is False
    assert outcome.events[-1].state is RunState.COMPLETE


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("state", "non_submission"),
    [
        (RecoveryState.SUBMITTED, False),
        (RecoveryState.NOT_SUBMITTED, True),
        (RecoveryState.UNRESOLVED, False),
    ],
)
async def test_recovery_state_distinguishes_submission_evidence(
    state: RecoveryState, non_submission: bool
) -> None:
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
    mutations = 0
    recovery_hashes: list[str | None] = []

    async def execute(
        _authorization: ConsumedPaidAuthorization, _request: PaidExecutionRequest
    ) -> None:
        nonlocal mutations
        mutations += 1
        raise PerfloMutationUncertainError("synthetic malformed response")

    async def verify() -> MachineReport:
        return report

    async def recover(
        run_id: str, transaction_hash: str | None
    ) -> tuple[RecoveryState, tuple[EvidenceArtifact, ...]]:
        assert run_id == request.run_id
        recovery_hashes.append(transaction_hash)
        return (
            state,
            (
                EvidenceArtifact(
                    artifact_id=f"{request.run_id}:recovery",
                    artifact_type=ArtifactType.PAYMENT_RECEIPT,
                    source="perflo.tx_status",
                    collected_at=datetime.now(UTC),
                    redacted=False,
                    data={"status": state.value},
                ),
            ),
        )

    outcome = await RunInvestigation(
        execute, verify, recover=recover, transaction_hash=lambda: "syn_hash_uncertain"
    ).execute(LiveRunCommand(request, capability))

    assert mutations == 1
    assert recovery_hashes == ["syn_hash_uncertain"]
    assert outcome.recovery is not None
    assert outcome.recovery.state is state
    assert outcome.recovery.proof_of_non_submission is non_submission


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status_payload", "expected"),
    [
        ({"status": "confirmed", "transaction_hash": "syn_hash"}, RecoveryState.SUBMITTED),
        ({"status": "failed", "transaction_hash": "syn_hash"}, RecoveryState.NOT_SUBMITTED),
        ({"status": "pending", "transaction_hash": "syn_hash"}, RecoveryState.UNRESOLVED),
    ],
)
async def test_collector_recovery_uses_transaction_status_without_a_second_mutation(
    status_payload: JsonValue, expected: RecoveryState
) -> None:
    class FakePerflo:
        async def transaction_status(self, transaction_hash: str) -> PerfloSuccessEnvelope:
            assert transaction_hash == "syn_hash_uncertain"
            return _envelope(status_payload)

        async def execute(self, *_args: object) -> PerfloSuccessEnvelope:
            raise AssertionError("recovery must not invoke paid execution")

    collector = LiveEvidenceCollector(
        cast(PerfloEvidencePort, FakePerflo()), cast(ContextEvidencePort, object())
    )
    state, artifacts = await collector.recover_submission("syn_run_uncertain", "syn_hash_uncertain")

    assert state is expected
    assert len(artifacts) == 1
    assert artifacts[0].source == "perflo.tx_status"
    assert collector.artifacts[-1] is artifacts[0]


@pytest.mark.asyncio
async def test_collector_recovery_uses_activity_history_without_a_handle() -> None:
    class FakePerflo:
        async def transaction_status(self, transaction_hash: str) -> PerfloSuccessEnvelope:
            raise AssertionError("no transaction handle is available")

        async def get_activity(self) -> PerfloSuccessEnvelope:
            return _envelope({"records": [{"transaction_hash": "syn_hash_uncertain"}]})

        async def execute(self, *_args: object) -> PerfloSuccessEnvelope:
            raise AssertionError("recovery must not invoke paid execution")

    collector = LiveEvidenceCollector(
        cast(PerfloEvidencePort, FakePerflo()), cast(ContextEvidencePort, object())
    )
    state, artifacts = await collector.recover_submission("syn_run_uncertain", None)

    assert state is RecoveryState.SUBMITTED
    assert len(artifacts) == 1
    assert artifacts[0].source == "perflo.activity"


@pytest.mark.asyncio
async def test_collector_empty_activity_history_does_not_prove_non_submission() -> None:
    class FakePerflo:
        async def get_activity(self) -> PerfloSuccessEnvelope:
            return _envelope({"records": []})

    collector = LiveEvidenceCollector(
        cast(PerfloEvidencePort, FakePerflo()), cast(ContextEvidencePort, object())
    )
    state, artifacts = await collector.recover_submission("syn_run_uncertain", None)

    assert state is RecoveryState.UNRESOLVED
    assert len(artifacts) == 1


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

        async def get_execution(self) -> PerfloSuccessEnvelope:
            raise AssertionError("execution status is not used for a certain submission")

        async def transaction_status(self, transaction_hash: str) -> PerfloSuccessEnvelope:
            raise AssertionError(
                f"transaction status is not used for a certain submission: {transaction_hash}"
            )

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

        async def get_execution(self) -> PerfloSuccessEnvelope:
            raise AssertionError("execution status is not used for a certain submission")

        async def transaction_status(self, transaction_hash: str) -> PerfloSuccessEnvelope:
            raise AssertionError(
                f"transaction status is not used for a certain submission: {transaction_hash}"
            )

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

        async def get_execution(self) -> PerfloSuccessEnvelope:
            raise AssertionError("execution status is not used for a certain submission")

        async def transaction_status(self, transaction_hash: str) -> PerfloSuccessEnvelope:
            raise AssertionError(
                f"transaction status is not used for a certain submission: {transaction_hash}"
            )

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


@pytest.mark.asyncio
async def test_run_explains_only_after_machine_report_is_complete() -> None:
    report = replay_fixture(Path("fixtures/clean-success"))
    report_before = report.model_dump_json()
    request = PaidExecutionRequest(
        run_id=report.run_id,
        target="https://example.invalid",
        body={},
        budget=Money(amount=Decimal("0.01"), unit="USDC"),
    )
    capability = PaidExecutionCapability.issue(
        request, expires_at=datetime.now(UTC) + timedelta(minutes=1)
    )
    artifact_ids = frozenset({"artifact:contract", "artifact:activity"})
    calls: list[str] = []

    async def execute(
        _authorization: ConsumedPaidAuthorization, _request: PaidExecutionRequest
    ) -> None:
        calls.append("execute")

    async def verify() -> MachineReport:
        calls.append("verify")
        return report

    async def explain(
        received_report: MachineReport, received_artifact_ids: frozenset[str]
    ) -> ExplanationRecord:
        calls.append("explain")
        assert received_report is report
        assert received_artifact_ids == artifact_ids
        return ExplanationRecord(
            explanation=InvestigationExplanation(
                run_id=report.run_id,
                summary="The deterministic checks verified the synthetic purchase.",
                evidence_used=("artifact:contract",),
                finding_ids=(report.findings[0].finding_id,),
                deterministic_verdict=report.verdict,
                recommended_next_step=None,
            ),
            source=ExplanationSource.PROVIDER,
            tool_calls=1,
        )

    outcome = await RunInvestigation(
        execute,
        verify,
        explain=explain,
        artifact_ids=lambda: artifact_ids,
    ).execute(LiveRunCommand(request, capability))

    assert calls == ["execute", "verify", "explain"]
    assert report.model_dump_json() == report_before
    assert outcome.report is report
    assert outcome.explanation.source is ExplanationSource.PROVIDER
    assert outcome.explanation.tool_calls == 1
    assert RunState.EXPLAINING in [event.state for event in outcome.events]


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["raise", "contradict"])
async def test_explanation_failure_returns_grounded_fallback(failure: str) -> None:
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

    async def execute(
        _authorization: ConsumedPaidAuthorization, _request: PaidExecutionRequest
    ) -> None:
        pass

    async def verify() -> MachineReport:
        return report

    async def explain(
        _received_report: MachineReport, _artifact_ids: frozenset[str]
    ) -> ExplanationRecord:
        if failure == "raise":
            raise RuntimeError("synthetic provider failure")
        return ExplanationRecord(
            explanation=InvestigationExplanation(
                run_id=report.run_id,
                summary="Contradictory provider output.",
                evidence_used=(),
                finding_ids=(),
                deterministic_verdict=Verdict.PAID_FAILURE,
                recommended_next_step=None,
            ),
            source=ExplanationSource.PROVIDER,
            tool_calls=1,
        )

    outcome = await RunInvestigation(execute, verify, explain=explain).execute(
        LiveRunCommand(request, capability)
    )

    assert outcome.report is report
    assert outcome.explanation.source is ExplanationSource.FALLBACK
    assert outcome.explanation.tool_calls == 0
    assert outcome.explanation.explanation.deterministic_verdict is report.verdict
    assert outcome.explanation.explanation.finding_ids == tuple(
        finding.finding_id for finding in report.findings
    )


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
        "settlediff.agent.explain",
    ]
    assert [name for name, _attributes in telemetry.events] == [
        "run.preflight",
        "run.authorized",
        "run.executing",
        "run.verifying",
        "run.explaining",
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
