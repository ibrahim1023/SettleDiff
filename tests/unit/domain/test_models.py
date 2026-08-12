from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from pydantic import ValidationError

from settlediff.domain.models import (
    ArtifactType,
    CheckStatus,
    EvidenceArtifact,
    ExecutionRecord,
    ExpectedContract,
    Finding,
    InvestigationExplanation,
    LedgerRecord,
    LedgerStatus,
    MachineReport,
    PaymentReceipt,
    PurchaseIntent,
    SettlementStatus,
    Severity,
    Verdict,
)
from settlediff.domain.money import Money

NOW = datetime(2026, 8, 12, 12, tzinfo=UTC)


def intent_fixture() -> PurchaseIntent:
    return PurchaseIntent(
        run_id="run_syn_001",
        task="Inspect a synthetic purchase",
        max_budget=Money(amount=Decimal("0.05"), unit="USD"),
        requested_service=None,
        created_at=NOW,
    )


def contract_fixture() -> ExpectedContract:
    return ExpectedContract(
        vendor_slug="synthetic-search",
        url="https://example.invalid/search",
        price=Money(amount=Decimal("0.01"), unit="USDC"),
        asset="USDC",
        protocol="mpp",
        chain="base",
        request_schema={"type": "object"},
    )


def execution_fixture() -> ExecutionRecord:
    return ExecutionRecord(
        vendor_slug="synthetic-search",
        upstream_http_status=200,
        charge=Money(amount=Decimal("0.01"), unit="USDC"),
        asset="USDC",
        protocol="mpp",
        chain="tempo",
        recipient="syn_recipient_001",
        settlement_status=SettlementStatus.SETTLED,
        transaction_id="syn_tx_001",
        session_id="syn_session_001",
        transaction_hash="syn_hash_001",
        response_body={"result": "synthetic"},
        executed_at=NOW,
    )


def ledger_fixture() -> LedgerRecord:
    return LedgerRecord(
        ledger_id="syn_ledger_001",
        vendor_slug="synthetic-search",
        amount=Money(amount=Decimal("0.01"), unit="USDC"),
        asset="USDC",
        protocol="mpp",
        chain="tempo",
        recipient="syn_recipient_001",
        status=LedgerStatus.CONFIRMED,
        error_reason=None,
        transaction_id="syn_tx_001",
        session_id="syn_session_001",
        transaction_hash="syn_hash_001",
        occurred_at=NOW,
    )


def finding_fixture() -> Finding:
    return Finding(
        finding_id="finding_chain_001",
        check_id="chain_consistency",
        severity=Severity.WARNING,
        status=CheckStatus.DIFF,
        expected="base",
        observed="tempo",
        message="Advertised and executed chains differ.",
        artifact_ids=("artifact_execution_001",),
        field_paths=("execution.chain",),
    )


def machine_report_fixture() -> MachineReport:
    return MachineReport(
        run_id="run_syn_001",
        intent=intent_fixture(),
        contract=contract_fixture(),
        execution=execution_fixture(),
        ledger=ledger_fixture(),
        findings=(finding_fixture(),),
        verdict=Verdict.VERIFIED_WITH_WARNINGS,
    )


def test_canonical_models_reject_unknown_fields_and_wrong_types() -> None:
    with pytest.raises(ValidationError):
        PurchaseIntent(
            run_id="run_syn_001",
            task="Inspect",
            max_budget=Money(amount=Decimal("1"), unit="USD"),
            requested_service=None,
            created_at=NOW,
            invented=True,  # type: ignore[call-arg]
        )

    with pytest.raises(ValidationError):
        PurchaseIntent(
            run_id=123,  # type: ignore[arg-type]
            task="Inspect",
            max_budget=Money(amount=Decimal("1"), unit="USD"),
            requested_service=None,
            created_at=NOW,
        )


@pytest.mark.parametrize(
    "timestamp",
    [
        datetime(2026, 8, 12, 12),
        datetime(2026, 8, 12, 16, tzinfo=timezone(timedelta(hours=4))),
    ],
)
def test_canonical_timestamps_require_utc(timestamp: datetime) -> None:
    with pytest.raises(ValidationError, match="UTC"):
        PurchaseIntent(
            run_id="run_syn_001",
            task="Inspect",
            max_budget=Money(amount=Decimal("1"), unit="USD"),
            requested_service=None,
            created_at=timestamp,
        )


def test_evidence_artifact_carries_a_versioned_redacted_envelope() -> None:
    artifact = EvidenceArtifact(
        artifact_id="artifact_execution_001",
        artifact_type=ArtifactType.EXECUTION,
        source="synthetic_fixture",
        collected_at=NOW,
        redacted=True,
        data={"transaction_id": "syn_tx_001"},
    )

    assert artifact.schema_version == 1
    assert artifact.collected_at.tzinfo is UTC
    assert artifact.redacted is True


def test_finding_requires_artifact_citation_for_observed_value() -> None:
    with pytest.raises(ValidationError, match="artifact citation"):
        Finding(
            finding_id="finding_chain_001",
            check_id="chain_consistency",
            severity=Severity.WARNING,
            status=CheckStatus.DIFF,
            expected="base",
            observed="tempo",
            message="Advertised and executed chains differ.",
            artifact_ids=(),
            field_paths=("execution.chain",),
        )


def test_machine_report_is_immutable() -> None:
    report = machine_report_fixture()

    with pytest.raises(ValidationError):
        report.verdict = Verdict.VERIFIED


def test_explanation_is_separate_from_machine_report() -> None:
    report = machine_report_fixture()
    explanation = InvestigationExplanation(
        run_id=report.run_id,
        summary="The synthetic purchase settled with a chain difference.",
        evidence_used=("artifact_execution_001",),
        finding_ids=("finding_chain_001",),
        deterministic_verdict=report.verdict,
        recommended_next_step=None,
    )

    assert not hasattr(report, "explanation")
    assert explanation.deterministic_verdict is report.verdict


def test_machine_report_round_trips_through_versioned_json() -> None:
    report = machine_report_fixture()

    assert MachineReport.model_validate_json(report.model_dump_json()) == report


def test_payment_receipt_is_a_strict_versioned_canonical_record() -> None:
    receipt = PaymentReceipt(
        amount=Money(amount=Decimal("0.01"), unit="USDC"),
        asset="USDC",
        protocol="mpp",
        chain="tempo",
        recipient="syn_recipient_001",
        settlement_status=SettlementStatus.SETTLED,
        transaction_id="syn_tx_001",
        session_id="syn_session_001",
        transaction_hash="syn_hash_001",
        issued_at=NOW,
        normalization_notes=(),
    )

    assert receipt.schema_version == 1
    assert PaymentReceipt.model_validate_json(receipt.model_dump_json()) == receipt
    with pytest.raises(ValidationError):
        PaymentReceipt.model_validate({**receipt.model_dump(), "invented": True})
