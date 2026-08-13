"""Explicit live-investigation state transitions."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from settlediff.application.auth import (
    ConsumedPaidAuthorization,
    PaidExecutionCapability,
    PaidExecutionRequest,
)
from settlediff.domain.models import MachineReport
from settlediff.perflo.client import PerfloMutationUncertainError


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


class RunInvestigation:
    """Coordinate one authorized execution without embedding verification logic."""

    def __init__(
        self,
        execute_paid: Callable[[ConsumedPaidAuthorization, PaidExecutionRequest], Awaitable[None]],
        verify: Callable[[], Awaitable[MachineReport]],
    ) -> None:
        self._execute_paid = execute_paid
        self._verify = verify

    async def execute(self, command: LiveRunCommand) -> InvestigationOutcome:
        timeline = RunTimeline()
        timeline.transition(RunState.AUTHORIZED)
        authorization = await command.capability.consume(command.request)
        timeline.transition(RunState.EXECUTING)
        uncertain = False
        try:
            await self._execute_paid(authorization, command.request)
        except PerfloMutationUncertainError:
            uncertain = True
            timeline.transition(RunState.EVIDENCE_RECOVERY)
        timeline.transition(RunState.VERIFYING)
        report = await self._verify()
        timeline.transition(RunState.COMPLETE)
        return InvestigationOutcome(
            report=report,
            events=timeline.events,
            submission_uncertain=uncertain,
        )
