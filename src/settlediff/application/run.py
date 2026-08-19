"""Explicit live-investigation state transitions."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from contextlib import AbstractContextManager, suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol, cast
from urllib.parse import urlsplit, urlunsplit

from pydantic import BaseModel, ConfigDict, JsonValue

from settlediff.agent.grounding import (
    ExplanationGroundingError,
    fallback_explanation,
    validate_explanation,
)
from settlediff.application.auth import (
    ConsumedPaidAuthorization,
    PaidExecutionCapability,
    PaidExecutionRequest,
)
from settlediff.application.budget import (
    InvestigationBudgetExceeded,
    InvestigationBudgetState,
)
from settlediff.contextdev.client import (
    ContextDevProtocolError,
    ContextDevUnavailableError,
    ContextEvidenceDiagnostic,
    ContextEvidenceErrorClass,
    ContextEvidencePort,
    ContextEvidenceRecord,
    ContextEvidenceRequest,
    ContextEvidenceState,
    eligible_evidence_url,
)
from settlediff.domain.checks import run_checks
from settlediff.domain.matching import match_activity
from settlediff.domain.models import (
    ArtifactType,
    EvidenceArtifact,
    ExecutionRecord,
    ExplanationRecord,
    ExplanationSource,
    MachineReport,
    PurchaseIntent,
)
from settlediff.domain.normalize import normalize_activity, normalize_contract, normalize_execution
from settlediff.domain.redaction import redact_artifact, redact_embedded_identifiers
from settlediff.domain.verdict import derive_verdict
from settlediff.perflo.client import PerfloMutationUncertainError
from settlediff.perflo.parser import PerfloEnvelope, PerfloSuccessEnvelope


class RecoveryState(StrEnum):
    SUBMITTED = "submitted"
    NOT_SUBMITTED = "not_submitted"
    UNRESOLVED = "unresolved"


@dataclass(frozen=True)
class SubmissionRecovery:
    state: RecoveryState
    proof_of_non_submission: bool
    evidence_ids: tuple[str, ...]


class RunState(StrEnum):
    PREFLIGHT = "preflight"
    AUTHORIZED = "authorized"
    EXECUTING = "executing"
    EVIDENCE_RECOVERY = "evidence_recovery"
    VERIFYING = "verifying"
    EXPLAINING = "explaining"
    COMPLETE = "complete"
    REFUSED = "refused"
    FAILED = "failed"


class RunTransitionError(ValueError):
    """A run attempted a transition not permitted by its current state."""


class RunEvent(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    state: RunState
    occurred_at: datetime


_ALLOWED: dict[RunState, set[RunState]] = {
    RunState.PREFLIGHT: {RunState.AUTHORIZED, RunState.REFUSED},
    RunState.AUTHORIZED: {RunState.EXECUTING, RunState.REFUSED},
    RunState.EXECUTING: {RunState.VERIFYING, RunState.EVIDENCE_RECOVERY, RunState.FAILED},
    RunState.EVIDENCE_RECOVERY: {RunState.VERIFYING, RunState.FAILED},
    RunState.VERIFYING: {RunState.EXPLAINING, RunState.FAILED},
    RunState.EXPLAINING: {RunState.COMPLETE, RunState.FAILED},
    RunState.COMPLETE: set(),
    RunState.REFUSED: set(),
    RunState.FAILED: set(),
}


class RunTimeline:
    """In-memory transition record; persistence is supplied by the Phase 11 repository."""

    def __init__(self) -> None:
        self._events = [RunEvent(state=RunState.PREFLIGHT, occurred_at=datetime.now(UTC))]

    @property
    def state(self) -> RunState:
        return self._events[-1].state

    @property
    def events(self) -> tuple[RunEvent, ...]:
        return tuple(self._events)

    def transition(self, state: RunState) -> RunEvent:
        if state not in _ALLOWED[self.state]:
            raise RunTransitionError(f"cannot transition from {self.state} to {state}")
        event = RunEvent(state=state, occurred_at=datetime.now(UTC))
        self._events.append(event)
        return event


@dataclass(frozen=True)
class LiveRunCommand:
    request: PaidExecutionRequest
    capability: PaidExecutionCapability


@dataclass(frozen=True)
class InvestigationOutcome:
    report: MachineReport
    explanation: ExplanationRecord
    recovery: SubmissionRecovery | None
    events: tuple[RunEvent, ...]
    submission_uncertain: bool


class PerfloEvidencePort(Protocol):
    """The four narrow Perflo calls used by a live investigation."""

    async def inspect_service(self, target: str) -> PerfloEnvelope: ...

    async def get_schema(self, slug: str) -> PerfloEnvelope: ...

    async def execute(
        self, authorization: ConsumedPaidAuthorization, request: PaidExecutionRequest
    ) -> PerfloEnvelope: ...

    async def get_activity(self) -> PerfloEnvelope: ...

    async def get_execution(self) -> PerfloEnvelope: ...

    async def transaction_status(self, transaction_hash: str) -> PerfloEnvelope: ...


class TelemetryPort(Protocol):
    def span(
        self, name: str, attributes: Mapping[str, object]
    ) -> AbstractContextManager[object]: ...

    def event(self, name: str, attributes: Mapping[str, object]) -> None: ...

    def counter(self, name: str, attributes: Mapping[str, object]) -> None: ...


class NullTelemetrySpan:
    def __enter__(self) -> None:
        pass

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: object,
    ) -> None:
        del exc_type, exc_value, traceback


class LiveEvidenceCollector:
    """Capture provider evidence, then derive a report with deterministic code only."""

    def __init__(
        self,
        perflo: PerfloEvidencePort,
        contextdev: ContextEvidencePort,
        budget: InvestigationBudgetState | None = None,
    ) -> None:
        self._perflo = perflo
        self._contextdev = contextdev
        self._budget = budget
        self._contract: EvidenceArtifact | None = None
        self._schema: EvidenceArtifact | None = None
        self._execution: EvidenceArtifact | None = None
        self._activity: EvidenceArtifact | None = None
        self._context: EvidenceArtifact | None = None
        self._recovery: EvidenceArtifact | None = None

    @property
    def artifacts(self) -> tuple[EvidenceArtifact, ...]:
        return tuple(
            artifact
            for artifact in (
                self._contract,
                self._schema,
                self._execution,
                self._activity,
                self._context,
                self._recovery,
            )
            if artifact is not None
        )

    async def preflight(self, request: PaidExecutionRequest) -> None:
        contract_data = _result_data(await self._perflo.inspect_service(request.target))
        self._contract = _artifact(
            request.run_id, ArtifactType.SERVICE_CONTRACT, "perflo.check", contract_data
        )
        contract = normalize_contract(self._contract)
        schema_data = _result_data(await self._perflo.get_schema(contract.vendor_slug))
        self._schema = _artifact(
            request.run_id, ArtifactType.CONTEXT_EVIDENCE, "perflo.schema", schema_data
        )

    async def execute(
        self, authorization: ConsumedPaidAuthorization, request: PaidExecutionRequest
    ) -> None:
        execution_data = _result_data(await self._perflo.execute(authorization, request))
        self._execution = _artifact(
            request.run_id, ArtifactType.EXECUTION, "perflo.fetch", execution_data
        )

    async def recover_submission(
        self, run_id: str, transaction_hash: str | None
    ) -> tuple[RecoveryState, tuple[EvidenceArtifact, ...]]:
        """Use read-only evidence to establish submission without another mutation."""
        if transaction_hash is not None:
            status_data = _result_data(await self._perflo.transaction_status(transaction_hash))
            artifact = _artifact(
                run_id, ArtifactType.PAYMENT_RECEIPT, "perflo.tx_status", status_data
            )
            self._recovery = artifact
            if isinstance(status_data, dict):
                status = cast(dict[str, JsonValue], status_data).get("status")
                if status == "confirmed":
                    return RecoveryState.SUBMITTED, (artifact,)
                if status == "failed":
                    return RecoveryState.NOT_SUBMITTED, (artifact,)
            return RecoveryState.UNRESOLVED, (artifact,)

        activity_data = _result_data(await self._perflo.get_activity())
        artifact = _artifact(run_id, ArtifactType.ACTIVITY, "perflo.activity", activity_data)
        self._recovery = artifact
        records = (
            cast(dict[str, JsonValue], activity_data).get("records")
            if isinstance(activity_data, dict)
            else None
        )
        if isinstance(records, list) and records:
            return RecoveryState.SUBMITTED, (artifact,)
        return RecoveryState.UNRESOLVED, (artifact,)

    async def verify(self, request: PaidExecutionRequest) -> MachineReport:
        if self._contract is None:
            raise RunTransitionError("live verification requires captured contract evidence")
        if self._execution is None:
            execution_data = _result_data(await self._perflo.get_execution())
            self._execution = _artifact(
                request.run_id, ArtifactType.EXECUTION, "perflo.fetch_status", execution_data
            )
        activity_data = _result_data(await self._perflo.get_activity())
        self._activity = _artifact(
            request.run_id, ArtifactType.ACTIVITY, "perflo.activity", activity_data
        )
        contract = normalize_contract(self._contract)
        execution = normalize_execution(self._execution)
        matched = match_activity(execution, normalize_activity(self._activity))
        intent = PurchaseIntent(
            run_id=request.run_id,
            task=f"Paid request to {request.target}",
            max_budget=request.budget,
            requested_service=contract.vendor_slug,
            created_at=datetime.now(UTC),
        )
        findings = run_checks(intent, contract, execution, matched)
        report = MachineReport(
            run_id=request.run_id,
            intent=intent,
            contract=contract,
            execution=execution,
            ledger=matched.matched,
            findings=findings,
            verdict=derive_verdict(findings),
        )
        await self._collect_context(request, execution)
        return report

    async def _collect_context(
        self, request: PaidExecutionRequest, execution: ExecutionRecord
    ) -> None:
        """Record Context evidence state without feeding it into deterministic checks."""
        url = eligible_evidence_url(execution)
        if url is None:
            failed = execution.upstream_http_status is not None and not (
                200 <= execution.upstream_http_status < 300
            )
            diagnostic = (
                ContextEvidenceDiagnostic.MISSING_ELIGIBLE_HTTPS_STATUS_URL
                if failed
                else ContextEvidenceDiagnostic.SERVICE_DID_NOT_FAIL
            )
            self._record_context(
                request.run_id,
                ContextEvidenceRecord(
                    state=ContextEvidenceState.NOT_APPLICABLE,
                    status_url=None,
                    excerpt=None,
                    observed_at=datetime.now(UTC),
                    diagnostic=diagnostic,
                    error_class=None,
                    body_bytes=None,
                ),
            )
            return

        safe_url = _safe_status_url(url)
        if self._budget is not None:
            try:
                await self._budget.consume_contextdev_call(run_id=request.run_id)
            except InvestigationBudgetExceeded:
                self._record_context(
                    request.run_id,
                    ContextEvidenceRecord(
                        state=ContextEvidenceState.BUDGET_EXHAUSTED,
                        status_url=safe_url,
                        excerpt=None,
                        observed_at=datetime.now(UTC),
                        diagnostic=ContextEvidenceDiagnostic.BUDGET_EXHAUSTED,
                        error_class=None,
                        body_bytes=None,
                    ),
                )
                return
        try:
            evidence = await self._contextdev.verify(
                ContextEvidenceRequest(url=url, claim=f"HTTP {execution.upstream_http_status}")
            )
        except ContextDevProtocolError as error:
            record = ContextEvidenceRecord(
                state=ContextEvidenceState.PROTOCOL_ERROR,
                status_url=safe_url,
                excerpt=None,
                observed_at=datetime.now(UTC),
                diagnostic=ContextEvidenceDiagnostic.PROVIDER_RESPONSE_INVALID,
                error_class=ContextEvidenceErrorClass.PROTOCOL,
                body_bytes=error.body_bytes,
            )
        except ContextDevUnavailableError as error:
            record = ContextEvidenceRecord(
                state=ContextEvidenceState.PROVIDER_UNAVAILABLE,
                status_url=safe_url,
                excerpt=None,
                observed_at=datetime.now(UTC),
                diagnostic=ContextEvidenceDiagnostic.PROVIDER_REQUEST_FAILED,
                error_class=ContextEvidenceErrorClass.UNAVAILABLE,
                body_bytes=error.body_bytes,
            )
        else:
            if not evidence.reachable:
                state = ContextEvidenceState.SOURCE_UNREACHABLE
                diagnostic = ContextEvidenceDiagnostic.SOURCE_SCRAPE_FAILED
            elif evidence.evidence_present:
                state = ContextEvidenceState.PRESENT
                diagnostic = ContextEvidenceDiagnostic.EXACT_CLAIM_PRESENT
            else:
                state = ContextEvidenceState.ABSENT
                diagnostic = ContextEvidenceDiagnostic.EXACT_CLAIM_ABSENT
            record = ContextEvidenceRecord(
                state=state,
                status_url=safe_url,
                excerpt=evidence.excerpt if state is ContextEvidenceState.PRESENT else None,
                observed_at=evidence.fetched_at,
                diagnostic=diagnostic,
                error_class=None,
                body_bytes=evidence.body_bytes,
            )
        self._record_context(request.run_id, record)

    def _record_context(self, run_id: str, record: ContextEvidenceRecord) -> None:
        self._context = redact_artifact(
            EvidenceArtifact(
                artifact_id=f"{run_id}:contextdev",
                artifact_type=ArtifactType.CONTEXT_EVIDENCE,
                source="contextdev",
                collected_at=record.observed_at,
                redacted=False,
                data=cast(JsonValue, record.model_dump(mode="json")),
            )
        )


class RunInvestigation:
    """Coordinate one authorized execution without embedding verification logic."""

    def __init__(
        self,
        execute_paid: Callable[[ConsumedPaidAuthorization, PaidExecutionRequest], Awaitable[None]],
        verify: Callable[[], Awaitable[MachineReport]],
        persist_event: Callable[[RunEvent], Awaitable[None]] | None = None,
        telemetry: TelemetryPort | None = None,
        explain: Callable[[MachineReport, frozenset[str]], Awaitable[ExplanationRecord]]
        | None = None,
        artifact_ids: Callable[[], frozenset[str]] | None = None,
        budget: InvestigationBudgetState | None = None,
        recover: Callable[
            [str, str | None], Awaitable[tuple[RecoveryState, tuple[EvidenceArtifact, ...]]]
        ]
        | None = None,
        transaction_hash: Callable[[], str | None] | None = None,
    ) -> None:
        self._execute_paid = execute_paid
        self._verify = verify
        self._persist_event = persist_event
        self._telemetry = telemetry
        self._explain = explain
        self._artifact_ids = artifact_ids
        self._recover = recover
        self._transaction_hash = transaction_hash
        self._budget = budget

    async def execute(self, command: LiveRunCommand) -> InvestigationOutcome:
        run_id = command.request.run_id
        with self._span("settlediff.run", {"run_id": run_id, "mode": "live"}):
            timeline = RunTimeline()
            await self._record(timeline.events[-1], run_id)
            await self._transition(timeline, RunState.AUTHORIZED, run_id)
            authorization = await command.capability.consume(command.request)
            await self._transition(timeline, RunState.EXECUTING, run_id)
            uncertain = False
            recovery: SubmissionRecovery | None = None
            try:
                with self._span(
                    "settlediff.perflo.execute", {"run_id": run_id, "component": "perflo"}
                ):
                    await self._execute_paid(authorization, command.request)
            except PerfloMutationUncertainError:
                uncertain = True
                await self._transition(timeline, RunState.EVIDENCE_RECOVERY, run_id)
                recovery = await self._recover_submission(run_id)
            await self._transition(timeline, RunState.VERIFYING, run_id)
            with self._span("settlediff.verify", {"run_id": run_id, "component": "domain"}):
                report = await self._verify()
            self._record_metrics(report)
            await self._transition(timeline, RunState.EXPLAINING, run_id)
            with self._span("settlediff.agent.explain", {"run_id": run_id, "component": "agent"}):
                explanation = await self._explain_report(report)
            await self._transition(timeline, RunState.COMPLETE, run_id)
            return InvestigationOutcome(
                report=report,
                explanation=explanation,
                recovery=recovery,
                events=timeline.events,
                submission_uncertain=uncertain,
            )

    async def _transition(self, timeline: RunTimeline, state: RunState, run_id: str) -> None:
        await self._record(timeline.transition(state), run_id)

    async def _record(self, event: RunEvent, run_id: str) -> None:
        if self._persist_event is not None:
            await self._persist_event(event)
        if self._telemetry is not None:
            self._telemetry.event(
                f"run.{event.state.value}",
                {"run_id": run_id, "component": "application", "status": event.state.value},
            )

    def _span(self, name: str, attributes: Mapping[str, object]) -> AbstractContextManager[object]:
        if self._telemetry is None:
            return NullTelemetrySpan()
        return self._telemetry.span(name, attributes)

    async def _recover_submission(self, run_id: str) -> SubmissionRecovery | None:
        if self._recover is None:
            return SubmissionRecovery(
                state=RecoveryState.UNRESOLVED,
                proof_of_non_submission=False,
                evidence_ids=(),
            )
        transaction_hash = self._transaction_hash() if self._transaction_hash is not None else None
        state, artifacts = await self._recover(run_id, transaction_hash)
        return SubmissionRecovery(
            state=RecoveryState(state),
            proof_of_non_submission=RecoveryState(state) is RecoveryState.NOT_SUBMITTED,
            evidence_ids=tuple(artifact.artifact_id for artifact in artifacts),
        )

    async def _explain_report(self, report: MachineReport) -> ExplanationRecord:
        artifact_ids: frozenset[str] = (
            self._artifact_ids() if self._artifact_ids is not None else frozenset()
        )
        fallback = ExplanationRecord(
            explanation=fallback_explanation(report, set(artifact_ids)),
            source=ExplanationSource.FALLBACK,
            tool_calls=0,
        )
        if self._explain is None:
            return fallback
        if self._budget is not None:
            try:
                await self._budget.consume_model_request(run_id=report.run_id)
            except InvestigationBudgetExceeded:
                return fallback
        try:
            record = await self._explain(report, artifact_ids)
            explanation = validate_explanation(record.explanation, report, set(artifact_ids))
            if self._budget is not None:
                for _ in range(record.tool_calls):
                    await self._budget.consume_tool_call(run_id=report.run_id)
        except (ExplanationGroundingError, RuntimeError, TimeoutError, ValueError):
            return fallback
        return record.model_copy(update={"explanation": explanation})

    def _record_metrics(self, report: MachineReport) -> None:
        if self._telemetry is None:
            return
        with suppress(Exception):
            self._telemetry.counter(
                "settlediff.runs", {"mode": "live", "verdict": report.verdict.value}
            )
            for finding in report.findings:
                self._telemetry.counter(
                    "settlediff.checks",
                    {"check_name": finding.check_id, "check_status": finding.status.value},
                )


def _safe_status_url(url: str) -> str:
    parts = urlsplit(url)
    netloc = parts.netloc.rsplit("@", maxsplit=1)[-1]
    return urlunsplit(
        (
            parts.scheme,
            netloc,
            redact_embedded_identifiers(parts.path),
            "",
            "",
        )
    )


def _artifact(
    run_id: str, artifact_type: ArtifactType, source: str, data: JsonValue
) -> EvidenceArtifact:
    return EvidenceArtifact(
        artifact_id=f"{run_id}:{artifact_type.value}",
        artifact_type=artifact_type,
        source=source,
        collected_at=datetime.now(UTC),
        redacted=False,
        data=data,
    )


def _result_data(envelope: PerfloEnvelope) -> JsonValue:
    if not isinstance(envelope, PerfloSuccessEnvelope):
        raise RunTransitionError("Perflo returned an error envelope after the adapter accepted it")
    result = envelope.payload.get("result")
    if result is None:
        raise RunTransitionError("Perflo success envelope did not include result evidence")
    return cast(JsonValue, result)
