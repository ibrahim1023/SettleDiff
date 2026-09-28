from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import cast

import pytest
from pydantic import JsonValue

from settlediff.application.auth import (
    CatalogResourceReference,
    ConsumedPaidAuthorization,
    HttpResourceReference,
    PaidExecutionCapability,
    PaidExecutionRequest,
)
from settlediff.application.budget import InvestigationBudgetState
from settlediff.application.payment_rails import AdapterEvidence, SubmissionUncertainError
from settlediff.application.replay import replay_fixture
from settlediff.application.run import (
    LiveEvidenceCollector,
    LiveRunCommand,
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
    RetrySafety,
    Verdict,
)
from settlediff.domain.money import Money
from settlediff.domain.retry import (
    CONFIRMED_RECEIPT,
    PROVIDER_PAYMENT_ATTEMPT,
    RetryRunStateSnapshot,
    analyze_retry,
)
from settlediff.perflo.adapter import PerfloAdapter, PerfloClientPort
from settlediff.perflo.client import PerfloMutationUncertainError
from settlediff.perflo.parser import PerfloSuccessEnvelope


async def authorize_collector(
    collector: LiveEvidenceCollector, request: PaidExecutionRequest
) -> ConsumedPaidAuthorization:
    payment_terms = collector.payment_terms
    return await PaidExecutionCapability.issue(
        request,
        payment_terms=payment_terms,
        expires_at=datetime.now(UTC) + timedelta(minutes=1),
    ).consume(request, payment_terms=payment_terms)


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
async def test_authorization_failure_emits_refused_terminal_event() -> None:
    report = replay_fixture(Path("fixtures/clean-success"))
    request = PaidExecutionRequest(
        run_id=report.run_id,
        resource=HttpResourceReference(url="https://example.invalid", method="POST", body={}),
        budget=Money(amount=Decimal("0.01"), unit="USDC"),
    )
    capability = PaidExecutionCapability.issue(
        request, expires_at=datetime.now(UTC) - timedelta(seconds=1)
    )
    persisted: list[RunState] = []

    async def execute(
        _authorization: ConsumedPaidAuthorization, _request: PaidExecutionRequest
    ) -> None:
        raise AssertionError("refused authorization must not execute")

    async def verify() -> MachineReport:
        return report

    async def persist(event: RunEvent) -> None:
        persisted.append(event.state)

    with pytest.raises(ValueError, match="expired"):
        await RunInvestigation(execute, verify, persist).execute(
            LiveRunCommand(request, capability)
        )

    assert persisted == [RunState.PREFLIGHT, RunState.REFUSED]


@pytest.mark.asyncio
async def test_execution_failure_emits_failed_terminal_event() -> None:
    report = replay_fixture(Path("fixtures/clean-success"))
    request = PaidExecutionRequest(
        run_id=report.run_id,
        resource=HttpResourceReference(url="https://example.invalid", method="POST", body={}),
        budget=Money(amount=Decimal("0.01"), unit="USDC"),
    )
    capability = PaidExecutionCapability.issue(
        request, expires_at=datetime.now(UTC) + timedelta(minutes=1)
    )
    persisted: list[RunState] = []

    async def execute(
        _authorization: ConsumedPaidAuthorization, _request: PaidExecutionRequest
    ) -> None:
        raise RuntimeError("synthetic execution failure")

    async def verify() -> MachineReport:
        return report

    async def persist(event: RunEvent) -> None:
        persisted.append(event.state)

    with pytest.raises(RuntimeError, match="execution failure"):
        await RunInvestigation(execute, verify, persist).execute(
            LiveRunCommand(request, capability)
        )

    assert persisted == [
        RunState.PREFLIGHT,
        RunState.AUTHORIZED,
        RunState.EXECUTING,
        RunState.FAILED,
    ]


@pytest.mark.asyncio
async def test_uncertain_execution_verifies_without_a_second_paid_attempt() -> None:
    report = replay_fixture(Path("fixtures/clean-success"))
    request = PaidExecutionRequest(
        run_id=report.run_id,
        resource=HttpResourceReference(url="https://example.invalid", method="POST", body={}),
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
    assert outcome.explanation.model_requests == 0
    assert outcome.explanation.input_tokens == 0
    assert outcome.explanation.output_tokens == 0
    assert outcome.explanation.model_cost is None
    assert outcome.explanation.rejected_output is None
    assert persisted == [event.state for event in outcome.events]


@pytest.mark.asyncio
async def test_uncertain_submission_without_handle_remains_unresolved() -> None:
    report = replay_fixture(Path("fixtures/clean-success"))
    request = PaidExecutionRequest(
        run_id=report.run_id,
        resource=HttpResourceReference(url="https://example.invalid", method="POST", body={}),
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
        resource=HttpResourceReference(url="https://example.invalid", method="POST", body={}),
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
        ({"status": "failed"}, RecoveryState.SUBMITTED),
        ({"status": "success"}, RecoveryState.SUBMITTED),
        ({"status": "submitted"}, RecoveryState.UNRESOLVED),
        ({"status": "processing"}, RecoveryState.UNRESOLVED),
        ({"status": "executing"}, RecoveryState.UNRESOLVED),
    ],
)
async def test_collector_recovery_uses_transaction_status_without_a_second_mutation(
    status_payload: dict[str, JsonValue], expected: RecoveryState
) -> None:
    class FakePerflo:
        async def transaction_status(self, transaction_hash: str) -> PerfloSuccessEnvelope:
            assert transaction_hash == "syn_hash_uncertain"
            return _tx_envelope(status_payload)

        async def execute(self, *_args: object) -> PerfloSuccessEnvelope:
            raise AssertionError("recovery must not invoke paid execution")

    collector = LiveEvidenceCollector(
        PerfloAdapter(cast(PerfloClientPort, FakePerflo())), cast(ContextEvidencePort, object())
    )
    state, artifacts = await collector.recover_submission("syn_run_uncertain", "syn_hash_uncertain")

    assert state is expected
    assert len(artifacts) == 1
    assert artifacts[0].source == "perflo.tx_status"
    assert collector.artifacts[-1] is artifacts[0]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status_payload",
    [
        {"status": "confirmed"},
        {"status": "not_submitted", "proof_of_non_submission": True},
        {"status": "pending"},
        {"status": 5},
        {},
    ],
)
async def test_collector_recovery_rejects_undeclared_transaction_status(
    status_payload: dict[str, JsonValue],
) -> None:
    class FakePerflo:
        async def transaction_status(self, transaction_hash: str) -> PerfloSuccessEnvelope:
            return _tx_envelope(status_payload)

    collector = LiveEvidenceCollector(
        PerfloAdapter(cast(PerfloClientPort, FakePerflo())), cast(ContextEvidencePort, object())
    )

    from settlediff.application.payment_rails import AdapterProtocolError

    with pytest.raises(AdapterProtocolError):
        await collector.recover_submission("syn_run_uncertain", "syn_hash_uncertain")


@pytest.mark.asyncio
async def test_confirmed_recovery_receipt_forces_do_not_retry() -> None:
    class FakePerflo:
        async def transaction_status(self, transaction_hash: str) -> PerfloSuccessEnvelope:
            return _tx_envelope({"status": "success"})

        async def execute(self, *_args: object) -> PerfloSuccessEnvelope:
            raise AssertionError("recovery must not invoke paid execution")

    collector = LiveEvidenceCollector(
        PerfloAdapter(cast(PerfloClientPort, FakePerflo())), cast(ContextEvidencePort, object())
    )
    state, artifacts = await collector.recover_submission("syn_run_uncertain", "syn_hash_uncertain")

    assert state is RecoveryState.SUBMITTED
    assessment = analyze_retry(
        None,
        artifacts,
        RetryRunStateSnapshot(
            run_id="syn_run_uncertain",
            state="evidence_recovery",
            submission_uncertain=True,
        ),
    )
    assert assessment.safety is RetrySafety.DO_NOT_RETRY
    assert CONFIRMED_RECEIPT in assessment.reason_codes


@pytest.mark.asyncio
async def test_collector_uncorrelated_activity_history_cannot_prove_submission() -> None:
    class FakePerflo:
        async def transaction_status(self, transaction_hash: str) -> PerfloSuccessEnvelope:
            raise AssertionError("no transaction handle is available")

        async def get_activity(self) -> PerfloSuccessEnvelope:
            return _agent_envelope([{"transaction_hash": "syn_hash_uncertain"}])

        async def execute(self, *_args: object) -> PerfloSuccessEnvelope:
            raise AssertionError("recovery must not invoke paid execution")

    collector = LiveEvidenceCollector(
        PerfloAdapter(cast(PerfloClientPort, FakePerflo())), cast(ContextEvidencePort, object())
    )
    state, artifacts = await collector.recover_submission("syn_run_uncertain", None)

    assert state is RecoveryState.UNRESOLVED
    assert len(artifacts) == 1
    assert artifacts[0].source == "perflo.activity.agent"


@pytest.mark.asyncio
async def test_collector_current_activity_history_cannot_prove_submission_without_a_handle() -> (
    None
):
    class FakePerflo:
        async def get_activity(self) -> PerfloSuccessEnvelope:
            return PerfloSuccessEnvelope(
                ok=True,
                payload={
                    "ok": True,
                    "agent": {"rows": [{"id": "syn_transaction_uncertain"}], "meta": {}},
                },
                stdout_bytes=0,
                stderr_bytes=0,
                returncode=0,
            )

    collector = LiveEvidenceCollector(
        PerfloAdapter(cast(PerfloClientPort, FakePerflo())), cast(ContextEvidencePort, object())
    )

    state, artifacts = await collector.recover_submission("syn_run_uncertain", None)

    assert state is RecoveryState.UNRESOLVED
    assert artifacts[0].data == {"rows": [{"id": "syn_transaction_uncertain"}], "meta": {}}


@pytest.mark.asyncio
async def test_collector_empty_activity_history_does_not_prove_non_submission() -> None:
    class FakePerflo:
        async def get_activity(self) -> PerfloSuccessEnvelope:
            return _agent_envelope([])

    collector = LiveEvidenceCollector(
        PerfloAdapter(cast(PerfloClientPort, FakePerflo())), cast(ContextEvidencePort, object())
    )
    state, artifacts = await collector.recover_submission("syn_run_uncertain", None)

    assert state is RecoveryState.UNRESOLVED
    assert len(artifacts) == 1
    assert collector.artifacts == artifacts


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "request_schema",
    [{}, '{"method":"POST","body":[{"name":"query","type":"string"}]}'],
)
async def test_live_preflight_accepts_embedded_schema(request_schema: JsonValue) -> None:
    request = PaidExecutionRequest(
        run_id="syn_current_contract",
        resource=HttpResourceReference(
            url="https://example.invalid/search", method="POST", body={"query": "synthetic"}
        ),
        budget=Money(amount=Decimal("0.02"), unit="USDC"),
    )
    contract: dict[str, JsonValue] = {
        "asset": "USDC",
        "chain": "tempo",
        "found": True,
        "method": "POST",
        "priceMinor": "10000",
        "requestSchema": request_schema,
        "source": "curated",
        "url": request.target,
    }

    class FakePerflo:
        async def inspect_service(self, target: str) -> PerfloSuccessEnvelope:
            assert target == request.target
            return PerfloSuccessEnvelope(
                ok=True,
                payload={"ok": True, "vendor": contract},
                stdout_bytes=0,
                stderr_bytes=0,
                returncode=0,
            )

        async def get_schema(self, slug: str) -> PerfloSuccessEnvelope:
            raise AssertionError(f"embedded schema must avoid a second preflight call: {slug}")

    collector = LiveEvidenceCollector(
        FakeRail(FakePerflo()),
        StubContextDev(evidence=CONTEXT_EVIDENCE),
    )

    await collector.preflight(request)

    assert [artifact.source for artifact in collector.artifacts] == [
        "perflo.vendor",
        "perflo.vendor.request_schema",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "contract_fields",
    [
        {"priceMinor": "60000", "asset": "USDC"},
        {"price": {"amount": "0.01", "unit": "EUR"}, "asset": "EUR"},
    ],
    ids=["quote-over-budget", "quote-unit-mismatch"],
)
async def test_preflight_rejects_quote_outside_authorized_budget(
    contract_fields: dict[str, JsonValue],
) -> None:
    request = PaidExecutionRequest(
        run_id="syn_quote_guard",
        resource=HttpResourceReference(
            url="https://example.invalid/search", method="POST", body={}
        ),
        budget=Money(amount=Decimal("0.05"), unit="USDC"),
    )
    contract: dict[str, JsonValue] = {
        "url": request.target,
        "requestSchema": {"type": "object"},
        **contract_fields,
    }

    class FakePerflo:
        async def inspect_service(self, target: str) -> PerfloSuccessEnvelope:
            return PerfloSuccessEnvelope(
                ok=True,
                payload={"ok": True, "vendor": contract},
                stdout_bytes=0,
                stderr_bytes=0,
                returncode=0,
            )

        async def execute(self, *_args: object) -> PerfloSuccessEnvelope:
            raise AssertionError("execution must not run with a rejected quote")

    collector = LiveEvidenceCollector(FakeRail(FakePerflo()), cast(ContextEvidencePort, object()))

    with pytest.raises(RunTransitionError, match="quote"):
        await collector.preflight(request)


@pytest.mark.asyncio
async def test_execute_sends_the_preflight_quote_not_the_budget() -> None:
    request = PaidExecutionRequest(
        run_id="syn_quote_execute",
        resource=HttpResourceReference(
            url="https://example.invalid/search", method="POST", body={}
        ),
        budget=Money(amount=Decimal("0.05"), unit="USDC"),
    )
    sent: list[Money] = []

    class FakePerflo:
        async def inspect_service(self, target: str) -> PerfloSuccessEnvelope:
            return PerfloSuccessEnvelope(
                ok=True,
                payload={
                    "ok": True,
                    "vendor": {
                        "url": request.target,
                        "requestSchema": {"type": "object"},
                        "priceMinor": "10000",
                        "asset": "USDC",
                    },
                },
                stdout_bytes=0,
                stderr_bytes=0,
                returncode=0,
            )

        async def execute(
            self,
            authorization: ConsumedPaidAuthorization,
            request: PaidExecutionRequest,
            quoted_price: Money,
        ) -> PerfloSuccessEnvelope:
            del authorization, request
            sent.append(quoted_price)
            return _envelope({"upstreamResponse": {"status": 200, "body": None}})

    collector = LiveEvidenceCollector(FakeRail(FakePerflo()), cast(ContextEvidencePort, object()))
    await collector.preflight(request)
    authorization = await authorize_collector(collector, request)
    await collector.execute(authorization, request)

    assert sent == [Money(amount=Decimal("0.01"), unit="USDC")]


@pytest.mark.asyncio
async def test_preflight_requires_a_quote_before_authorization() -> None:
    request = PaidExecutionRequest(
        run_id="syn_quote_missing",
        resource=HttpResourceReference(
            url="https://example.invalid/search", method="POST", body={}
        ),
        budget=Money(amount=Decimal("0.05"), unit="USDC"),
    )

    class FakePerflo:
        async def inspect_service(self, target: str) -> PerfloSuccessEnvelope:
            return PerfloSuccessEnvelope(
                ok=True,
                payload={
                    "ok": True,
                    "vendor": {"url": request.target, "requestSchema": {"type": "object"}},
                },
                stdout_bytes=0,
                stderr_bytes=0,
                returncode=0,
            )

        async def execute(self, *_args: object) -> PerfloSuccessEnvelope:
            raise AssertionError("execution must not run without a quoted price")

    collector = LiveEvidenceCollector(FakeRail(FakePerflo()), cast(ContextEvidencePort, object()))
    with pytest.raises(RunTransitionError, match="quoted price"):
        await collector.preflight(request)


@pytest.mark.asyncio
async def test_live_evidence_collector_builds_a_deterministic_report() -> None:
    report = replay_fixture(Path("fixtures/clean-success"))
    request = PaidExecutionRequest(
        run_id=report.run_id,
        resource=HttpResourceReference(
            url=(report.contract.url if report.contract else None) or "https://example.invalid",
            method="POST",
            body={},
        ),
        budget=Money(amount=Decimal("0.01"), unit="USDC"),
    )

    class FakePerflo:
        async def inspect_service(self, target: str) -> PerfloSuccessEnvelope:
            del target
            return _vendor_envelope(_fixture_data("contract.json"))

        async def get_schema(self, slug: str) -> PerfloSuccessEnvelope:
            del slug
            return _vendor_envelope({"request_schema": {}})

        async def execute(
            self,
            authorization: ConsumedPaidAuthorization,
            request: PaidExecutionRequest,
            quoted_price: Money,
        ) -> PerfloSuccessEnvelope:
            del authorization, request, quoted_price
            return _envelope(_fixture_data("execution.json"))

        async def get_activity(self) -> PerfloSuccessEnvelope:
            return PerfloSuccessEnvelope(
                ok=True,
                payload={
                    "ok": True,
                    "agent": {
                        "rows": _fixture_data("activity.json"),
                        "meta": {"limit": 20, "offset": 0, "total": 1},
                    },
                    "money": [],
                },
                stdout_bytes=0,
                stderr_bytes=0,
                returncode=0,
            )

        async def get_execution(self) -> PerfloSuccessEnvelope:
            raise AssertionError("execution status is not used for a certain submission")

        async def transaction_status(self, transaction_hash: str) -> PerfloSuccessEnvelope:
            raise AssertionError(
                f"transaction status is not used for a certain submission: {transaction_hash}"
            )

    collector = LiveEvidenceCollector(
        FakeRail(FakePerflo()),
        StubContextDev(evidence=CONTEXT_EVIDENCE),
    )
    await collector.preflight(request)
    authorization = await authorize_collector(collector, request)
    await collector.execute(authorization, request)
    collected = await collector.verify(request)

    assert collected.verdict == report.verdict
    assert collected.ledger == report.ledger
    assert collected.retry is not None
    assert collected.retry.safety is RetrySafety.REQUIRES_HUMAN_DECISION
    assert PROVIDER_PAYMENT_ATTEMPT in collected.retry.reason_codes
    assert {artifact.artifact_type.value for artifact in collector.artifacts} == {
        "service_contract",
        "execution",
        "activity",
        "context_evidence",
    }


@pytest.mark.asyncio
async def test_live_evidence_collector_accepts_a_non_perflo_adapter() -> None:
    report = replay_fixture(Path("fixtures/clean-success"))
    request = PaidExecutionRequest(
        run_id=report.run_id,
        resource=HttpResourceReference(
            url=(report.contract.url if report.contract else None) or "https://example.invalid",
            method="POST",
            body={},
        ),
        budget=Money(amount=Decimal("0.01"), unit="USDC"),
    )
    contract_value = _fixture_data("contract.json")
    assert isinstance(contract_value, dict)
    contract = cast(
        dict[str, JsonValue],
        {**contract_value, "request_schema": {"type": "object"}},
    )

    class SyntheticRail:
        adapter_id = "synthetic"

        async def inspect(self, request: PaidExecutionRequest) -> AdapterEvidence:
            assert request.target == "https://example.invalid/search"
            return AdapterEvidence(
                adapter_id=self.adapter_id,
                protocol_version="2",
                operation="inspect",
                source="synthetic.contract",
                artifact_type=ArtifactType.SERVICE_CONTRACT,
                data=contract,
            )

        async def execute_once(
            self,
            authorization: ConsumedPaidAuthorization,
            request: PaidExecutionRequest,
            quoted_price: Money,
        ) -> AdapterEvidence:
            authorization.require_exact_request(request)
            assert quoted_price == Money(amount=Decimal("0.01"), unit="USDC")
            return AdapterEvidence(
                adapter_id=self.adapter_id,
                operation="execute",
                source="synthetic.execution",
                artifact_type=ArtifactType.EXECUTION,
                data=_fixture_data("execution.json"),
                transaction_reference="syn_hash_clean",
            )

        async def collect_activity(self) -> AdapterEvidence:
            return AdapterEvidence(
                adapter_id=self.adapter_id,
                operation="activity",
                source="synthetic.activity",
                artifact_type=ArtifactType.ACTIVITY,
                data=_fixture_data("activity.json"),
            )

    collector = LiveEvidenceCollector(SyntheticRail(), StubContextDev(evidence=CONTEXT_EVIDENCE))
    await collector.preflight(request)
    assert collector.payment_terms.adapter_id == "synthetic"
    assert collector.payment_terms.protocol_version == "2"
    assert collector.payment_terms.quoted_price == Money(amount=Decimal("0.01"), unit="USDC")
    assert collector.payment_terms.body_digest == PaidExecutionCapability.body_digest_for(
        request.body
    )
    authorization = await authorize_collector(collector, request)

    await collector.execute(authorization, request)
    collected = await collector.verify(request)

    assert collected.verdict == report.verdict
    assert collector.transaction_reference == "syn_hash_clean"
    assert [artifact.source for artifact in collector.artifacts] == [
        "synthetic.contract",
        "synthetic.contract.request_schema",
        "synthetic.execution",
        "synthetic.activity",
        "contextdev",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("adapter_id", "operation", "artifact_type", "message"),
    [
        ("other", "inspect", ArtifactType.SERVICE_CONTRACT, "identity"),
        ("synthetic", "inspect", ArtifactType.EXECUTION, "service_contract"),
        ("synthetic", "execute", ArtifactType.SERVICE_CONTRACT, "operation"),
    ],
)
async def test_collector_rejects_mislabeled_adapter_evidence(
    adapter_id: str,
    operation: str,
    artifact_type: ArtifactType,
    message: str,
) -> None:
    request = PaidExecutionRequest(
        run_id="syn_run",
        resource=HttpResourceReference(url="https://example.invalid", method="POST", body={}),
        budget=Money(amount=Decimal("0.01"), unit="USDC"),
    )

    class InvalidRail:
        adapter_id = "synthetic"

        async def inspect(self, request: PaidExecutionRequest) -> AdapterEvidence:
            return AdapterEvidence(
                adapter_id=adapter_id,
                operation=operation,
                source="synthetic.invalid",
                artifact_type=artifact_type,
                data={"url": request.target, "request_schema": {}},
            )

        async def execute_once(
            self,
            authorization: ConsumedPaidAuthorization,
            request: PaidExecutionRequest,
            quoted_price: Money,
        ) -> AdapterEvidence:
            del authorization, request, quoted_price
            raise AssertionError

        async def collect_activity(self) -> AdapterEvidence:
            raise AssertionError

    collector = LiveEvidenceCollector(InvalidRail(), cast(ContextEvidencePort, object()))

    with pytest.raises(RunTransitionError, match=message):
        await collector.preflight(request)


@pytest.mark.asyncio
async def test_collector_preserves_uncertain_execution_evidence_and_reference() -> None:
    request = PaidExecutionRequest(
        run_id="syn_uncertain_adapter",
        resource=HttpResourceReference(
            url="https://example.invalid/search", method="POST", body={}
        ),
        budget=Money(amount=Decimal("0.01"), unit="USDC"),
    )

    class UncertainRail:
        adapter_id = "synthetic"

        async def inspect(self, request: PaidExecutionRequest) -> AdapterEvidence:
            return AdapterEvidence(
                adapter_id=self.adapter_id,
                operation="inspect",
                source="synthetic.contract",
                artifact_type=ArtifactType.SERVICE_CONTRACT,
                data={
                    "url": request.target,
                    "price": {"amount": "0.01", "unit": "USDC"},
                    "asset": "USDC",
                    "request_schema": {"type": "object"},
                },
            )

        async def execute_once(
            self,
            authorization: ConsumedPaidAuthorization,
            request: PaidExecutionRequest,
            quoted_price: Money,
        ) -> AdapterEvidence:
            authorization.require_exact_request(request)
            assert quoted_price == request.budget
            return AdapterEvidence(
                adapter_id=self.adapter_id,
                operation="execute",
                source="synthetic.execution",
                artifact_type=ArtifactType.EXECUTION,
                data={"settlement_status": "unknown"},
                submission_uncertain=True,
                transaction_reference="syn_uncertain_hash",
            )

        async def collect_activity(self) -> AdapterEvidence:
            raise AssertionError

    collector = LiveEvidenceCollector(UncertainRail(), cast(ContextEvidencePort, object()))
    await collector.preflight(request)
    authorization = await authorize_collector(collector, request)

    with pytest.raises(SubmissionUncertainError):
        await collector.execute(authorization, request)

    assert collector.transaction_reference == "syn_uncertain_hash"
    assert collector.artifacts[-1].source == "synthetic.execution"


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
    contextdev: StubContextDev,
    *,
    execution: JsonValue = FAILED_EXECUTION,
    budget: InvestigationBudgetState | None = None,
) -> LiveEvidenceCollector:
    class FakePerflo:
        async def inspect_service(self, target: str) -> PerfloSuccessEnvelope:
            del target
            return _vendor_envelope(_fixture_data("contract.json"))

        async def get_schema(self, slug: str) -> PerfloSuccessEnvelope:
            del slug
            return _vendor_envelope({"request_schema": {}})

        async def execute(
            self,
            authorization: ConsumedPaidAuthorization,
            request: PaidExecutionRequest,
            quoted_price: Money,
        ) -> PerfloSuccessEnvelope:
            del authorization, request, quoted_price
            return _envelope(execution)

        async def get_activity(self) -> PerfloSuccessEnvelope:
            return _agent_envelope(_fixture_data("activity.json"))

        async def get_execution(self) -> PerfloSuccessEnvelope:
            raise AssertionError("execution status is not used for a certain submission")

        async def transaction_status(self, transaction_hash: str) -> PerfloSuccessEnvelope:
            raise AssertionError(
                f"transaction status is not used for a certain submission: {transaction_hash}"
            )

    return LiveEvidenceCollector(FakeRail(FakePerflo()), contextdev=contextdev, budget=budget)


async def run_failing_collector(collector: LiveEvidenceCollector) -> MachineReport:
    request = PaidExecutionRequest(
        run_id="syn_run_context",
        resource=HttpResourceReference(
            url="https://example.invalid/search", method="POST", body={}
        ),
        budget=Money(amount=Decimal("0.01"), unit="USDC"),
    )
    await collector.preflight(request)
    authorization = await authorize_collector(collector, request)
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
            return _vendor_envelope(_fixture_data("contract.json"))

        async def get_schema(self, slug: str) -> PerfloSuccessEnvelope:
            del slug
            return _vendor_envelope({"request_schema": {}})

        async def execute(
            self,
            authorization: ConsumedPaidAuthorization,
            request: PaidExecutionRequest,
            quoted_price: Money,
        ) -> PerfloSuccessEnvelope:
            del authorization, request, quoted_price
            return _envelope(_fixture_data("execution.json"))

        async def get_activity(self) -> PerfloSuccessEnvelope:
            return PerfloSuccessEnvelope(
                ok=True,
                payload={
                    "ok": True,
                    "agent": {
                        "rows": _fixture_data("activity.json"),
                        "meta": {"limit": 20, "offset": 0, "total": 1},
                    },
                    "money": [],
                },
                stdout_bytes=0,
                stderr_bytes=0,
                returncode=0,
            )

        async def get_execution(self) -> PerfloSuccessEnvelope:
            raise AssertionError("execution status is not used for a certain submission")

        async def transaction_status(self, transaction_hash: str) -> PerfloSuccessEnvelope:
            raise AssertionError(
                f"transaction status is not used for a certain submission: {transaction_hash}"
            )

    collector = LiveEvidenceCollector(FakeRail(FakePerflo()), contextdev=contextdev)
    request = PaidExecutionRequest(
        run_id="syn_run_clean",
        resource=HttpResourceReference(
            url="https://example.invalid/search", method="POST", body={}
        ),
        budget=Money(amount=Decimal("0.01"), unit="USDC"),
    )
    await collector.preflight(request)
    authorization = await authorize_collector(collector, request)
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
        resource=HttpResourceReference(url="https://example.invalid", method="POST", body={}),
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
@pytest.mark.parametrize("failure", ["timeout", "contradict"])
async def test_explanation_failure_returns_grounded_fallback(failure: str) -> None:
    report = replay_fixture(Path("fixtures/clean-success"))
    request = PaidExecutionRequest(
        run_id=report.run_id,
        resource=HttpResourceReference(url="https://example.invalid", method="POST", body={}),
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
        if failure == "timeout":
            raise TimeoutError("synthetic provider timeout")
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
    assert outcome.explanation.tool_calls == (0 if failure == "timeout" else 1)
    assert outcome.explanation.explanation.deterministic_verdict is report.verdict
    assert outcome.explanation.explanation.finding_ids == tuple(
        finding.finding_id for finding in report.findings
    )


@pytest.mark.asyncio
async def test_context_budget_exhaustion_records_an_explicit_evidence_state() -> None:
    from settlediff.application.budget import InvestigationBudget, InvestigationBudgetState

    budget = InvestigationBudgetState(
        InvestigationBudget.issue(
            "syn_run_context",
            contextdev_calls=0,
            model_requests=1,
            tool_calls=6,
            input_tokens=8_000,
            output_tokens=1_000,
        )
    )
    contextdev = StubContextDev(evidence=CONTEXT_EVIDENCE)
    collector = failing_collector(contextdev, budget=budget)

    report = await run_failing_collector(collector)

    assert report.verdict is Verdict.PAID_FAILURE
    assert contextdev.requests == []
    artifact = next(a for a in collector.artifacts if a.source == "contextdev")
    assert isinstance(artifact.data, dict)
    assert artifact.data["state"] == ContextEvidenceState.BUDGET_EXHAUSTED
    assert artifact.data["diagnostic"] == "budget_exhausted"


@pytest.mark.asyncio
async def test_exhausted_model_budget_returns_fallback_without_calling_the_model() -> None:
    from settlediff.application.budget import InvestigationBudget, InvestigationBudgetState

    report = replay_fixture(Path("fixtures/clean-success"))
    request = PaidExecutionRequest(
        run_id=report.run_id,
        resource=HttpResourceReference(url="https://example.invalid", method="POST", body={}),
        budget=Money(amount=Decimal("0.01"), unit="USDC"),
    )
    capability = PaidExecutionCapability.issue(
        request, expires_at=datetime.now(UTC) + timedelta(minutes=1)
    )
    budget = InvestigationBudgetState(
        InvestigationBudget.issue(
            request.run_id,
            contextdev_calls=1,
            model_requests=0,
            tool_calls=6,
            input_tokens=8_000,
            output_tokens=1_000,
        )
    )
    calls: list[str] = []

    async def execute(
        _authorization: ConsumedPaidAuthorization, _request: PaidExecutionRequest
    ) -> None:
        pass

    async def verify() -> MachineReport:
        return report

    async def explain(_report: MachineReport, _artifact_ids: frozenset[str]) -> ExplanationRecord:
        calls.append("explain")
        raise AssertionError("model must not be called when its budget is exhausted")

    outcome = await RunInvestigation(execute, verify, explain=explain, budget=budget).execute(
        LiveRunCommand(request, capability)
    )

    assert calls == []
    assert outcome.explanation.source is ExplanationSource.FALLBACK
    assert budget.remaining().model_requests == 0


@pytest.mark.asyncio
async def test_tool_calls_are_accounted_against_the_budget() -> None:
    from settlediff.application.budget import InvestigationBudget, InvestigationBudgetState

    report = replay_fixture(Path("fixtures/clean-success"))
    request = PaidExecutionRequest(
        run_id=report.run_id,
        resource=HttpResourceReference(url="https://example.invalid", method="POST", body={}),
        budget=Money(amount=Decimal("0.01"), unit="USDC"),
    )
    capability = PaidExecutionCapability.issue(
        request, expires_at=datetime.now(UTC) + timedelta(minutes=1)
    )
    budget = InvestigationBudgetState(
        InvestigationBudget.issue(
            request.run_id,
            contextdev_calls=1,
            model_requests=4,
            tool_calls=1,
            input_tokens=8_000,
            output_tokens=1_000,
        )
    )
    explanation_calls: list[str] = []

    async def execute(
        _authorization: ConsumedPaidAuthorization, _request: PaidExecutionRequest
    ) -> None:
        pass

    async def verify() -> MachineReport:
        return report

    async def explain(_report: MachineReport, _artifact_ids: frozenset[str]) -> ExplanationRecord:
        explanation_calls.append("called")
        return ExplanationRecord(
            explanation=InvestigationExplanation(
                run_id=report.run_id,
                summary="Provider explanation with three tool calls.",
                evidence_used=(),
                finding_ids=(report.findings[0].finding_id,),
                deterministic_verdict=report.verdict,
                recommended_next_step=None,
            ),
            source=ExplanationSource.PROVIDER,
            tool_calls=3,
        )

    outcome = await RunInvestigation(execute, verify, explain=explain, budget=budget).execute(
        LiveRunCommand(request, capability)
    )

    assert outcome.explanation.source is ExplanationSource.FALLBACK
    assert explanation_calls == []
    assert budget.remaining().tool_calls == 1


@pytest.mark.asyncio
async def test_token_budget_exhaustion_skips_model_without_mutating_report() -> None:
    from settlediff.application.budget import InvestigationBudget, InvestigationBudgetState

    report = replay_fixture(Path("fixtures/clean-success"))
    report_before = report.model_dump_json()
    request = PaidExecutionRequest(
        run_id=report.run_id,
        resource=HttpResourceReference(url="https://example.invalid", method="POST", body={}),
        budget=Money(amount=Decimal("0.01"), unit="USDC"),
    )
    capability = PaidExecutionCapability.issue(
        request, expires_at=datetime.now(UTC) + timedelta(minutes=1)
    )
    budget = InvestigationBudgetState(
        InvestigationBudget.issue(
            request.run_id,
            contextdev_calls=1,
            model_requests=4,
            tool_calls=6,
            input_tokens=1,
            output_tokens=1,
        )
    )
    explanation_calls: list[str] = []

    async def execute(
        _authorization: ConsumedPaidAuthorization, _request: PaidExecutionRequest
    ) -> None:
        pass

    async def verify() -> MachineReport:
        return report

    async def explain(_report: MachineReport, _artifact_ids: frozenset[str]) -> ExplanationRecord:
        explanation_calls.append("called")
        return ExplanationRecord(
            explanation=InvestigationExplanation(
                run_id=report.run_id,
                summary="Provider explanation.",
                evidence_used=(),
                finding_ids=(report.findings[0].finding_id,),
                deterministic_verdict=report.verdict,
                recommended_next_step=None,
            ),
            source=ExplanationSource.PROVIDER,
            tool_calls=0,
            model_requests=1,
            input_tokens=2,
            output_tokens=1,
            rejected_output='{"deterministic_verdict":"VERIFIED"}',
        )

    outcome = await RunInvestigation(execute, verify, explain=explain, budget=budget).execute(
        LiveRunCommand(request, capability)
    )

    assert outcome.explanation.source is ExplanationSource.FALLBACK
    assert explanation_calls == []
    assert outcome.explanation.model_requests == 0
    assert outcome.explanation.input_tokens == 0
    assert outcome.explanation.output_tokens == 0
    assert outcome.explanation.rejected_output is None
    assert report.model_dump_json() == report_before
    assert budget.remaining().input_tokens == 1
    assert budget.remaining().output_tokens == 1


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

    def histogram(self, name: str, value: object, attributes: Mapping[str, object]) -> None:
        del name, value, attributes


@pytest.mark.asyncio
async def test_run_emits_safe_state_and_boundary_telemetry() -> None:
    report = replay_fixture(Path("fixtures/clean-success"))
    request = PaidExecutionRequest(
        run_id=report.run_id,
        resource=HttpResourceReference(url="https://example.invalid", method="POST", body={}),
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
        "settlediff.authorize",
        "settlediff.payment_rail.execute",
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
        resource=HttpResourceReference(url="https://example.invalid", method="POST", body={}),
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


def _vendor_envelope(vendor: JsonValue) -> PerfloSuccessEnvelope:
    return PerfloSuccessEnvelope(
        ok=True,
        payload={"ok": True, "vendor": vendor},
        stdout_bytes=0,
        stderr_bytes=0,
        returncode=0,
    )


def _agent_envelope(rows: JsonValue, meta: JsonValue | None = None) -> PerfloSuccessEnvelope:
    return PerfloSuccessEnvelope(
        ok=True,
        payload={"ok": True, "agent": {"rows": rows, "meta": meta if meta is not None else {}}},
        stdout_bytes=0,
        stderr_bytes=0,
        returncode=0,
    )


def _tx_envelope(payload: dict[str, JsonValue]) -> PerfloSuccessEnvelope:
    return PerfloSuccessEnvelope(
        ok=True,
        payload={"ok": True, "txHash": "syn_hash_uncertain", **payload},
        stdout_bytes=0,
        stderr_bytes=0,
        returncode=0,
    )


_CATALOG_VENDOR: dict[str, JsonValue] = {
    "slug": "synthetic-weather",
    "payable": True,
    "price": {"amount": "0.01", "currency": "USD"},
    "maxChargePerCall": {"amount": "0.05", "currency": "USD"},
    "input": {"fields": [{"name": "city", "in": "body", "type": "string", "required": True}]},
}


def _catalog_request() -> PaidExecutionRequest:
    return PaidExecutionRequest(
        run_id="syn_catalog_run",
        resource=CatalogResourceReference(
            slug="synthetic-weather",
            input={"city": "synthetic-city"},
            query={},
        ),
        budget=Money(amount=Decimal("0.05"), unit="USD"),
    )


@pytest.mark.asyncio
async def test_catalog_preflight_issues_schema3_terms_and_revalidation_matches() -> None:
    request = _catalog_request()
    calls: list[str] = []

    class FakePerflo:
        async def inspect_service(self, slug: str) -> PerfloSuccessEnvelope:
            calls.append(slug)
            return _vendor_envelope(_CATALOG_VENDOR)

    collector = LiveEvidenceCollector(
        PerfloAdapter(cast(PerfloClientPort, FakePerflo())),
        cast(ContextEvidencePort, object()),
    )

    await collector.preflight(request)

    terms = collector.payment_terms
    assert terms.schema_version == 3
    assert terms.resource_digest == request.resource_digest
    assert terms.maximum_charge == request.budget
    assert terms.resource_url is None
    assert terms.method is None
    assert terms.body_digest is None

    await collector.revalidate_payment_terms(request)

    assert calls == ["synthetic-weather", "synthetic-weather"]
    artifact_ids = {artifact.artifact_id for artifact in collector.artifacts}
    assert "syn_catalog_run:service_contract" in artifact_ids
    assert "syn_catalog_run:service_contract_reinspection" in artifact_ids


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mutation", "match"),
    [
        ({"slug": "other-vendor"}, "slug|drifted"),
        ({"payable": False}, "not payable"),
        ({"price": {"amount": "0.01", "currency": "EUR"}}, "USD|drifted"),
        ({"maxChargePerCall": {"amount": "0.09", "currency": "USD"}}, "maximum|budget|drifted"),
        (
            {
                "input": {
                    "fields": [{"name": "city", "in": "query", "type": "string", "required": True}]
                }
            },
            "drifted",
        ),
    ],
    ids=["slug", "payable", "currency", "max-charge", "input-placement"],
)
async def test_catalog_revalidation_rejects_contract_drift(
    mutation: dict[str, JsonValue], match: str
) -> None:
    request = _catalog_request()
    drifted = {**_CATALOG_VENDOR, **mutation}
    calls = iter([_CATALOG_VENDOR, drifted])

    class FakePerflo:
        async def inspect_service(self, slug: str) -> PerfloSuccessEnvelope:
            del slug
            return _vendor_envelope(next(calls))

    collector = LiveEvidenceCollector(
        PerfloAdapter(cast(PerfloClientPort, FakePerflo())),
        cast(ContextEvidencePort, object()),
    )
    await collector.preflight(request)

    with pytest.raises(RunTransitionError, match=match):
        await collector.revalidate_payment_terms(request)


@pytest.mark.asyncio
async def test_catalog_revalidation_rejects_malformed_second_observation() -> None:
    request = _catalog_request()
    calls = iter([_CATALOG_VENDOR, ["not-an-object"]])

    class FakePerflo:
        async def inspect_service(self, slug: str) -> PerfloSuccessEnvelope:
            del slug
            return _vendor_envelope(cast(JsonValue, next(calls)))

    collector = LiveEvidenceCollector(
        PerfloAdapter(cast(PerfloClientPort, FakePerflo())),
        cast(ContextEvidencePort, object()),
    )
    await collector.preflight(request)

    with pytest.raises(ValueError):
        await collector.revalidate_payment_terms(request)


@pytest.mark.asyncio
async def test_http_request_skips_reinspection_even_when_adapter_supports_it() -> None:
    request = PaidExecutionRequest(
        run_id="syn_http_run",
        resource=HttpResourceReference(
            url="https://example.invalid/search", method="POST", body={}
        ),
        budget=Money(amount=Decimal("0.02"), unit="USDC"),
    )
    calls: list[str] = []

    class FakePerflo:
        async def inspect_service(self, target: str) -> PerfloSuccessEnvelope:
            calls.append(target)
            return _vendor_envelope(
                {
                    "url": request.target,
                    "price": {"amount": "0.01", "unit": "USDC"},
                    "asset": "USDC",
                    "requestSchema": {"type": "object"},
                }
            )

    collector = LiveEvidenceCollector(
        PerfloAdapter(cast(PerfloClientPort, FakePerflo())),
        cast(ContextEvidencePort, object()),
    )

    await collector.revalidate_payment_terms(request)

    assert calls == []


def test_x402_adapter_does_not_implement_reinspection() -> None:
    from settlediff.application.payment_rails import ContractReinspectionPort
    from settlediff.x402.adapter import X402Adapter

    assert not isinstance(X402Adapter, ContractReinspectionPort)


class FakeRail:
    """Collector-test rail that unwraps fake Perflo envelopes without v8 guards."""

    adapter_id = "perflo"

    def __init__(self, client: object) -> None:
        self._client = cast(PerfloClientPort, client)

    async def inspect(self, request: PaidExecutionRequest) -> AdapterEvidence:
        envelope = await self._client.inspect_service(request.target)
        return AdapterEvidence(
            adapter_id="perflo",
            operation="inspect",
            source="perflo.vendor",
            artifact_type=ArtifactType.SERVICE_CONTRACT,
            data=envelope.payload["vendor"],
        )

    async def collect_schema(self, slug: str) -> AdapterEvidence:
        envelope = await self._client.get_schema(slug)
        return AdapterEvidence(
            adapter_id="perflo",
            operation="schema",
            source="perflo.schema",
            artifact_type=ArtifactType.CONTEXT_EVIDENCE,
            data=envelope.payload["vendor"],
        )

    async def execute_once(
        self,
        authorization: ConsumedPaidAuthorization,
        request: PaidExecutionRequest,
        quoted_price: Money,
    ) -> AdapterEvidence:
        envelope = await self._client.execute(authorization, request, quoted_price)
        return AdapterEvidence(
            adapter_id="perflo",
            operation="execute",
            source="perflo.pay",
            artifact_type=ArtifactType.EXECUTION,
            data=envelope.payload["result"],
        )

    async def collect_activity(self) -> AdapterEvidence:
        envelope = await self._client.get_activity()
        return AdapterEvidence(
            adapter_id="perflo",
            operation="activity",
            source="perflo.activity.agent",
            artifact_type=ArtifactType.ACTIVITY,
            data=envelope.payload["agent"],
        )

    async def collect_transaction(self, transaction_reference: str) -> AdapterEvidence:
        envelope = await self._client.transaction_status(transaction_reference)
        return AdapterEvidence(
            adapter_id="perflo",
            operation="transaction_status",
            source="perflo.tx_status",
            artifact_type=ArtifactType.PAYMENT_RECEIPT,
            data={key: value for key, value in envelope.payload.items() if key != "ok"},
            transaction_reference=transaction_reference,
        )
