"""Explicit live-investigation state transitions."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol, cast

from pydantic import BaseModel, ConfigDict, JsonValue

from settlediff.application.auth import (
    ConsumedPaidAuthorization,
    PaidExecutionCapability,
    PaidExecutionRequest,
)
from settlediff.domain.checks import run_checks
from settlediff.domain.matching import match_activity
from settlediff.domain.models import ArtifactType, EvidenceArtifact, MachineReport, PurchaseIntent
from settlediff.domain.normalize import normalize_activity, normalize_contract, normalize_execution
from settlediff.domain.verdict import derive_verdict
from settlediff.perflo.client import PerfloMutationUncertainError
from settlediff.perflo.parser import PerfloEnvelope, PerfloSuccessEnvelope


class RunState(StrEnum):
    PREFLIGHT = "preflight"
    AUTHORIZED = "authorized"
    EXECUTING = "executing"
    EVIDENCE_RECOVERY = "evidence_recovery"
    VERIFYING = "verifying"
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
    RunState.VERIFYING: {RunState.COMPLETE, RunState.FAILED},
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


class LiveEvidenceCollector:
    """Capture provider evidence, then derive a report with deterministic code only."""

    def __init__(self, perflo: PerfloEvidencePort) -> None:
        self._perflo = perflo
        self._contract: EvidenceArtifact | None = None
        self._schema: EvidenceArtifact | None = None
        self._execution: EvidenceArtifact | None = None
        self._activity: EvidenceArtifact | None = None

    @property
    def artifacts(self) -> tuple[EvidenceArtifact, ...]:
        return tuple(
            artifact
            for artifact in (self._contract, self._schema, self._execution, self._activity)
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

    async def verify(self, request: PaidExecutionRequest) -> MachineReport:
        if self._contract is None or self._execution is None:
            raise RunTransitionError(
                "live verification requires captured contract and execution evidence"
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
        return MachineReport(
            run_id=request.run_id,
            intent=intent,
            contract=contract,
            execution=execution,
            ledger=matched.matched,
            findings=findings,
            verdict=derive_verdict(findings),
        )


class RunInvestigation:
    """Coordinate one authorized execution without embedding verification logic."""

    def __init__(
        self,
        execute_paid: Callable[[ConsumedPaidAuthorization, PaidExecutionRequest], Awaitable[None]],
        verify: Callable[[], Awaitable[MachineReport]],
        persist_event: Callable[[RunEvent], Awaitable[None]] | None = None,
    ) -> None:
        self._execute_paid = execute_paid
        self._verify = verify
        self._persist_event = persist_event

    async def execute(self, command: LiveRunCommand) -> InvestigationOutcome:
        timeline = RunTimeline()
        await self._record(timeline.events[-1])
        await self._transition(timeline, RunState.AUTHORIZED)
        authorization = await command.capability.consume(command.request)
        await self._transition(timeline, RunState.EXECUTING)
        uncertain = False
        try:
            await self._execute_paid(authorization, command.request)
        except PerfloMutationUncertainError:
            uncertain = True
            await self._transition(timeline, RunState.EVIDENCE_RECOVERY)
        await self._transition(timeline, RunState.VERIFYING)
        report = await self._verify()
        await self._transition(timeline, RunState.COMPLETE)
        return InvestigationOutcome(
            report=report,
            events=timeline.events,
            submission_uncertain=uncertain,
        )

    async def _transition(self, timeline: RunTimeline, state: RunState) -> None:
        await self._record(timeline.transition(state))

    async def _record(self, event: RunEvent) -> None:
        if self._persist_event is not None:
            await self._persist_event(event)


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
