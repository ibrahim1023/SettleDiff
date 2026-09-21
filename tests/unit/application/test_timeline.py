from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest
from pydantic import JsonValue, ValidationError

from settlediff.application.replay import replay_fixture
from settlediff.application.run import RunEvent, RunState
from settlediff.application.timeline import (
    EvidenceTimelineEvent,
    build_evidence_timeline,
)
from settlediff.domain.models import (
    ArtifactType,
    DeliveryAssessment,
    DeliveryObservation,
    DeliveryStatus,
    EvidenceArtifact,
)

NOW = datetime(2026, 9, 1, tzinfo=UTC)
EARLIER = datetime(2026, 8, 31, 23, 59, tzinfo=UTC)


def _report():
    return replay_fixture(Path("fixtures/x402-clean-success"))


def _artifact(
    artifact_id: str,
    artifact_type: ArtifactType,
    *,
    collected_at: datetime,
    source: str = "synthetic.source",
) -> EvidenceArtifact:
    return EvidenceArtifact(
        artifact_id=artifact_id,
        artifact_type=artifact_type,
        source=source,
        collected_at=collected_at,
        redacted=True,
        data={},
    )


def _delivery(observation: DeliveryObservation | None = None) -> DeliveryAssessment:
    if observation is None:
        observation = DeliveryObservation(
            observed_at=NOW,
            status_code=200,
            media_type="application/json",
            received_bytes=21,
            truncated=False,
            parsed_body={"result": "synthetic"},
            evidence_ids=("syn_run:execution",),
        )
    return DeliveryAssessment(
        status=DeliveryStatus.SATISFIED,
        reason_code="DELIVERY_SATISFIED",
        evidence_ids=("syn_run:contract", "syn_run:execution"),
        observation=observation,
        response_contract_digest="a" * 64,
    )


def test_timeline_builds_intent_run_artifact_events_in_partial_order() -> None:
    report = _report()
    events = (
        RunEvent(state=RunState.PREFLIGHT, occurred_at=EARLIER),
        RunEvent(state=RunState.AUTHORIZED, occurred_at=NOW),
    )
    contract = _artifact("syn_run:contract", ArtifactType.SERVICE_CONTRACT, collected_at=NOW)
    execution = _artifact("syn_run:execution", ArtifactType.EXECUTION, collected_at=NOW)
    activity = _artifact("syn_run:activity", ArtifactType.ACTIVITY, collected_at=NOW)

    timeline = build_evidence_timeline(report, events, (contract, execution, activity))

    assert tuple(event.sequence for event in timeline) == tuple(range(len(timeline)))
    intent = timeline[0]
    assert intent.source == "settlediff.intent"
    assert intent.attributes == {"event": "intent_recorded"}
    assert intent.source_time == report.intent.created_at
    states = [event for event in timeline if event.source == "settlediff.run_state"]
    assert [event.attributes["state"] for event in states] == ["preflight", "authorized"]
    assert all(event.attributes["event"] == "run_state" for event in states)
    artifact_events = {
        event.artifact_ids[0]: event
        for event in timeline
        if event.attributes.get("event") == "artifact_observed"
    }
    assert set(artifact_events) == {
        "syn_run:contract",
        "syn_run:execution",
        "syn_run:activity",
    }
    assert report.execution is not None
    assert report.ledger is not None
    assert artifact_events["syn_run:execution"].source_time == report.execution.executed_at
    assert artifact_events["syn_run:activity"].source_time == report.ledger.occurred_at
    assert artifact_events["syn_run:contract"].source_time is None
    assert artifact_events["syn_run:execution"].attributes["artifact_type"] == "execution"
    assert artifact_events["syn_run:execution"].attributes["redacted"] is True


def test_artifact_input_order_does_not_change_timeline() -> None:
    report = _report()
    artifacts = (
        _artifact("syn_run:b", ArtifactType.EXECUTION, collected_at=NOW),
        _artifact("syn_run:a", ArtifactType.SERVICE_CONTRACT, collected_at=EARLIER),
    )

    forward = build_evidence_timeline(report, (), artifacts)
    reversed_order = build_evidence_timeline(report, (), artifacts[::-1])

    assert forward == reversed_order


def test_findings_attach_to_exact_ids_and_canonical_aliases() -> None:
    report = _report()
    execution = _artifact("syn_run:execution", ArtifactType.EXECUTION, collected_at=NOW)

    timeline = build_evidence_timeline(report, (), (execution,))

    event = next(
        event for event in timeline if event.attributes.get("event") == "artifact_observed"
    )
    expected = {
        finding.finding_id
        for finding in report.findings
        if {"syn_run:execution", "execution"} & set(finding.artifact_ids)
    }
    assert expected
    assert set(event.finding_ids) == expected


def test_delivery_observation_event_carries_metadata_without_body_or_digests() -> None:
    report = _report()
    execution_artifact = _artifact(
        "syn_run:execution", ArtifactType.EXECUTION, collected_at=EARLIER
    )
    report = report.model_copy(update={"schema_version": 3, "delivery": _delivery()})
    events = (RunEvent(state=RunState.PREFLIGHT, occurred_at=EARLIER),)

    timeline = build_evidence_timeline(report, events, (execution_artifact,))

    delivery_event = next(
        event for event in timeline if event.attributes.get("event") == "delivery_observed"
    )
    assert delivery_event.source == "synthetic.source.paid_response"
    assert delivery_event.source_time is None
    assert delivery_event.observed_at == NOW
    assert delivery_event.artifact_ids == ("syn_run:contract", "syn_run:execution")
    assert delivery_event.attributes == {
        "event": "delivery_observed",
        "status_code": 200,
        "media_type": "application/json",
        "received_bytes": 21,
        "truncated": False,
        "delivery_status": "SATISFIED",
        "reason_code": "DELIVERY_SATISFIED",
    }
    serialized = json.dumps(delivery_event.model_dump(mode="json"))
    assert '"result"' not in serialized
    assert "a" * 32 not in serialized
    assert "parsed_body" not in serialized
    assert "digest" not in serialized
    assert "REQUEST_TRANSMITTED" not in json.dumps(
        [event.model_dump(mode="json") for event in timeline]
    )


def test_uncited_delivery_observation_uses_neutral_source() -> None:
    report = _report()
    observation = DeliveryObservation(
        observed_at=NOW,
        status_code=500,
        media_type=None,
        received_bytes=9,
        truncated=False,
        parsed_body=None,
        evidence_ids=("elsewhere:execution",),
    )
    report = report.model_copy(update={"schema_version": 3, "delivery": _delivery(observation)})

    timeline = build_evidence_timeline(report, (), ())

    delivery_event = next(
        event for event in timeline if event.attributes.get("event") == "delivery_observed"
    )
    assert delivery_event.source == "settlediff.delivery"
    assert "media_type" not in delivery_event.attributes


def test_missing_source_times_remain_none_and_do_not_invent_semantics() -> None:
    report = _report()
    artifact = _artifact("syn_run:schema", ArtifactType.CONTEXT_EVIDENCE, collected_at=NOW)

    timeline = build_evidence_timeline(report, (), (artifact,))

    event = next(
        event for event in timeline if event.attributes.get("event") == "artifact_observed"
    )
    assert event.source_time is None
    assert "REQUEST_TRANSMITTED" not in json.dumps(
        [entry.model_dump(mode="json") for entry in timeline]
    )


@pytest.mark.parametrize(
    "overrides",
    [
        {"observed_at": datetime(2026, 9, 1)},
        {"source_time": datetime(2026, 9, 1)},
        {"attributes": {"event": {"nested": True}}},
        {"attributes": {"event": "x" * 257}},
        {"attributes": {"a" * 65: "value"}},
        {"artifact_ids": tuple(f"id_{index}" for index in range(17))},
        {"source": "x" * 256},
    ],
)
def test_timeline_event_rejects_unbounded_or_ambiguous_fields(
    overrides: dict[str, object],
) -> None:
    values: dict[str, object] = {
        "sequence": 0,
        "source_time": None,
        "observed_at": NOW,
        "source": "synthetic.source",
        "artifact_ids": (),
        "finding_ids": (),
        "attributes": {"event": "synthetic"},
    }
    values.update(overrides)
    with pytest.raises(ValidationError):
        EvidenceTimelineEvent.model_validate(values)


def test_timeline_event_json_round_trips_strictly() -> None:
    report = _report()
    timeline = build_evidence_timeline(report, (), ())

    for event in timeline:
        assert (
            EvidenceTimelineEvent.model_validate_json(event.model_dump_json(), strict=True) == event
        )


def test_overlong_or_empty_source_fails_closed() -> None:
    report = _report()
    artifact = _artifact(
        "syn_run:execution",
        ArtifactType.EXECUTION,
        collected_at=NOW,
        source="s" * 256,
    )
    with pytest.raises(ValueError, match="bounded"):
        build_evidence_timeline(report, (), (artifact,))


def test_timeline_orders_by_source_time_then_observation_then_generation() -> None:
    report = _report()
    assert report.execution is not None and report.execution.executed_at is not None
    old_source_time = _artifact("syn_run:execution", ArtifactType.EXECUTION, collected_at=NOW)
    no_source_time = _artifact(
        "syn_run:context",
        ArtifactType.CONTEXT_EVIDENCE,
        collected_at=datetime(2026, 8, 30, tzinfo=UTC),
    )

    timeline = build_evidence_timeline(report, (), (old_source_time, no_source_time))

    context_event = next(event for event in timeline if event.artifact_ids == ("syn_run:context",))
    execution_event = next(
        event for event in timeline if event.artifact_ids == ("syn_run:execution",)
    )
    assert context_event.sequence < execution_event.sequence
    assert cast(JsonValue, execution_event.attributes["artifact_type"]) == "execution"
