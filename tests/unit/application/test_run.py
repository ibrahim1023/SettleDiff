from __future__ import annotations

import pytest

from settlediff.application.run import RunState, RunTimeline, RunTransitionError


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
