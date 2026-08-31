from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from typing import cast

import pytest
from pydantic import ValidationError

from settlediff.domain.models import (
    ArtifactType,
    AssetIdentity,
    CheckStatus,
    EvidenceArtifact,
    ExecutionRecord,
    ExpectedContract,
    ExplanationRecord,
    ExplanationSource,
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


def test_explanation_record_round_trips_with_explicit_provenance() -> None:
    explanation = InvestigationExplanation(
        run_id="run_syn_001",
        summary="Deterministic verification produced a warning.",
        evidence_used=("artifact_execution_001",),
        finding_ids=("finding_chain_001",),
        deterministic_verdict=Verdict.VERIFIED_WITH_WARNINGS,
        recommended_next_step=None,
    )
    record = ExplanationRecord(
        explanation=explanation,
        source=ExplanationSource.PROVIDER,
        tool_calls=2,
    )

    restored = ExplanationRecord.model_validate_json(record.model_dump_json())

    assert restored == record
    assert restored.source is ExplanationSource.PROVIDER
    assert restored.tool_calls == 2


def test_explanation_record_usage_defaults_support_existing_rows() -> None:
    explanation = InvestigationExplanation(
        run_id="run_syn_001",
        summary="Deterministic verification completed.",
        evidence_used=(),
        finding_ids=(),
        deterministic_verdict=Verdict.VERIFIED,
        recommended_next_step=None,
    )
    existing_json = (
        '{"schema_version":1,"explanation":'
        + explanation.model_dump_json()
        + ',"source":"fallback","tool_calls":0}'
    )

    restored = ExplanationRecord.model_validate_json(existing_json)

    assert restored.model_requests == 0
    assert restored.input_tokens == 0
    assert restored.output_tokens == 0
    assert restored.model_cost is None
    assert restored.rejected_output is None


def test_explanation_record_usage_is_bounded_and_uses_decimal_cost() -> None:
    explanation = InvestigationExplanation(
        run_id="run_syn_001",
        summary="Deterministic verification completed.",
        evidence_used=(),
        finding_ids=(),
        deterministic_verdict=Verdict.VERIFIED,
        recommended_next_step=None,
    )
    record = ExplanationRecord(
        explanation=explanation,
        source=ExplanationSource.PROVIDER,
        tool_calls=1,
        model_requests=2,
        input_tokens=123,
        output_tokens=45,
        model_cost=Decimal("0.0012"),
        rejected_output="redacted diagnostic",
    )

    restored = ExplanationRecord.model_validate_json(record.model_dump_json())

    assert restored.model_cost == Decimal("0.0012")
    with pytest.raises(ValidationError):
        ExplanationRecord(
            explanation=explanation,
            source=ExplanationSource.PROVIDER,
            tool_calls=0,
            model_requests=11,
        )
    with pytest.raises(ValidationError):
        ExplanationRecord.model_validate({**record.model_dump(), "rejected_output": "x" * 2049})


def test_finding_money_round_trips_as_money_not_generic_json() -> None:
    finding = Finding(
        finding_id="finding_budget_001",
        check_id="budget",
        severity=Severity.INFO,
        status=CheckStatus.PASS,
        expected=Money(amount=Decimal("0.05"), unit="USDC"),
        observed=Money(amount=Decimal("0.01"), unit="USDC"),
        message="Execution charge is within budget.",
        artifact_ids=("artifact_execution_001",),
        field_paths=("execution.charge",),
    )

    restored = Finding.model_validate_json(finding.model_dump_json())

    assert restored == finding
    assert isinstance(restored.expected, Money)
    assert isinstance(restored.observed, Money)


def test_asset_identity_requires_lossless_network_bound_identity() -> None:
    identity = AssetIdentity(
        symbol="USDC",
        network="eip155:84532",
        reference="0x036CbD53842c5426634e7929541eC2318f3dCF7e",
        decimals=6,
    )

    assert identity.schema_version == 1
    assert AssetIdentity.model_validate_json(identity.model_dump_json()) == identity
    for invalid_network in ("base-sepolia", "eip155:", ":84532", "EIP155:84532"):
        with pytest.raises(ValidationError):
            AssetIdentity(
                symbol="USDC",
                network=invalid_network,
                reference="0x036CbD53842c5426634e7929541eC2318f3dCF7e",
                decimals=6,
            )
    for invalid_decimals in (-1, 256, 6.0, True):
        with pytest.raises(ValidationError):
            AssetIdentity(
                symbol="USDC",
                network="eip155:84532",
                reference="0x036CbD53842c5426634e7929541eC2318f3dCF7e",
                decimals=invalid_decimals,  # type: ignore[arg-type]
            )
    with pytest.raises(ValidationError):
        AssetIdentity.model_validate({**identity.model_dump(), "invented": True})


def test_schema_v2_records_carry_rail_neutral_payment_evidence() -> None:
    identity = AssetIdentity(
        symbol="USDC",
        network="eip155:84532",
        reference="0x036CbD53842c5426634e7929541eC2318f3dCF7e",
        decimals=6,
    )
    contract = ExpectedContract.model_validate(
        {
            **contract_fixture().model_dump(),
            "schema_version": 2,
            "scheme": "exact",
            "network": "eip155:84532",
            "asset_identity": identity,
            "recipient": "0x1111111111111111111111111111111111111111",
        }
    )
    execution = ExecutionRecord.model_validate(
        {
            **execution_fixture().model_dump(),
            "schema_version": 2,
            "scheme": "exact",
            "network": "eip155:84532",
            "asset_identity": identity,
        }
    )
    ledger = LedgerRecord.model_validate(
        {
            **ledger_fixture().model_dump(),
            "schema_version": 2,
            "scheme": "exact",
            "network": "eip155:84532",
            "asset_identity": identity,
        }
    )
    receipt = PaymentReceipt(
        amount=Money(amount=Decimal("0.001"), unit="USDC"),
        asset="USDC",
        asset_identity=identity,
        protocol="x402",
        scheme="exact",
        chain=None,
        network="eip155:84532",
        recipient="0x1111111111111111111111111111111111111111",
        settlement_status=SettlementStatus.SETTLED,
        transaction_id=None,
        session_id=None,
        transaction_hash="0x2222222222222222222222222222222222222222222222222222222222222222",
        issued_at=NOW,
    )
    report = MachineReport(
        run_id="run_syn_001",
        intent=intent_fixture(),
        contract=contract,
        execution=execution,
        receipt=receipt,
        ledger=ledger,
        findings=(finding_fixture(),),
        verdict=Verdict.VERIFIED_WITH_WARNINGS,
    )

    assert contract.schema_version == 2
    assert contract.network == "eip155:84532"
    assert contract.asset_identity == identity
    assert contract.recipient == "0x1111111111111111111111111111111111111111"
    assert execution.network == "eip155:84532"
    assert execution.asset_identity == identity
    assert ledger.network == "eip155:84532"
    assert ledger.asset_identity == identity
    assert receipt.schema_version == 2
    assert report.schema_version == 2
    assert report.receipt == receipt


def test_schema_v1_report_remains_readable_without_v2_fields() -> None:
    legacy = machine_report_fixture().model_dump(mode="json")
    legacy["schema_version"] = 1
    legacy.pop("receipt", None)
    for name in ("contract", "execution", "ledger"):
        record = cast(dict[str, object], legacy[name])
        record["schema_version"] = 1
        for field in ("scheme", "network", "asset_identity"):
            record.pop(field, None)
    contract = cast(dict[str, object], legacy["contract"])
    contract.pop("recipient", None)

    restored = MachineReport.model_validate_json(json.dumps(legacy))

    assert restored.schema_version == 1
    assert restored.receipt is None
    assert restored.contract is not None
    assert restored.contract.network is None
    assert restored.contract.asset_identity is None
    assert restored.contract.recipient is None

    invalid_contract = dict(contract)
    invalid_contract["network"] = "eip155:84532"
    with pytest.raises(ValidationError, match="schema version 1"):
        ExpectedContract.model_validate_json(json.dumps(invalid_contract))
    invalid_report = dict(legacy)
    invalid_report["receipt"] = PaymentReceipt(
        amount=None,
        asset=None,
        protocol="x402",
        chain=None,
        recipient=None,
        settlement_status=SettlementStatus.UNKNOWN,
        transaction_id=None,
        session_id=None,
        transaction_hash=None,
        issued_at=None,
    ).model_dump(mode="json")
    with pytest.raises(ValidationError, match="schema version 1"):
        MachineReport.model_validate_json(json.dumps(invalid_report))


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

    assert receipt.schema_version == 2
    assert PaymentReceipt.model_validate_json(receipt.model_dump_json()) == receipt
    with pytest.raises(ValidationError):
        PaymentReceipt.model_validate({**receipt.model_dump(), "invented": True})
