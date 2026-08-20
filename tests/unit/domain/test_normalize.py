from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import JsonValue

from settlediff.domain.models import (
    ArtifactType,
    EvidenceArtifact,
    ExecutionRecord,
    ExpectedContract,
    LedgerRecord,
    LedgerStatus,
    PaymentReceipt,
    SettlementStatus,
)
from settlediff.domain.money import Money
from settlediff.domain.normalize import (
    ArtifactParseError,
    normalize_activity,
    normalize_contract,
    normalize_execution,
    normalize_receipt,
)

NOW = datetime(2026, 8, 12, 12, tzinfo=UTC)


def artifact(
    artifact_id: str,
    artifact_type: ArtifactType,
    data: JsonValue,
) -> EvidenceArtifact:
    return EvidenceArtifact(
        artifact_id=artifact_id,
        artifact_type=artifact_type,
        source="synthetic_fixture",
        collected_at=NOW,
        redacted=True,
        data=data,
    )


def test_normalize_current_perflo_contract_without_inventing_vendor_identity() -> None:
    raw = artifact(
        "artifact_contract_current",
        ArtifactType.SERVICE_CONTRACT,
        {
            "asset": "USDC",
            "chain": "tempo",
            "found": True,
            "method": "POST",
            "priceMinor": "10000",
            "requestSchema": '{"method":"POST","body":[{"name":"query","type":"string"}]}',
            "source": "curated",
            "url": "https://example.invalid/search",
        },
    )

    contract = normalize_contract(raw)

    assert contract.vendor_slug is None
    assert contract.price == Money(amount=Decimal("0.01"), unit="USDC")
    assert contract.request_schema == {
        "method": "POST",
        "body": [{"name": "query", "type": "string"}],
    }


def test_normalize_contract_maps_aliases_and_preserves_raw_evidence() -> None:
    raw_data: dict[str, JsonValue] = {
        "vendorSlug": "synthetic-search",
        "url": "https://example.invalid/search",
        "priceMinor": "10000",
        "priceMinorUnits": 6,
        "asset": "usdc",
        "protocol": "MPP",
        "chain": "Base",
        "requestSchema": {"type": "object"},
        "futureContractField": {"version": 2},
    }
    raw = artifact("artifact_contract_001", ArtifactType.SERVICE_CONTRACT, raw_data)

    contract = normalize_contract(raw)

    assert contract == ExpectedContract(
        vendor_slug="synthetic-search",
        url="https://example.invalid/search",
        price=Money(amount=Decimal("0.01"), unit="USDC"),
        asset="USDC",
        protocol="mpp",
        chain="base",
        request_schema={"type": "object"},
        normalization_notes=(),
    )
    assert raw.data["futureContractField"] == {"version": 2}  # type: ignore[index]


def test_normalize_paid_failure_keeps_settlement_separate_from_http_status() -> None:
    raw = artifact(
        "artifact_execution_001",
        ArtifactType.EXECUTION,
        {
            "vendorSlug": "synthetic-search",
            "upstreamHttpStatus": 400,
            "amountMinor": "10000",
            "amountMinorUnits": 6,
            "asset": "usdc",
            "protocol": "MPP",
            "chain": "Tempo",
            "recipient": "syn_recipient_001",
            "settlementStatus": "settled",
            "transactionId": "syn_tx_paid_failure_001",
            "sessionId": "syn_session_001",
            "transactionHash": "syn_hash_001",
            "responseBody": {"error": "synthetic malformed request"},
            "executedAt": "2026-08-12T12:00:00Z",
        },
    )

    execution = normalize_execution(raw)

    assert execution.upstream_http_status == 400
    assert execution.settlement_status is SettlementStatus.SETTLED
    assert execution.charge == Money(amount=Decimal("0.01"), unit="USDC")
    assert execution.executed_at == NOW


def test_normalize_current_execution_preserves_missing_provider_timestamp() -> None:
    raw = artifact(
        "artifact_execution_current",
        ArtifactType.EXECUTION,
        {"upstreamResponse": {"status": 200, "body": {"answer": "synthetic"}}},
    )

    execution = normalize_execution(raw)

    assert execution.executed_at is None
    assert execution.response_body is None
    assert raw.data == {"upstreamResponse": {"status": 200, "body": {"answer": "synthetic"}}}


def test_normalize_receipt_maps_only_consistency_fields() -> None:
    raw = artifact(
        "artifact_receipt_001",
        ArtifactType.PAYMENT_RECEIPT,
        {
            "amount": {"amount": "0.01", "unit": "USDC", "minor_units": None},
            "asset": "USDC",
            "protocol": "mpp",
            "chain": "tempo",
            "recipient": "syn_recipient_001",
            "settlementStatus": "settled",
            "transactionId": "syn_tx_001",
            "sessionId": "syn_session_001",
            "transactionHash": "syn_hash_001",
            "issuedAt": "2026-08-12T12:00:00+00:00",
            "opaqueReceiptMaterial": "[redacted-synthetic]",
        },
    )

    receipt = normalize_receipt(raw)

    assert receipt.amount == Money(amount=Decimal("0.01"), unit="USDC")
    assert receipt.chain == "tempo"
    assert receipt.issued_at == NOW
    assert "opaqueReceiptMaterial" in raw.data  # type: ignore[operator]


def test_normalize_activity_maps_each_candidate_without_matching_it() -> None:
    raw = artifact(
        "artifact_activity_001",
        ArtifactType.ACTIVITY,
        [
            {
                "ledgerId": "syn_ledger_001",
                "vendorSlug": "synthetic-search",
                "amount": "$0.01",
                "asset": "usdc",
                "protocol": "MPP",
                "chain": "Tempo",
                "recipient": "syn_recipient_ledger_001",
                "status": "confirmed",
                "errorReason": None,
                "transactionId": "syn_tx_001",
                "sessionId": "syn_session_001",
                "transactionHash": "syn_hash_001",
                "occurredAt": "2026-08-12T12:00:00Z",
            },
            {
                "ledgerId": "syn_ledger_002",
                "status": "pending",
                "occurredAt": "2026-08-12T12:00:01Z",
            },
        ],
    )

    records = normalize_activity(raw)

    assert len(records) == 2
    assert records[0].amount == Money(amount=Decimal("0.01"), unit="USDC")
    assert records[0].status is LedgerStatus.CONFIRMED
    assert records[1].status is LedgerStatus.PENDING


def test_normalize_current_activity_millisecond_timestamp() -> None:
    raw = artifact(
        "artifact_activity_current",
        ArtifactType.ACTIVITY,
        [
            {
                "id": "syn_ledger_current",
                "vendorSlug": "synthetic-search",
                "amount": "$0.02",
                "asset": "USDC",
                "protocol": "mpp",
                "chain": "tempo",
                "status": "confirmed",
                "txHash": "syn_hash_current",
                "createdAt": int(NOW.timestamp() * 1000),
            }
        ],
    )

    record = normalize_activity(raw)[0]

    assert record.occurred_at == NOW
    assert record.amount == Money(amount=Decimal("0.02"), unit="USDC")
    assert record.transaction_hash == "syn_hash_current"


def test_unknown_enumerated_values_are_explicit_and_diagnostic() -> None:
    raw = artifact(
        "artifact_execution_unknown",
        ArtifactType.EXECUTION,
        {
            "asset": "SYN_NEW_ASSET",
            "protocol": "syn-new-protocol",
            "chain": "syn-new-chain",
            "settlement_status": "syn-new-status",
            "executed_at": "2026-08-12T12:00:00Z",
        },
    )

    execution = normalize_execution(raw)

    assert execution.asset == "unknown"
    assert execution.protocol == "unknown"
    assert execution.chain == "unknown"
    assert execution.settlement_status is SettlementStatus.UNKNOWN
    assert execution.normalization_notes == (
        "unknown asset at data.asset",
        "unknown protocol at data.protocol",
        "unknown chain at data.chain",
        "unknown settlement status at data.settlement_status",
    )


@pytest.mark.parametrize(
    ("data", "field_path"),
    [
        (
            {
                "vendor_slug": "one",
                "vendorSlug": "two",
                "url": "https://example.invalid",
                "request_schema": {},
            },
            "data.vendor_slug",
        ),
        (
            {
                "vendor_slug": "one",
                "url": "https://example.invalid",
                "request_schema": {},
                "price_minor": "10000",
                "asset": "UNKNOWN",
            },
            "data.price_minor_units",
        ),
    ],
)
def test_contract_parse_errors_include_artifact_and_field_path(
    data: dict[str, JsonValue], field_path: str
) -> None:
    raw = artifact("artifact_bad_contract", ArtifactType.SERVICE_CONTRACT, data)

    with pytest.raises(ArtifactParseError) as error:
        normalize_contract(raw)

    assert error.value.artifact_id == "artifact_bad_contract"
    assert error.value.field_path == field_path
    assert "artifact_bad_contract" in str(error.value)
    assert field_path in str(error.value)


@given(amount=st.decimals(min_value="0", max_value="1000", allow_nan=False, allow_infinity=False))
def test_canonical_contract_normalization_is_idempotent(amount: Decimal) -> None:
    contract = ExpectedContract(
        vendor_slug="synthetic-search",
        url="https://example.invalid/search",
        price=Money(amount=amount, unit="USDC"),
        asset="USDC",
        protocol="mpp",
        chain="base",
        request_schema={"type": "object"},
        normalization_notes=(),
    )
    raw = artifact(
        "artifact_canonical_contract",
        ArtifactType.SERVICE_CONTRACT,
        contract.model_dump(mode="json"),
    )

    assert normalize_contract(raw) == contract


def test_all_canonical_normalizers_round_trip_json_values() -> None:
    execution = ExecutionRecord(
        vendor_slug=None,
        upstream_http_status=200,
        charge=None,
        asset=None,
        protocol=None,
        chain=None,
        recipient=None,
        settlement_status=SettlementStatus.UNKNOWN,
        transaction_id=None,
        session_id=None,
        transaction_hash=None,
        response_body=None,
        executed_at=NOW,
        normalization_notes=("synthetic retained note",),
    )
    receipt = PaymentReceipt(
        amount=None,
        asset=None,
        protocol=None,
        chain=None,
        recipient=None,
        settlement_status=SettlementStatus.UNKNOWN,
        transaction_id=None,
        session_id=None,
        transaction_hash=None,
        issued_at=None,
        normalization_notes=("synthetic retained note",),
    )
    ledger = LedgerRecord(
        ledger_id="syn_ledger_001",
        vendor_slug=None,
        amount=None,
        asset=None,
        protocol=None,
        chain=None,
        recipient=None,
        status=LedgerStatus.UNKNOWN,
        error_reason=None,
        transaction_id=None,
        session_id=None,
        transaction_hash=None,
        occurred_at=NOW,
        normalization_notes=("synthetic retained note",),
    )

    assert (
        normalize_execution(
            artifact(
                "artifact_execution", ArtifactType.EXECUTION, execution.model_dump(mode="json")
            )
        )
        == execution
    )
    assert (
        normalize_receipt(
            artifact(
                "artifact_receipt", ArtifactType.PAYMENT_RECEIPT, receipt.model_dump(mode="json")
            )
        )
        == receipt
    )
    assert normalize_activity(
        artifact("artifact_activity", ArtifactType.ACTIVITY, [ledger.model_dump(mode="json")])
    ) == (ledger,)
