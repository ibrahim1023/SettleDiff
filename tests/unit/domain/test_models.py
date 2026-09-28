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
    IndependentSettlementObservation,
    IndependentSettlementStatus,
    InvestigationExplanation,
    LedgerRecord,
    LedgerStatus,
    MachineReport,
    PayerValidationPolicy,
    PaymentReceipt,
    PurchaseIntent,
    SettlementComparison,
    SettlementComparisonStatus,
    SettlementProfile,
    SettlementStatus,
    SettlementVerificationDimensions,
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
            "max_timeout_seconds": 300,
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
    assert contract.max_timeout_seconds == 300
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
    legacy.pop("adapter_id", None)
    for name in ("contract", "execution", "ledger"):
        record = cast(dict[str, object], legacy[name])
        record["schema_version"] = 1
        for field in ("scheme", "network", "asset_identity"):
            record.pop(field, None)
    contract = cast(dict[str, object], legacy["contract"])
    contract.pop("recipient", None)
    contract.pop("max_timeout_seconds", None)

    restored = MachineReport.model_validate_json(json.dumps(legacy))

    assert restored.schema_version == 1
    assert restored.receipt is None
    assert restored.adapter_id is None
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


def test_contract_schema_below_4_requires_a_url() -> None:
    values = contract_fixture().model_dump()
    values["schema_version"] = 2
    values["url"] = None

    with pytest.raises(ValidationError, match="requires a resource URL"):
        ExpectedContract.model_validate(values)


def test_contract_schema_4_may_omit_url_for_catalog_resources() -> None:
    values = contract_fixture().model_dump()
    values["schema_version"] = 4
    values["url"] = None
    values["required_max_charge"] = Money(amount=Decimal("0.05"), unit="USDC")
    values["payable"] = True

    contract = ExpectedContract.model_validate(values)

    assert contract.schema_version == 4
    assert contract.url is None
    assert contract.payable is True


def test_contract_schema_4_requires_an_identity() -> None:
    values = contract_fixture().model_dump()
    values["schema_version"] = 4
    values["url"] = None
    values["vendor_slug"] = None

    with pytest.raises(ValidationError, match="identity"):
        ExpectedContract.model_validate(values)


def test_contract_schema_4_url_without_vendor_slug_is_valid() -> None:
    values = contract_fixture().model_dump()
    values["schema_version"] = 4
    values["vendor_slug"] = None

    contract = ExpectedContract.model_validate(values)

    assert contract.url is not None
    assert contract.vendor_slug is None


def settlement_profile_payload(**updates: object) -> dict[str, object]:
    values: dict[str, object] = {
        "network": "eip155:84532",
        "chain_id": 84532,
        "asset_identity": AssetIdentity(
            symbol="USDC",
            network="eip155:84532",
            reference="0x036CbD53842c5426634e7929541eC2318f3dCF7e",
            decimals=6,
        ).model_dump(mode="json"),
        "atomic_amount": 10000,
        "recipient": "0x1111111111111111111111111111111111111111",
        "payer": "0x2222222222222222222222222222222222222222",
        "payer_policy": "REQUIRED",
    }
    return values | updates


def settlement_dimensions_payload(**updates: object) -> dict[str, object]:
    values: dict[str, object] = {
        "chain_verified": True,
        "asset_verified": True,
        "amount_verified": True,
        "recipient_verified": True,
        "payer_verified": True,
        "payer_policy": "REQUIRED",
    }
    return values | updates


def settlement_observation_payload(**updates: object) -> dict[str, object]:
    values: dict[str, object] = {
        "status": "CONFIRMED",
        "diagnostic": "EXACT_TRANSFER_CONFIRMED",
        "source": "rpc:base-sepolia",
        "observed_at": NOW.isoformat(),
        "transaction_reference": "0x" + "2" * 64,
        "profile": settlement_profile_payload(),
        "dimensions": settlement_dimensions_payload(),
        "ledger": ledger_fixture().model_dump(mode="json"),
    }
    return values | updates


def settlement_comparison_payload(**updates: object) -> dict[str, object]:
    values: dict[str, object] = {
        "status": "MATCH",
        "diagnostic": "INDEPENDENT_OBSERVATION_MATCHES_PROVIDER",
        "provider_evidence_ids": ("artifact:provider-activity",),
        "observer_evidence_ids": ("artifact:independent-settlement",),
    }
    return values | updates


def schema4_report_payload(**updates: object) -> dict[str, object]:
    ledger = ledger_fixture().model_dump(mode="json")
    provider_activity = LedgerRecord(
        ledger_id="syn_activity_001",
        vendor_slug="synthetic-search",
        amount=Money(amount=Decimal("-0.01"), unit="USDC"),
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
    ).model_dump(mode="json")
    payload = machine_report_fixture().model_dump(mode="json")
    payload["schema_version"] = 4
    payload["ledger"] = ledger
    payload["provider_activity"] = provider_activity
    payload["independent_settlement"] = settlement_observation_payload(ledger=ledger)
    payload["settlement_comparison"] = settlement_comparison_payload()
    return payload | updates


def test_schema_v4_report_carries_settlement_provenance() -> None:
    payload = schema4_report_payload()

    report = MachineReport.model_validate_json(json.dumps(payload))

    assert report.schema_version == 4
    assert report.provider_activity is not None
    assert report.provider_activity.ledger_id == "syn_activity_001"
    observation = report.independent_settlement
    assert observation is not None
    assert observation.status is IndependentSettlementStatus.CONFIRMED
    assert observation.profile is not None
    assert observation.profile.payer_policy is PayerValidationPolicy.REQUIRED
    assert observation.dimensions == SettlementVerificationDimensions(
        chain_verified=True,
        asset_verified=True,
        amount_verified=True,
        recipient_verified=True,
        payer_verified=True,
    )
    assert report.settlement_comparison is not None
    assert report.settlement_comparison.status is SettlementComparisonStatus.MATCH
    assert report.ledger is not None and report.ledger == observation.ledger

    restored = MachineReport.model_validate_json(report.model_dump_json())
    assert restored == report
    dumped = report.model_dump(mode="json")
    assert dumped["provider_activity"]["ledger_id"] == "syn_activity_001"
    assert dumped["independent_settlement"]["status"] == "CONFIRMED"
    assert dumped["settlement_comparison"]["status"] == "MATCH"


def test_schema_v4_report_with_unavailable_observation_is_not_comparable() -> None:
    payload = schema4_report_payload(
        ledger=None,
        provider_activity=None,
        independent_settlement=settlement_observation_payload(
            status="UNAVAILABLE",
            diagnostic="SETTLEMENT_PROFILE_UNAVAILABLE",
            transaction_reference=None,
            profile=None,
            dimensions=settlement_dimensions_payload(
                chain_verified=False,
                asset_verified=False,
                amount_verified=False,
                recipient_verified=False,
                payer_verified=False,
            ),
            ledger=None,
        ),
        settlement_comparison=settlement_comparison_payload(
            status="NOT_COMPARABLE",
            diagnostic="NO_INDEPENDENT_SETTLEMENT_OBSERVATION",
            observer_evidence_ids=(),
        ),
    )

    report = MachineReport.model_validate_json(json.dumps(payload))

    assert report.schema_version == 4
    assert report.provider_activity is None
    assert "provider_activity" not in report.model_dump(mode="json")
    observation = report.independent_settlement
    assert observation is not None
    assert observation.status is IndependentSettlementStatus.UNAVAILABLE
    assert observation.profile is None
    assert observation.ledger is None
    assert report.settlement_comparison is not None
    assert report.settlement_comparison.status is SettlementComparisonStatus.NOT_COMPARABLE


@pytest.mark.parametrize("schema_version", [1, 2, 3])
@pytest.mark.parametrize(
    "field",
    ["provider_activity", "independent_settlement", "settlement_comparison"],
)
def test_schemas_below_4_reject_settlement_fields_even_when_null(
    schema_version: int, field: str
) -> None:
    payload = machine_report_fixture().model_dump(mode="json")
    payload["schema_version"] = schema_version
    payload[field] = None

    with pytest.raises(ValidationError, match=f"schema version {schema_version}"):
        MachineReport.model_validate_json(json.dumps(payload))


@pytest.mark.parametrize("field", ["independent_settlement", "settlement_comparison"])
def test_schema_v4_requires_settlement_fields(field: str) -> None:
    for removal in ("missing", "null"):
        payload = schema4_report_payload()
        if removal == "missing":
            payload.pop(field)
        else:
            payload[field] = None
        with pytest.raises(ValidationError, match="schema version 4"):
            MachineReport.model_validate_json(json.dumps(payload))


def test_schema_v4_report_ledger_must_match_independent_observation() -> None:
    payload = schema4_report_payload(
        ledger=ledger_fixture().model_dump(mode="json") | {"ledger_id": "syn_ledger_other"},
    )

    with pytest.raises(ValidationError, match="ledger"):
        MachineReport.model_validate_json(json.dumps(payload))


@pytest.mark.parametrize(
    "dimension",
    [
        "chain_verified",
        "asset_verified",
        "amount_verified",
        "recipient_verified",
        "payer_verified",
    ],
)
def test_confirmed_observation_rejects_any_unverified_dimension(dimension: str) -> None:
    payload = settlement_observation_payload(
        dimensions=settlement_dimensions_payload(**{dimension: False})
    )

    with pytest.raises(ValidationError, match="confirmed"):
        IndependentSettlementObservation.model_validate_json(json.dumps(payload))


@pytest.mark.parametrize("missing", ["profile", "ledger"])
def test_confirmed_observation_requires_profile_and_ledger(missing: str) -> None:
    payload = settlement_observation_payload(**{missing: None})

    with pytest.raises(ValidationError, match="confirmed"):
        IndependentSettlementObservation.model_validate_json(json.dumps(payload))


def test_confirmed_observation_rejects_non_confirmed_ledger() -> None:
    payload = settlement_observation_payload(
        ledger=ledger_fixture().model_dump(mode="json") | {"status": "pending"}
    )

    with pytest.raises(ValidationError, match="confirmed"):
        IndependentSettlementObservation.model_validate_json(json.dumps(payload))


def test_failed_observation_accepts_failed_ledger() -> None:
    payload = settlement_observation_payload(
        status="FAILED",
        diagnostic="EXTERNAL_LEDGER_FAILED",
        ledger=ledger_fixture().model_dump(mode="json") | {"status": "failed"},
    )

    observation = IndependentSettlementObservation.model_validate_json(json.dumps(payload))

    assert observation.status is IndependentSettlementStatus.FAILED
    assert observation.ledger is not None
    assert observation.ledger.status is LedgerStatus.FAILED


@pytest.mark.parametrize("ledger_update", [None, {"status": "confirmed"}, {"status": "pending"}])
def test_failed_observation_requires_failed_ledger(
    ledger_update: dict[str, str] | None,
) -> None:
    ledger: dict[str, object] | None = None
    if ledger_update is not None:
        ledger = ledger_fixture().model_dump(mode="json") | ledger_update
    payload = settlement_observation_payload(status="FAILED", ledger=ledger)

    with pytest.raises(ValidationError, match="failed"):
        IndependentSettlementObservation.model_validate_json(json.dumps(payload))


@pytest.mark.parametrize("status", ["INDETERMINATE", "UNAVAILABLE"])
def test_non_conclusive_observation_rejects_ledger_evidence(status: str) -> None:
    payload = settlement_observation_payload(status=status)

    with pytest.raises(ValidationError, match="non-conclusive"):
        IndependentSettlementObservation.model_validate_json(json.dumps(payload))


@pytest.mark.parametrize(
    "updates",
    [
        {
            "asset_identity": AssetIdentity(
                symbol="USDC",
                network="eip155:1",
                reference="0x036CbD53842c5426634e7929541eC2318f3dCF7e",
                decimals=6,
            ).model_dump(mode="json")
        },
        {"atomic_amount": 0},
        {"atomic_amount": -5},
        {"chain_id": 0},
    ],
)
def test_settlement_profile_rejects_incoherent_identity(
    updates: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        SettlementProfile.model_validate_json(json.dumps(settlement_profile_payload(**updates)))


def test_settlement_provenance_rejects_unknown_fields_and_payer_relaxation() -> None:
    with pytest.raises(ValidationError):
        SettlementProfile.model_validate_json(json.dumps(settlement_profile_payload(invented=True)))
    with pytest.raises(ValidationError):
        IndependentSettlementObservation.model_validate_json(
            json.dumps(settlement_observation_payload(invented=True))
        )
    with pytest.raises(ValidationError):
        SettlementComparison.model_validate_json(
            json.dumps(settlement_comparison_payload(invented=True))
        )
    with pytest.raises(ValidationError):
        SettlementProfile.model_validate_json(
            json.dumps(settlement_profile_payload(payer_policy="OPTIONAL"))
        )
    with pytest.raises(ValidationError):
        SettlementVerificationDimensions.model_validate_json(
            json.dumps(settlement_dimensions_payload(payer_policy="OPTIONAL"))
        )
