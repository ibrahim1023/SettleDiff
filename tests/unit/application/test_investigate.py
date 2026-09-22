from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import NoReturn, cast

import pytest
from pydantic import JsonValue

from settlediff.application.bundle import export_bundle
from settlediff.application.investigate import (
    InvestigationError,
    InvestigationNotFoundError,
    InvestigationRepository,
    PurchaseInvestigation,
    investigate_purchase,
)
from settlediff.application.replay import replay_fixture
from settlediff.application.run import RunEvent, RunState
from settlediff.application.timeline import EvidenceTimelineEvent
from settlediff.domain.drift import DriftStatus, build_contract_snapshot
from settlediff.domain.integrity import sha256_digest
from settlediff.domain.models import (
    ArtifactType,
    CheckStatus,
    DeliveryAssessment,
    DeliveryObservation,
    DeliveryStatus,
    EvidenceArtifact,
    MachineReport,
    RetryAssessment,
    RetrySafety,
    Verdict,
)
from settlediff.domain.verdict import derive_verdict
from settlediff.storage.sqlite import SQLiteReportRepository

FIXTURES = Path(__file__).parents[3] / "fixtures"
EVENT = RunEvent(state=RunState.COMPLETE, occurred_at=datetime(2026, 8, 12, tzinfo=UTC))

_ARTIFACT_FILES = {
    "contract.json": ArtifactType.SERVICE_CONTRACT,
    "execution.json": ArtifactType.EXECUTION,
    "receipt.json": ArtifactType.PAYMENT_RECEIPT,
    "activity.json": ArtifactType.ACTIVITY,
}


def _fixture_artifacts(scenario: str, run_id: str) -> tuple[EvidenceArtifact, ...]:
    return tuple(
        EvidenceArtifact(
            artifact_id=f"{run_id}:{artifact_type.value}",
            artifact_type=artifact_type,
            source="fixture",
            collected_at=datetime(2026, 8, 12, tzinfo=UTC),
            redacted=False,
            data=json.loads((FIXTURES / scenario / name).read_text()),
        )
        for name, artifact_type in _ARTIFACT_FILES.items()
        if (FIXTURES / scenario / name).is_file()
    )


def _persist(
    tmp_path: Path,
    scenario: str,
    *,
    adapter_id: str | None = None,
    with_artifacts: bool = True,
) -> tuple[SQLiteReportRepository, MachineReport]:
    repository = SQLiteReportRepository(tmp_path / f"{scenario}.sqlite3")
    report = replay_fixture(FIXTURES / scenario)
    if adapter_id is not None:
        report = report.model_copy(update={"adapter_id": adapter_id})
    artifacts = _fixture_artifacts(scenario, report.run_id) if with_artifacts else ()
    repository.save(report, events=(EVENT,), artifacts=artifacts)
    return repository, report


class _GuardedRepository:
    """Delegates persisted reads; external seams raise if touched."""

    def __init__(self, repository: SQLiteReportRepository) -> None:
        self._repository = repository

    def get(self, run_id: str) -> MachineReport | None:
        return self._repository.get(run_id)

    def events(self, run_id: str):
        return self._repository.events(run_id)

    def timeline(self, run_id: str):
        return self._repository.timeline(run_id)

    def artifacts(self, run_id: str):
        return self._repository.artifacts(run_id)

    def explanation(self, run_id: str):
        return self._repository.explanation(run_id)

    def contract_snapshots(self, target: str, rail: str):
        return self._repository.contract_snapshots(target, rail)

    def observed_contract_snapshots(self, target: str, rail: str):
        return self._repository.observed_contract_snapshots(target, rail)

    def request(self, *_args: object, **_kwargs: object) -> NoReturn:
        raise AssertionError("investigation attempted an external call")

    def connect(self, *_args: object, **_kwargs: object) -> NoReturn:
        raise AssertionError("investigation attempted a network connection")

    def close(self) -> None:
        self._repository.close()


def test_clean_success_investigation(tmp_path: Path) -> None:
    repository, report = _persist(tmp_path, "clean-success")
    guarded = _GuardedRepository(repository)

    investigation = investigate_purchase(cast(InvestigationRepository, guarded), report.run_id)

    assert investigation.run_id == report.run_id
    assert investigation.verdict is Verdict.VERIFIED
    assert investigation.issues == ()
    assert investigation.money_movement is not None
    assert investigation.money_movement.check_id == "settlement"
    assert investigation.money_movement.status is CheckStatus.PASS
    assert investigation.money_movement.evidence_ids == tuple(
        next(f for f in report.findings if f.check_id == "settlement").artifact_ids
    )
    assert investigation.amount_agreement is not None
    assert investigation.amount_agreement.check_id == "price"
    assert investigation.recipient_agreement is not None
    assert investigation.delivery is None
    assert [f.check_id for f in investigation.activity_agreement] == [
        "activity_persistence",
        "ledger_outcome",
    ]
    assert investigation.drift is None
    assert investigation.retry is None
    assert investigation.timeline == repository.timeline(report.run_id)
    repository.close()


def test_paid_failure_surfaces_unresolved_issues(tmp_path: Path) -> None:
    repository, report = _persist(tmp_path, "paid-failure")

    investigation = investigate_purchase(repository, report.run_id)

    assert investigation.verdict is Verdict.PAID_FAILURE
    assert [(i.check_id, i.status) for i in investigation.issues] == [
        ("service_execution", CheckStatus.FAIL),
        ("paid_failure", CheckStatus.FAIL),
        ("ledger_outcome", CheckStatus.WARN),
    ]
    repository.close()


def test_failed_broadcast_money_movement_is_unknown(tmp_path: Path) -> None:
    repository, report = _persist(tmp_path, "failed-broadcast")

    investigation = investigate_purchase(repository, report.run_id)

    assert investigation.money_movement is not None
    assert investigation.money_movement.status is CheckStatus.UNKNOWN
    assert investigation.money_movement.check_id == "settlement"
    repository.close()


def test_ambiguous_activity_surfaces_both_activity_checks(tmp_path: Path) -> None:
    repository, report = _persist(tmp_path, "ambiguous-activity")

    investigation = investigate_purchase(repository, report.run_id)

    assert [(f.check_id, f.status) for f in investigation.activity_agreement] == [
        ("activity_persistence", CheckStatus.WARN),
        ("ledger_outcome", CheckStatus.UNKNOWN),
    ]
    repository.close()


def test_chain_conflict_visible_in_issues(tmp_path: Path) -> None:
    repository, report = _persist(tmp_path, "chain-diff")

    investigation = investigate_purchase(repository, report.run_id)

    assert [i.check_id for i in investigation.issues] == ["chain"]
    assert investigation.issues[0].status is CheckStatus.DIFF
    repository.close()


def test_no_snapshots_means_no_drift(tmp_path: Path) -> None:
    repository, report = _persist(tmp_path, "x402-clean-success", adapter_id="x402")

    investigation = investigate_purchase(repository, report.run_id)

    assert investigation.drift is None
    repository.close()


def test_single_snapshot_reports_unavailable_drift(tmp_path: Path) -> None:
    repository, report = _persist(tmp_path, "x402-clean-success", adapter_id="x402")
    assert report.contract is not None
    snapshot = build_contract_snapshot(
        report.contract.url, "x402", report.contract, cast(JsonValue, {"synthetic": True})
    )
    repository.save_contract_snapshot(snapshot, datetime(2026, 9, 1, tzinfo=UTC))

    investigation = investigate_purchase(repository, report.run_id)

    assert investigation.drift is not None
    assert investigation.drift.status is DriftStatus.UNAVAILABLE
    assert investigation.drift.current_snapshot_digest == snapshot.snapshot_digest
    repository.close()


def test_two_snapshots_report_diff(tmp_path: Path) -> None:
    repository, report = _persist(tmp_path, "x402-clean-success", adapter_id="x402")
    assert report.contract is not None
    first = build_contract_snapshot(
        report.contract.url, "x402", report.contract, cast(JsonValue, {"v": 1})
    )
    second = build_contract_snapshot(
        report.contract.url, "x402", report.contract, cast(JsonValue, {"v": 2})
    )
    repository.save_contract_snapshot(first, datetime(2026, 9, 1, tzinfo=UTC))
    repository.save_contract_snapshot(second, datetime(2026, 9, 2, tzinfo=UTC))

    investigation = investigate_purchase(repository, report.run_id)

    assert investigation.drift is not None
    assert investigation.drift.status is DriftStatus.DIFF
    assert investigation.drift.previous_snapshot_digest == first.snapshot_digest
    assert investigation.drift.current_snapshot_digest == second.snapshot_digest
    repository.close()


@pytest.mark.parametrize("safety", list(RetrySafety))
def test_retry_assessment_copied_exactly(tmp_path: Path, safety: RetrySafety) -> None:
    repository, report = _persist(tmp_path, "clean-success")
    retry = RetryAssessment(
        safety=safety,
        reason_codes=("synthetic_reason",),
        evidence_ids=(f"{report.run_id}:execution",),
    )
    updated = report.model_copy(update={"schema_version": 3, "retry": retry})
    repository.save(
        updated, events=(EVENT,), artifacts=_fixture_artifacts("clean-success", report.run_id)
    )

    investigation = investigate_purchase(repository, report.run_id)

    assert investigation.retry == retry
    repository.close()


def test_delivery_assessment_copied_and_verdict_checked(tmp_path: Path) -> None:
    repository, report = _persist(tmp_path, "clean-success")
    delivery = DeliveryAssessment(
        status=DeliveryStatus.SATISFIED,
        reason_code="body_matched",
        evidence_ids=(f"{report.run_id}:execution",),
        observation=DeliveryObservation(
            observed_at=datetime(2026, 8, 12, tzinfo=UTC),
            status_code=200,
            media_type="application/json",
            received_bytes=10,
            truncated=False,
            parsed_body=None,
            evidence_ids=(f"{report.run_id}:execution",),
        ),
        response_contract_digest=sha256_digest({"synthetic": True}),
    )
    updated = report.model_copy(update={"schema_version": 3, "delivery": delivery})
    assert derive_verdict(updated.findings, delivery=delivery) is updated.verdict
    repository.save(
        updated, events=(EVENT,), artifacts=_fixture_artifacts("clean-success", report.run_id)
    )

    investigation = investigate_purchase(repository, report.run_id)

    assert investigation.delivery == delivery
    repository.close()


def test_bundle_available_matches_export(tmp_path: Path) -> None:
    repository, report = _persist(tmp_path, "clean-success")

    investigation = investigate_purchase(repository, report.run_id)

    assert investigation.bundle.status == "AVAILABLE"
    assert (
        investigation.bundle.bundle_sha256 == export_bundle(repository, report.run_id).bundle_sha256
    )
    repository.close()


def test_bundle_unavailable_when_citations_incomplete(tmp_path: Path) -> None:
    repository, report = _persist(tmp_path, "clean-success", with_artifacts=False)

    investigation = investigate_purchase(repository, report.run_id)

    assert investigation.bundle.status == "UNAVAILABLE"
    assert investigation.bundle.bundle_sha256 is None
    repository.close()


def test_missing_run_raises_not_found(tmp_path: Path) -> None:
    repository = SQLiteReportRepository(tmp_path / "empty.sqlite3")

    with pytest.raises(InvestigationNotFoundError):
        investigate_purchase(repository, "no-such-run")
    repository.close()


def test_rejects_report_intent_mismatch(tmp_path: Path) -> None:
    repository, report = _persist(tmp_path, "clean-success")
    tampered = report.model_copy(
        update={"intent": report.intent.model_copy(update={"run_id": "other-run"})}
    )
    repository.save(
        tampered, events=(EVENT,), artifacts=_fixture_artifacts("clean-success", report.run_id)
    )

    with pytest.raises(InvestigationError, match="run IDs"):
        investigate_purchase(repository, report.run_id)
    repository.close()


def test_rejects_inconsistent_verdict(tmp_path: Path) -> None:
    repository, report = _persist(tmp_path, "paid-failure")
    tampered = report.model_copy(update={"verdict": Verdict.VERIFIED})
    repository.save(
        tampered, events=(EVENT,), artifacts=_fixture_artifacts("paid-failure", report.run_id)
    )

    with pytest.raises(InvestigationError, match="verdict"):
        investigate_purchase(repository, report.run_id)
    repository.close()


def test_rejects_duplicate_finding_ids_and_check_ids(tmp_path: Path) -> None:
    repository, report = _persist(tmp_path, "clean-success")
    duplicated_id = report.findings + (report.findings[0].model_copy(update={"check_id": "dup"}),)
    tampered = report.model_copy(update={"findings": duplicated_id})
    repository.save(
        tampered, events=(EVENT,), artifacts=_fixture_artifacts("clean-success", report.run_id)
    )
    with pytest.raises(InvestigationError, match="duplicate finding IDs"):
        investigate_purchase(repository, report.run_id)

    duplicated_check = report.findings + (
        report.findings[0].model_copy(update={"finding_id": "check:dup"}),
    )
    tampered = report.model_copy(update={"findings": duplicated_check})
    repository.save(
        tampered, events=(EVENT,), artifacts=_fixture_artifacts("clean-success", report.run_id)
    )
    with pytest.raises(InvestigationError, match="duplicate check IDs"):
        investigate_purchase(repository, report.run_id)
    repository.close()


def test_rejects_timeline_sequence_gap(tmp_path: Path) -> None:
    repository, report = _persist(tmp_path, "clean-success")

    class _GappedRepository(_GuardedRepository):
        def timeline(self, run_id: str) -> tuple[EvidenceTimelineEvent, ...]:
            return tuple(
                event.model_copy(update={"sequence": event.sequence + 1})
                for event in self._repository.timeline(run_id)
            )

    with pytest.raises(InvestigationError, match="timeline"):
        investigate_purchase(
            cast(InvestigationRepository, _GappedRepository(repository)), report.run_id
        )
    repository.close()


def test_rejects_requested_persisted_identity_mismatch(tmp_path: Path) -> None:
    repository, report = _persist(tmp_path, "clean-success")

    class _AlienRepository(_GuardedRepository):
        def get(self, run_id: str) -> MachineReport | None:
            return self._repository.get(report.run_id)

        def timeline(self, run_id: str):
            raise AssertionError("timeline read must not happen before identity check")

    with pytest.raises(InvestigationError, match="requested run"):
        investigate_purchase(
            cast(InvestigationRepository, _AlienRepository(repository)), "requested-other-run"
        )
    repository.close()


def test_observed_snapshots_aba_compares_latest_pair(tmp_path: Path) -> None:
    repository, report = _persist(tmp_path, "x402-clean-success", adapter_id="x402")
    assert report.contract is not None
    snapshot_a = build_contract_snapshot(
        report.contract.url, "x402", report.contract, cast(JsonValue, {"v": 1})
    )
    snapshot_b = build_contract_snapshot(
        report.contract.url, "x402", report.contract, cast(JsonValue, {"v": 2})
    )
    repository.save_contract_snapshot(snapshot_a, datetime(2026, 9, 1, tzinfo=UTC))
    repository.save_contract_snapshot(snapshot_b, datetime(2026, 9, 2, tzinfo=UTC))
    repository.save_contract_snapshot(snapshot_a, datetime(2026, 9, 3, tzinfo=UTC))

    investigation = investigate_purchase(repository, report.run_id)

    assert investigation.drift is not None
    assert investigation.drift.status is DriftStatus.DIFF
    assert investigation.drift.previous_snapshot_digest == snapshot_b.snapshot_digest
    assert investigation.drift.current_snapshot_digest == snapshot_a.snapshot_digest
    repository.close()


def test_observed_snapshots_aa_reports_match(tmp_path: Path) -> None:
    repository, report = _persist(tmp_path, "x402-clean-success", adapter_id="x402")
    assert report.contract is not None
    snapshot = build_contract_snapshot(
        report.contract.url, "x402", report.contract, cast(JsonValue, {"v": 1})
    )
    repository.save_contract_snapshot(snapshot, datetime(2026, 9, 1, tzinfo=UTC))
    repository.save_contract_snapshot(snapshot, datetime(2026, 9, 2, tzinfo=UTC))

    investigation = investigate_purchase(repository, report.run_id)

    assert investigation.drift is not None
    assert investigation.drift.status is DriftStatus.MATCH
    assert investigation.drift.previous_snapshot_digest == snapshot.snapshot_digest
    assert investigation.drift.current_snapshot_digest == snapshot.snapshot_digest
    assert investigation.drift.change_codes == ()
    repository.close()


def test_oversized_evidence_list_fails_closed(tmp_path: Path) -> None:
    repository, report = _persist(tmp_path, "clean-success")
    findings = tuple(
        finding.model_copy(
            update={"artifact_ids": tuple(f"{report.run_id}:ev{i}" for i in range(17))}
        )
        if finding.check_id == "settlement"
        else finding
        for finding in report.findings
    )
    tampered = report.model_copy(update={"findings": findings})
    repository.save(
        tampered, events=(EVENT,), artifacts=_fixture_artifacts("clean-success", report.run_id)
    )

    with pytest.raises(InvestigationError, match="strict validation"):
        investigate_purchase(repository, report.run_id)
    repository.close()


def test_projection_strict_round_trip(tmp_path: Path) -> None:
    repository, report = _persist(tmp_path, "clean-success")

    investigation = investigate_purchase(repository, report.run_id)

    assert (
        PurchaseInvestigation.model_validate_json(investigation.model_dump_json(), strict=True)
        == investigation
    )
    repository.close()
