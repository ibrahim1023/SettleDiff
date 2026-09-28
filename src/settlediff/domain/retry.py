"""Deterministic retry-safety assessment of persisted payment evidence."""

from __future__ import annotations

from typing import Literal, cast

from pydantic import JsonValue

from settlediff.domain.models import (
    ArtifactType,
    CanonicalModel,
    EvidenceArtifact,
    LedgerStatus,
    MachineReport,
    NonEmptyStr,
    RetryAssessment,
    RetrySafety,
    SettlementStatus,
)

CONFIRMED_TRANSFER = "CONFIRMED_TRANSFER"
CONFIRMED_RECEIPT = "CONFIRMED_RECEIPT"
REVERTED_RECEIPT = "REVERTED_RECEIPT"
TRANSMISSION_CONFIRMED = "TRANSMISSION_CONFIRMED"
PROVIDER_PAYMENT_ATTEMPT = "PROVIDER_PAYMENT_ATTEMPT"
EXPLICIT_NON_SUBMISSION = "EXPLICIT_NON_SUBMISSION"
RUN_REFUSED = "RUN_REFUSED"
SUBMISSION_UNCERTAIN = "SUBMISSION_UNCERTAIN"
RECOVERY_PENDING = "RECOVERY_PENDING"
RECOVERY_UNAVAILABLE = "RECOVERY_UNAVAILABLE"
RECOVERY_INVALID = "RECOVERY_INVALID"
EVIDENCE_CONTRADICTION = "EVIDENCE_CONTRADICTION"
EVIDENCE_MISSING = "EVIDENCE_MISSING"
NON_SUBMISSION_UNPROVEN = "NON_SUBMISSION_UNPROVEN"

_REASON_ORDER = (
    CONFIRMED_TRANSFER,
    CONFIRMED_RECEIPT,
    REVERTED_RECEIPT,
    TRANSMISSION_CONFIRMED,
    PROVIDER_PAYMENT_ATTEMPT,
    EXPLICIT_NON_SUBMISSION,
    RUN_REFUSED,
    SUBMISSION_UNCERTAIN,
    RECOVERY_PENDING,
    RECOVERY_UNAVAILABLE,
    RECOVERY_INVALID,
    EVIDENCE_CONTRADICTION,
    EVIDENCE_MISSING,
    NON_SUBMISSION_UNPROVEN,
)

_RECEIPT_PENDING_STATES = frozenset({"pending", "unknown", "unresolved", "processing", "executing"})
_RECEIPT_SUBMITTED_STATES = frozenset({"submitted"})
_NON_SUBMISSION_SOURCES = frozenset({"not_submitted", "proven_not_submitted"})
_ATTEMPT_STATUSES = frozenset(
    {"settled", "failed", "pending", "confirmed", "submitted", "success", "processing", "executing"}
)
_ACTIVITY_ENTRY_KEYS = ("entries", "activity", "items", "records")

_SAFE = RetrySafety.SAFE_TO_RETRY
_HUMAN = RetrySafety.REQUIRES_HUMAN_DECISION
_DO_NOT = RetrySafety.DO_NOT_RETRY

_Signal = tuple[RetrySafety, str, str]


class RetryRunStateSnapshot(CanonicalModel):
    schema_version: Literal[1] = 1
    run_id: NonEmptyStr
    state: NonEmptyStr
    submission_uncertain: bool


def _receipt_signals(artifact: EvidenceArtifact, data: JsonValue) -> list[_Signal]:
    artifact_id = artifact.artifact_id
    if not isinstance(data, dict):
        return [(_HUMAN, RECOVERY_INVALID, artifact_id)]
    record = cast(dict[str, JsonValue], data)
    signals: list[_Signal] = []
    status = record.get("status")
    source_state = record.get("source_submission_state")
    if source_state == "submitted_confirmed":
        signals.append((_DO_NOT, TRANSMISSION_CONFIRMED, artifact_id))
    if status in {"confirmed", "success"}:
        reason = (
            CONFIRMED_TRANSFER
            if "transaction_receipt" in artifact.source.casefold()
            else CONFIRMED_RECEIPT
        )
        signals.append((_DO_NOT, reason, artifact_id))
    elif status == "failed":
        signals.append((_DO_NOT, REVERTED_RECEIPT, artifact_id))
    elif status == "not_submitted":
        if record.get("proof_of_non_submission") is True:
            if source_state is None or source_state in _NON_SUBMISSION_SOURCES:
                signals.append((_SAFE, EXPLICIT_NON_SUBMISSION, artifact_id))
            else:
                signals.append((_HUMAN, EVIDENCE_CONTRADICTION, artifact_id))
        else:
            signals.append((_HUMAN, NON_SUBMISSION_UNPROVEN, artifact_id))
    elif status in _RECEIPT_PENDING_STATES:
        signals.append((_HUMAN, RECOVERY_PENDING, artifact_id))
    elif status in _RECEIPT_SUBMITTED_STATES or (status is None and _has_attempt_fields(record)):
        signals.append((_HUMAN, PROVIDER_PAYMENT_ATTEMPT, artifact_id))
    else:
        signals.append((_HUMAN, RECOVERY_INVALID, artifact_id))
    diagnostic = record.get("diagnostic")
    if diagnostic == "rpc_unavailable":
        signals.append((_HUMAN, RECOVERY_UNAVAILABLE, artifact_id))
    elif diagnostic is not None:
        signals.append((_HUMAN, RECOVERY_INVALID, artifact_id))
    return signals


def _has_attempt_fields(record: dict[str, JsonValue]) -> bool:
    if (
        record.get("settlement_status") in _ATTEMPT_STATUSES
        or record.get("status") in _ATTEMPT_STATUSES
    ):
        return True
    for key in ("transaction_reference", "transaction_hash", "transaction_id"):
        value = record.get(key)
        if isinstance(value, str) and value:
            return True
    return record.get("payment_attempted") is True


def _activity_entries(data: JsonValue) -> tuple[dict[str, JsonValue], ...] | None:
    if isinstance(data, list):
        if any(not isinstance(entry, dict) for entry in data):
            return None
        return tuple(cast(dict[str, JsonValue], entry) for entry in data)
    if isinstance(data, dict):
        record = cast(dict[str, JsonValue], data)
        entries = [record]
        for key in _ACTIVITY_ENTRY_KEYS:
            if key in record:
                nested = record[key]
                if not isinstance(nested, list) or any(
                    not isinstance(entry, dict) for entry in nested
                ):
                    return None
                entries.extend(cast(dict[str, JsonValue], entry) for entry in nested)
        return tuple(entries)
    return None


def _activity_signals(artifact: EvidenceArtifact, data: JsonValue) -> list[_Signal]:
    artifact_id = artifact.artifact_id
    entries = _activity_entries(data)
    if entries is None:
        return [(_HUMAN, RECOVERY_INVALID, artifact_id)]
    if isinstance(data, dict) and not data or isinstance(data, list) and not data:
        return []
    signals: list[_Signal] = []
    if "transaction_receipt" in artifact.source.casefold():
        for entry in entries:
            status = entry.get("status")
            if status == "confirmed":
                signals.append((_DO_NOT, CONFIRMED_TRANSFER, artifact_id))
            elif status == "failed":
                signals.append((_DO_NOT, REVERTED_RECEIPT, artifact_id))
    signals.append((_HUMAN, PROVIDER_PAYMENT_ATTEMPT, artifact_id))
    return signals


def _execution_signals(artifact: EvidenceArtifact, data: JsonValue) -> list[_Signal]:
    artifact_id = artifact.artifact_id
    if not isinstance(data, dict):
        return [(_HUMAN, RECOVERY_INVALID, artifact_id)]
    record = cast(dict[str, JsonValue], data)
    if _has_attempt_fields(record):
        return [(_HUMAN, PROVIDER_PAYMENT_ATTEMPT, artifact_id)]
    return []


def _report_signals(report: MachineReport | None, run_id: str) -> list[_Signal]:
    if report is None:
        return []
    attempted = False
    receipt = report.receipt
    if receipt is not None and (
        receipt.settlement_status
        in {SettlementStatus.SETTLED, SettlementStatus.FAILED, SettlementStatus.PENDING}
        or receipt.transaction_id
        or receipt.transaction_hash
    ):
        attempted = True
    execution = report.execution
    if execution is not None and (
        execution.settlement_status
        in {SettlementStatus.SETTLED, SettlementStatus.FAILED, SettlementStatus.PENDING}
        or execution.transaction_id
        or execution.transaction_hash
    ):
        attempted = True
    ledger = report.ledger
    if ledger is not None and (
        ledger.status in {LedgerStatus.CONFIRMED, LedgerStatus.FAILED, LedgerStatus.PENDING}
        or ledger.transaction_id
        or ledger.transaction_hash
    ):
        attempted = True
    if attempted:
        return [(_HUMAN, PROVIDER_PAYMENT_ATTEMPT, f"{run_id}:report")]
    return []


def analyze_retry(
    report: MachineReport | None,
    recovery_artifacts: tuple[EvidenceArtifact, ...],
    run_state: RetryRunStateSnapshot,
) -> RetryAssessment:
    """Combine persisted evidence into a conservative retry-safety assessment."""
    run_state_id = f"{run_state.run_id}:run_state"
    signals: list[_Signal] = []
    for artifact in recovery_artifacts:
        if artifact.artifact_type is ArtifactType.PAYMENT_RECEIPT:
            signals.extend(_receipt_signals(artifact, artifact.data))
        elif artifact.artifact_type is ArtifactType.ACTIVITY:
            signals.extend(_activity_signals(artifact, artifact.data))
        elif artifact.artifact_type is ArtifactType.EXECUTION:
            signals.extend(_execution_signals(artifact, artifact.data))
    signals.extend(_report_signals(report, run_state.run_id))
    if run_state.submission_uncertain:
        signals.append((_HUMAN, SUBMISSION_UNCERTAIN, run_state_id))
    elif run_state.state == "refused":
        signals.append((_SAFE, RUN_REFUSED, run_state_id))

    safeties = {safety for safety, _, _ in signals}
    has_safe = _SAFE in safeties
    has_blocking = _HUMAN in safeties or _DO_NOT in safeties
    if has_safe and has_blocking:
        signals.append((_HUMAN, EVIDENCE_CONTRADICTION, run_state_id))
    if _DO_NOT in safeties:
        safety = _DO_NOT
    elif has_blocking:
        safety = _HUMAN
    elif has_safe:
        safety = _SAFE
    else:
        safety = _HUMAN
        signals.append((_HUMAN, EVIDENCE_MISSING, run_state_id))

    reason_codes = tuple(
        reason for reason in _REASON_ORDER if any(code == reason for _, code, _ in signals)
    )
    evidence_ids = tuple(sorted({evidence for _, _, evidence in signals}))[:16]
    return RetryAssessment(safety=safety, reason_codes=reason_codes, evidence_ids=evidence_ids)
