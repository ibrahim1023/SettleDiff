"""Deterministic persisted-evidence purchase investigation."""

from __future__ import annotations

from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from settlediff.application.bundle import BundleError, export_bundle
from settlediff.application.run import RunEvent
from settlediff.application.timeline import EvidenceTimelineEvent
from settlediff.domain.drift import ContractDrift, ContractSnapshot, compare_contract_snapshots
from settlediff.domain.integrity import Sha256Digest
from settlediff.domain.models import (
    CheckStatus,
    DeliveryAssessment,
    EvidenceArtifact,
    ExplanationRecord,
    Finding,
    MachineReport,
    NonEmptyStr,
    RetryAssessment,
    Severity,
    Verdict,
)
from settlediff.domain.verdict import derive_verdict


class InvestigationError(ValueError):
    """Persisted evidence could not support a consistent investigation."""


class InvestigationNotFoundError(InvestigationError):
    """The requested run is not persisted."""


class InvestigationRepository(Protocol):
    def get(self, run_id: str) -> MachineReport | None: ...

    def events(self, run_id: str) -> tuple[RunEvent, ...]: ...

    def timeline(self, run_id: str) -> tuple[EvidenceTimelineEvent, ...]: ...

    def artifacts(self, run_id: str) -> tuple[EvidenceArtifact, ...]: ...

    def explanation(self, run_id: str) -> ExplanationRecord | None: ...

    def contract_snapshots(self, target: str, rail: str) -> tuple[ContractSnapshot, ...]: ...

    def observed_contract_snapshots(
        self, target: str, rail: str
    ) -> tuple[ContractSnapshot, ...]: ...


class InvestigationFinding(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    finding_id: NonEmptyStr
    check_id: NonEmptyStr
    severity: Severity
    status: CheckStatus
    message: NonEmptyStr
    evidence_ids: tuple[NonEmptyStr, ...] = Field(max_length=16)


class InvestigationBundle(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    status: Literal["AVAILABLE", "UNAVAILABLE"]
    bundle_sha256: Sha256Digest | None

    @model_validator(mode="after")
    def require_coherent_status(self) -> InvestigationBundle:
        if (self.status == "AVAILABLE") != (self.bundle_sha256 is not None):
            raise ValueError("bundle availability must match its digest")
        return self


class PurchaseInvestigation(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    run_id: NonEmptyStr
    verdict: Verdict
    issues: tuple[InvestigationFinding, ...]
    money_movement: InvestigationFinding | None
    amount_agreement: InvestigationFinding | None
    recipient_agreement: InvestigationFinding | None
    delivery: DeliveryAssessment | None
    activity_agreement: tuple[InvestigationFinding, ...]
    drift: ContractDrift | None
    retry: RetryAssessment | None
    timeline: tuple[EvidenceTimelineEvent, ...]
    bundle: InvestigationBundle


_SNAPSHOT_RAILS = frozenset({"perflo", "x402"})


def _summary(finding: Finding) -> InvestigationFinding:
    return InvestigationFinding(
        finding_id=finding.finding_id,
        check_id=finding.check_id,
        severity=finding.severity,
        status=finding.status,
        message=finding.message,
        evidence_ids=finding.artifact_ids,
    )


def _select(findings: dict[str, Finding], check_id: str) -> InvestigationFinding | None:
    finding = findings.get(check_id)
    return _summary(finding) if finding is not None else None


def _drift(report: MachineReport, repository: InvestigationRepository) -> ContractDrift | None:
    if report.contract is None or report.adapter_id not in _SNAPSHOT_RAILS:
        return None
    target = report.contract.vendor_slug if report.adapter_id == "perflo" else report.contract.url
    if target is None:
        return None
    snapshots = repository.observed_contract_snapshots(target, report.adapter_id)
    if not snapshots:
        return None
    try:
        if len(snapshots) == 1:
            return compare_contract_snapshots(None, snapshots[-1])
        return compare_contract_snapshots(snapshots[-2], snapshots[-1])
    except ValueError as error:
        raise InvestigationError(f"contract snapshots are inconsistent: {error}") from error


def investigate_purchase(repository: InvestigationRepository, run_id: str) -> PurchaseInvestigation:
    """Project persisted evidence into a rail-neutral purchase investigation."""
    report = repository.get(run_id)
    if report is None:
        raise InvestigationNotFoundError(f"run {run_id!r} not found")
    if report.run_id != run_id:
        raise InvestigationError(
            f"requested run {run_id!r} but persisted report belongs to {report.run_id!r}"
        )
    if report.run_id != report.intent.run_id:
        raise InvestigationError("persisted report and intent run IDs differ")
    if derive_verdict(report.findings, delivery=report.delivery) is not report.verdict:
        raise InvestigationError("persisted verdict is inconsistent with its findings")
    finding_ids = [finding.finding_id for finding in report.findings]
    if len(set(finding_ids)) != len(finding_ids):
        raise InvestigationError("persisted report contains duplicate finding IDs")
    check_ids = [finding.check_id for finding in report.findings]
    if len(set(check_ids)) != len(check_ids):
        raise InvestigationError("persisted report contains duplicate check IDs")

    timeline = repository.timeline(run_id)
    if [event.sequence for event in timeline] != list(range(len(timeline))):
        raise InvestigationError("persisted timeline sequences are not exact positions")

    try:
        bundle = export_bundle(repository, run_id)
    except BundleError:
        bundle_status = InvestigationBundle(status="UNAVAILABLE", bundle_sha256=None)
    except ValidationError as error:
        raise InvestigationError("persisted evidence failed strict validation") from error
    else:
        bundle_status = InvestigationBundle(status="AVAILABLE", bundle_sha256=bundle.bundle_sha256)

    try:
        by_check = {finding.check_id: finding for finding in report.findings}
        activity = tuple(
            summary
            for check_id in ("activity_persistence", "ledger_outcome")
            if (summary := _select(by_check, check_id)) is not None
        )
        investigation = PurchaseInvestigation(
            run_id=report.run_id,
            verdict=report.verdict,
            issues=tuple(
                _summary(finding)
                for finding in report.findings
                if finding.status is not CheckStatus.PASS
            ),
            money_movement=_select(by_check, "settlement"),
            amount_agreement=_select(by_check, "price"),
            recipient_agreement=_select(by_check, "recipient"),
            delivery=report.delivery,
            activity_agreement=activity,
            drift=_drift(report, repository),
            retry=report.retry,
            timeline=timeline,
            bundle=bundle_status,
        )
        return PurchaseInvestigation.model_validate_json(
            investigation.model_dump_json(), strict=True
        )
    except ValidationError as error:
        raise InvestigationError("persisted evidence failed strict validation") from error
