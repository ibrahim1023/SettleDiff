"""Deterministic evidence timeline derived from persisted report evidence."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Annotated, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, JsonValue, StringConstraints

from settlediff.application.run import RunEvent
from settlediff.domain.models import (
    ArtifactType,
    EvidenceArtifact,
    ExecutionRecord,
    LedgerRecord,
    MachineReport,
    NonEmptyStr,
    PaymentReceipt,
    UtcDatetime,
)
from settlediff.domain.redaction import redact_embedded_identifiers, redact_value

TimelineAttributeName = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)
]
TimelineAttributeValue = (
    Annotated[str, StringConstraints(strip_whitespace=True, max_length=256)] | bool | int | None
)

_ARTIFACT_SOURCE_TIME_FIELDS = {
    ArtifactType.EXECUTION: "execution",
    ArtifactType.PAYMENT_RECEIPT: "receipt",
    ArtifactType.ACTIVITY: "ledger",
}
_ARTIFACT_FINDING_ALIASES = {
    ArtifactType.SERVICE_CONTRACT: "contract",
    ArtifactType.EXECUTION: "execution",
    ArtifactType.PAYMENT_RECEIPT: "receipt",
    ArtifactType.ACTIVITY: "activity",
    ArtifactType.CONTEXT_EVIDENCE: "context",
}
_MAX_SOURCE_LENGTH = 255


class EvidenceTimelineEvent(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    sequence: int = Field(ge=0)
    source_time: UtcDatetime | None
    observed_at: UtcDatetime
    source: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)]
    artifact_ids: tuple[NonEmptyStr, ...] = Field(default=(), max_length=16)
    finding_ids: tuple[NonEmptyStr, ...] = Field(default=(), max_length=16)
    attributes: dict[TimelineAttributeName, TimelineAttributeValue] = Field(
        default_factory=dict, max_length=16
    )


def build_evidence_timeline(
    report: MachineReport,
    run_events: tuple[RunEvent, ...],
    artifacts: tuple[EvidenceArtifact, ...],
) -> tuple[EvidenceTimelineEvent, ...]:
    """Derive a deterministic supported partial order over report evidence.

    Events sort by their source timestamp when one is available and otherwise by
    observation time; provisional generation order breaks remaining ties. This is
    a supported partial order, not a claim of exact chronology.
    """
    provisional: list[EvidenceTimelineEvent] = []

    def add(
        *,
        source_time: datetime | None,
        observed_at: datetime,
        source: str,
        artifact_ids: tuple[str, ...] = (),
        finding_ids: tuple[str, ...] = (),
        attributes: dict[str, str | bool | int | None],
    ) -> None:
        provisional.append(
            EvidenceTimelineEvent(
                sequence=len(provisional),
                source_time=source_time,
                observed_at=observed_at,
                source=_bounded_source(source),
                artifact_ids=artifact_ids,
                finding_ids=finding_ids,
                attributes=_attributes(attributes),
            )
        )

    add(
        source_time=report.intent.created_at,
        observed_at=report.intent.created_at,
        source="settlediff.intent",
        attributes={"event": "intent_recorded"},
    )
    for event in run_events:
        add(
            source_time=event.occurred_at,
            observed_at=event.occurred_at,
            source="settlediff.run_state",
            attributes={"event": "run_state", "state": event.state.value},
        )
    for artifact in sorted(artifacts, key=lambda value: (value.collected_at, value.artifact_id)):
        add(
            source_time=_artifact_source_time(report, artifact),
            observed_at=artifact.collected_at,
            source=artifact.source,
            artifact_ids=(artifact.artifact_id,),
            finding_ids=_artifact_finding_ids(report, artifact),
            attributes={
                "event": "artifact_observed",
                "artifact_type": artifact.artifact_type.value,
                "redacted": artifact.redacted,
            },
        )
    delivery = report.delivery
    if delivery is not None and delivery.observation is not None:
        observation = delivery.observation
        execution_artifact = _cited_execution_artifact(artifacts, observation.evidence_ids)
        attributes: dict[str, str | bool | int | None] = {
            "event": "delivery_observed",
            "status_code": observation.status_code,
            "received_bytes": observation.received_bytes,
            "truncated": observation.truncated,
            "delivery_status": delivery.status.value,
            "reason_code": delivery.reason_code,
        }
        if observation.media_type is not None:
            attributes["media_type"] = observation.media_type
        add(
            source_time=None,
            observed_at=observation.observed_at,
            source=(
                f"{execution_artifact.source}.paid_response"
                if execution_artifact is not None
                else "settlediff.delivery"
            ),
            artifact_ids=delivery.evidence_ids,
            finding_ids=tuple(
                finding.finding_id for finding in report.findings if finding.check_id == "delivery"
            )[:16],
            attributes=attributes,
        )
    ordered = sorted(
        provisional,
        key=lambda event: (
            event.source_time if event.source_time is not None else event.observed_at,
            event.observed_at,
            event.sequence,
        ),
    )
    return tuple(
        event.model_copy(update={"sequence": sequence}) for sequence, event in enumerate(ordered)
    )


def _bounded_source(source: str) -> str:
    redacted = redact_embedded_identifiers(source).strip()
    if not redacted or len(redacted) > _MAX_SOURCE_LENGTH:
        raise ValueError("timeline event source exceeds the bounded length")
    return redacted


def _attributes(
    attributes: dict[str, str | bool | int | None],
) -> dict[str, str | bool | int | None]:
    return {
        name: cast(
            str | bool | int | None,
            redact_value(cast(JsonValue, value), key=name),
        )
        for name, value in attributes.items()
    }


def redact_timeline_event(event: EvidenceTimelineEvent) -> EvidenceTimelineEvent:
    """Reapply bounded-source and attribute redaction to a supplied event."""
    payload = cast(dict[str, JsonValue], event.model_dump(mode="json"))
    payload["source"] = _bounded_source(event.source)
    payload["attributes"] = cast(
        JsonValue,
        {
            name: redact_value(cast(JsonValue, value), key=name)
            for name, value in event.attributes.items()
        },
    )
    return EvidenceTimelineEvent.model_validate_json(json.dumps(payload), strict=True)


def _artifact_source_time(report: MachineReport, artifact: EvidenceArtifact) -> datetime | None:
    field = _ARTIFACT_SOURCE_TIME_FIELDS.get(artifact.artifact_type)
    if field is None:
        return None
    record = getattr(report, field)
    if isinstance(record, ExecutionRecord):
        return record.executed_at
    if isinstance(record, PaymentReceipt):
        return record.issued_at
    if isinstance(record, LedgerRecord):
        return record.occurred_at
    return None


def _artifact_finding_ids(report: MachineReport, artifact: EvidenceArtifact) -> tuple[str, ...]:
    alias = _ARTIFACT_FINDING_ALIASES.get(artifact.artifact_type)
    cited = {artifact.artifact_id, *((alias,) if alias is not None else ())}
    return tuple(
        finding.finding_id for finding in report.findings if cited & set(finding.artifact_ids)
    )[:16]


def _cited_execution_artifact(
    artifacts: tuple[EvidenceArtifact, ...], evidence_ids: tuple[str, ...]
) -> EvidenceArtifact | None:
    cited = set(evidence_ids)
    for artifact in artifacts:
        if artifact.artifact_type is ArtifactType.EXECUTION and artifact.artifact_id in cited:
            return artifact
    return None
