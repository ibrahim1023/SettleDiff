from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import cast

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import JsonValue

from settlediff.domain.models import (
    ArtifactType,
    AssetIdentity,
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
    assert execution.upstream_http_status == 200
    assert execution.response_body == {"answer": "synthetic"}
    assert raw.data == {"upstreamResponse": {"status": 200, "body": {"answer": "synthetic"}}}


def test_normalize_execution_rejects_conflicting_upstream_response_fields() -> None:
    raw = artifact(
        "artifact_execution_conflict",
        ArtifactType.EXECUTION,
        {
            "upstreamHttpStatus": 200,
            "responseBody": {"answer": "top-level"},
            "upstreamResponse": {"status": 500, "body": {"answer": "nested"}},
        },
    )

    with pytest.raises(ArtifactParseError, match="conflicting documented fields") as error:
        normalize_execution(raw)

    assert error.value.field_path == "data.upstream_http_status"


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


def test_normalize_canonical_v2_payment_evidence() -> None:
    identity_data: dict[str, JsonValue] = {
        "schema_version": 1,
        "symbol": "USDC",
        "network": "eip155:84532",
        "reference": "syn_usdc_base_sepolia",
        "decimals": 6,
    }
    identity = AssetIdentity.model_validate(identity_data)
    contract = normalize_contract(
        artifact(
            "artifact_contract_v2",
            ArtifactType.SERVICE_CONTRACT,
            {
                "url": "https://example.invalid/weather",
                "price": {"amount": "0.001", "unit": "USDC"},
                "asset": "USDC",
                "protocol": "x402",
                "chain": None,
                "request_schema": {},
                "scheme": "exact",
                "network": "eip155:84532",
                "asset_identity": identity_data,
                "recipient": "syn_recipient",
            },
        )
    )
    execution = normalize_execution(
        artifact(
            "artifact_execution_v2",
            ArtifactType.EXECUTION,
            {
                "upstream_http_status": 200,
                "charge": {"amount": "0.001", "unit": "USDC"},
                "asset": "USDC",
                "protocol": "x402",
                "chain": None,
                "recipient": "syn_recipient",
                "scheme": "exact",
                "network": "eip155:84532",
                "asset_identity": identity_data,
                "settlement_status": "unknown",
            },
        )
    )
    receipt = normalize_receipt(
        artifact(
            "artifact_receipt_v2",
            ArtifactType.PAYMENT_RECEIPT,
            {
                "amount": {"amount": "0.001", "unit": "USDC"},
                "asset": "USDC",
                "protocol": "x402",
                "chain": None,
                "recipient": "syn_recipient",
                "scheme": "exact",
                "network": "eip155:84532",
                "asset_identity": identity_data,
                "settlement_status": "settled",
            },
        )
    )
    ledger = normalize_activity(
        artifact(
            "artifact_activity_v2",
            ArtifactType.ACTIVITY,
            [
                {
                    "ledger_id": "syn_ledger",
                    "amount": {"amount": "0.001", "unit": "USDC"},
                    "asset": "USDC",
                    "protocol": "x402",
                    "chain": None,
                    "recipient": "syn_recipient",
                    "scheme": "exact",
                    "network": "eip155:84532",
                    "asset_identity": identity_data,
                    "status": "confirmed",
                    "occurred_at": "2026-08-12T12:00:00Z",
                }
            ],
        )
    )[0]

    assert contract.protocol == execution.protocol == receipt.protocol == ledger.protocol == "x402"
    assert contract.scheme == execution.scheme == receipt.scheme == ledger.scheme == "exact"
    assert contract.network == execution.network == receipt.network == ledger.network
    assert contract.asset_identity == execution.asset_identity == receipt.asset_identity == identity
    assert ledger.asset_identity == identity
    assert contract.recipient == "syn_recipient"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("network", "base-sepolia"),
        (
            "asset_identity",
            {
                "schema_version": 1,
                "symbol": "USDC",
                "network": "base-sepolia",
                "reference": "syn_usdc",
                "decimals": 6,
            },
        ),
    ],
)
def test_normalize_rejects_invalid_v2_identity_fields(field: str, value: JsonValue) -> None:
    data: dict[str, JsonValue] = {
        "url": "https://example.invalid/weather",
        "request_schema": {},
        field: value,
    }

    with pytest.raises(ArtifactParseError, match=field):
        normalize_contract(
            artifact("artifact_contract_invalid_v2", ArtifactType.SERVICE_CONTRACT, data)
        )


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


@pytest.mark.parametrize(
    ("raw_status", "expected"),
    [("broadcast", LedgerStatus.PENDING), ("broadcast_failed", LedgerStatus.FAILED)],
)
def test_normalize_perflo_broadcast_statuses(raw_status: str, expected: LedgerStatus) -> None:
    raw = artifact(
        "artifact_activity_broadcast",
        ArtifactType.ACTIVITY,
        [
            {
                "id": "syn_activity_broadcast",
                "amount": "$0.01",
                "asset": "USDC",
                "status": raw_status,
                "createdAt": int(NOW.timestamp() * 1000),
            }
        ],
    )

    record = normalize_activity(raw)[0]

    assert record.status is expected
    assert record.amount == Money(amount=Decimal("0.01"), unit="USDC")


def test_unknown_bounded_values_are_diagnostic_without_restricting_protocols() -> None:
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
    assert execution.protocol == "syn-new-protocol"
    assert execution.chain == "unknown"
    assert execution.settlement_status is SettlementStatus.UNKNOWN
    assert execution.normalization_notes == (
        "unknown asset at data.asset",
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


PERFLO_FIXTURES = Path("tests/contract/perflo")
TX_HASH = "0x1111111111111111111111111111111111111111111111111111111111111111"


def perflo_fixture(name: str) -> dict[str, JsonValue]:
    return cast(dict[str, JsonValue], json.loads((PERFLO_FIXTURES / name).read_text()))


def test_normalize_v8_vendor_maps_catalog_contract() -> None:
    raw = artifact(
        "artifact_vendor_v8",
        ArtifactType.SERVICE_CONTRACT,
        perflo_fixture("vendor.json")["vendor"],
    )

    contract = normalize_contract(raw)

    assert contract.schema_version == 4
    assert contract.vendor_slug == "synthetic-weather"
    assert contract.url is None
    assert contract.price == Money(amount=Decimal("0.01"), unit="USD")
    assert contract.required_max_charge == Money(amount=Decimal("0.05"), unit="USD")
    assert contract.request_schema == {
        "fields": [
            {
                "name": "city",
                "in": "body",
                "type": "string",
                "required": True,
                "description": "Synthetic city",
            },
            {
                "name": "units",
                "in": "query",
                "type": "string",
                "required": False,
                "description": None,
            },
        ],
        "example": {"city": "Exampleville"},
    }
    assert contract.asset is None
    assert contract.response_contract is None


def test_normalize_v8_execution_maps_wallet_settlement() -> None:
    raw = artifact(
        "artifact_pay_v8",
        ArtifactType.EXECUTION,
        perflo_fixture("pay_wallet_success.json")["result"],
    )

    execution = normalize_execution(raw)

    assert execution.vendor_slug == "synthetic-weather"
    assert execution.charge == Money(amount=Decimal("0.01"), unit="USD")
    assert execution.upstream_http_status == 200
    assert execution.settlement_status is SettlementStatus.SETTLED
    assert execution.chain == "base"
    assert execution.transaction_hash == TX_HASH
    assert execution.transaction_id == "syn_tx_wallet_001"
    assert execution.response_body == {"weather": "synthetic-sunny"}
    assert execution.executed_at == datetime(2026, 9, 10, 10, 0, 2, tzinfo=UTC)
    assert execution.protocol is None
    assert execution.recipient is None
    assert execution.asset is None


def test_normalize_v8_credit_execution_never_invents_transfer_facts() -> None:
    raw = artifact(
        "artifact_pay_credit",
        ArtifactType.EXECUTION,
        perflo_fixture("pay_credit_success.json")["result"],
    )

    execution = normalize_execution(raw)

    assert execution.charge == Money(amount=Decimal("0.01"), unit="USD")
    assert execution.settlement_status is SettlementStatus.SETTLED
    assert execution.transaction_id == "syn_tx_credit_001"
    assert execution.transaction_hash is None
    assert execution.chain is None
    assert execution.asset is None
    assert execution.recipient is None


def test_normalize_v8_failed_execution_preserves_bounded_failure() -> None:
    raw = artifact(
        "artifact_pay_failed",
        ArtifactType.EXECUTION,
        perflo_fixture("pay_failed.json")["result"],
    )

    execution = normalize_execution(raw)

    assert execution.settlement_status is SettlementStatus.FAILED
    assert execution.upstream_http_status == 500
    assert execution.response_body == {"error": "synthetic-paid-failure"}
    assert execution.transaction_hash is None


@pytest.mark.parametrize(
    ("name", "status"),
    [
        ("task_running.json", SettlementStatus.UNKNOWN),
        ("task_indeterminate.json", SettlementStatus.UNKNOWN),
        ("task_failed.json", SettlementStatus.UNKNOWN),
    ],
)
def test_normalize_v8_task_results_leave_optional_fields_absent(
    name: str, status: SettlementStatus
) -> None:
    raw = artifact("artifact_task", ArtifactType.EXECUTION, perflo_fixture(name)["result"])

    execution = normalize_execution(raw)

    assert execution.settlement_status is status
    assert execution.charge is None
    assert execution.transaction_hash is None
    assert execution.executed_at is None


def test_normalize_v8_activity_maps_ledger_states_and_money_objects() -> None:
    raw = artifact(
        "artifact_activity_v8",
        ArtifactType.ACTIVITY,
        perflo_fixture("activity.json")["agent"],
    )

    records = normalize_activity(raw)

    assert [record.status for record in records] == [
        LedgerStatus.CONFIRMED,
        LedgerStatus.PENDING,
        LedgerStatus.FAILED,
    ]
    posted, pending, voided = records
    assert posted.ledger_id == "syn_activity_posted"
    assert posted.vendor_slug == "synthetic-weather"
    assert posted.amount == Money(amount=Decimal("-0.01"), unit="USD")
    assert posted.transaction_hash == TX_HASH
    assert posted.transaction_id is None
    assert posted.occurred_at == datetime(2026, 9, 10, 10, 0, 2, tzinfo=UTC)
    assert pending.transaction_hash is None
    assert voided.transaction_hash is None
    assert posted.asset is None


def test_normalize_activity_rejects_malformed_v8_agent_object() -> None:
    raw = artifact(
        "artifact_activity_bad",
        ArtifactType.ACTIVITY,
        {"rows": "not-a-list", "meta": {}},
    )

    with pytest.raises(ArtifactParseError):
        normalize_activity(raw)


def test_normalize_v8_object_currency_does_not_create_asset_identity() -> None:
    raw = artifact(
        "artifact_contract_currency",
        ArtifactType.SERVICE_CONTRACT,
        {
            "slug": "synthetic-weather",
            "price": {"amount": "0.01", "currency": "USD"},
        },
    )

    contract = normalize_contract(raw)

    assert contract.price == Money(amount=Decimal("0.01"), unit="USD")
    assert contract.asset is None
    assert contract.asset_identity is None


def test_normalize_rejects_malformed_money_object() -> None:
    raw = artifact(
        "artifact_contract_bad_money",
        ArtifactType.SERVICE_CONTRACT,
        {
            "slug": "synthetic-weather",
            "price": {"amount": "0.01", "currency": "USD", "extra": True},
        },
    )

    with pytest.raises(ArtifactParseError):
        normalize_contract(raw)


def test_normalize_rejects_money_object_with_extra_keys() -> None:
    raw = artifact(
        "artifact_money_extra",
        ArtifactType.SERVICE_CONTRACT,
        {
            "slug": "synthetic-weather",
            "price": {"amount": "0.01", "currency": "USD", "fee": "0.001"},
        },
    )

    with pytest.raises(ArtifactParseError):
        normalize_contract(raw)


def test_normalize_rejects_mixed_scalar_and_object_money() -> None:
    raw = artifact(
        "artifact_money_mixed",
        ArtifactType.SERVICE_CONTRACT,
        {
            "slug": "synthetic-weather",
            "price": {"amount": "0.01", "currency": "USD"},
            "priceMinor": "100",
            "asset": "USD",
        },
    )

    with pytest.raises(ArtifactParseError, match="cannot mix"):
        normalize_contract(raw)


def test_normalize_keeps_legacy_money_object_compatibility() -> None:
    raw = artifact(
        "artifact_money_legacy",
        ArtifactType.SERVICE_CONTRACT,
        {
            "url": "https://example.invalid/search",
            "price": {"amount": "0.01", "unit": "USDC"},
            "asset": "USDC",
        },
    )

    contract = normalize_contract(raw)

    assert contract.price == Money(amount=Decimal("0.01"), unit="USDC")


def test_contract_digest_ignores_unnormalized_vendor_source_fields() -> None:
    vendor = perflo_fixture("vendor.json")["vendor"]
    first = normalize_contract(artifact("artifact_digest_a", ArtifactType.SERVICE_CONTRACT, vendor))
    same = normalize_contract(artifact("artifact_digest_b", ArtifactType.SERVICE_CONTRACT, vendor))
    assert first.digest == same.digest

    assert isinstance(vendor, dict)
    noisy: dict[str, JsonValue] = {
        **vendor,
        "name": "Renamed Vendor",
        "latencyMsP50": 9999,
        "futureField": {"changed": True},
    }
    noisy_contract = normalize_contract(
        artifact("artifact_digest_noisy", ArtifactType.SERVICE_CONTRACT, noisy)
    )
    assert noisy_contract.digest == first.digest

    drifted: dict[str, JsonValue] = {
        **vendor,
        "maxChargePerCall": {"amount": "0.09", "currency": "USD"},
    }
    drifted_contract = normalize_contract(
        artifact("artifact_digest_drifted", ArtifactType.SERVICE_CONTRACT, drifted)
    )
    assert drifted_contract.digest != first.digest
