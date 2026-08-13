"""Explicit live-investigation state transitions."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict


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
