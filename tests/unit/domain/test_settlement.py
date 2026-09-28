from __future__ import annotations

from datetime import UTC, datetime

import pytest

from settlediff.domain.models import (
    AssetIdentity,
    ExecutionRecord,
    IndependentSettlementObservation,
    IndependentSettlementStatus,
    LedgerRecord,
    LedgerStatus,
    PaymentReceipt,
    SettlementComparisonStatus,
    SettlementProfile,
    SettlementStatus,
    SettlementVerificationDimensions,
)
from settlediff.domain.settlement import compare_settlement, provider_settlement_status

NOW = datetime(2026, 9, 25, tzinfo=UTC)


def observation(
    status: IndependentSettlementStatus, diagnostic: str
) -> IndependentSettlementObservation:
    conclusive = status in {
        IndependentSettlementStatus.CONFIRMED,
        IndependentSettlementStatus.FAILED,
    }
    identity = AssetIdentity(
        symbol="USDC",
        network="eip155:84532",
        reference="syn_asset",
        decimals=6,
    )
    profile = SettlementProfile(
        network="eip155:84532",
        chain_id=84532,
        asset_identity=identity,
        atomic_amount=1000,
        recipient="syn_recipient",
        payer="syn_payer",
    )
    ledger = None
    if conclusive:
        ledger = LedgerRecord(
            ledger_id="syn_ledger",
            vendor_slug=None,
            amount=None,
            asset=None,
            protocol="x402",
            chain=None,
            recipient=None,
            scheme="exact",
            network="eip155:84532",
            asset_identity=None,
            status=(
                LedgerStatus.CONFIRMED
                if status is IndependentSettlementStatus.CONFIRMED
                else LedgerStatus.FAILED
            ),
            error_reason=None,
            transaction_id=None,
            session_id=None,
            transaction_hash="syn_transaction",
            occurred_at=NOW,
        )
    return IndependentSettlementObservation(
        status=status,
        diagnostic=diagnostic,
        source="synthetic.observer",
        observed_at=NOW,
        transaction_reference="syn_transaction",
        profile=profile,
        dimensions=SettlementVerificationDimensions(
            chain_verified=conclusive,
            asset_verified=status is IndependentSettlementStatus.CONFIRMED,
            amount_verified=status is IndependentSettlementStatus.CONFIRMED,
            recipient_verified=status is IndependentSettlementStatus.CONFIRMED,
            payer_verified=status is IndependentSettlementStatus.CONFIRMED,
        ),
        ledger=ledger,
    )


@pytest.mark.parametrize(
    ("observer_status", "observer_diagnostic", "provider", "expected", "diagnostic"),
    [
        (
            IndependentSettlementStatus.CONFIRMED,
            "EXACT_TRANSFER_CONFIRMED",
            SettlementStatus.SETTLED,
            SettlementComparisonStatus.MATCH,
            "PROVIDER_SETTLEMENT_CONFIRMED",
        ),
        (
            IndependentSettlementStatus.CONFIRMED,
            "EXACT_TRANSFER_CONFIRMED",
            SettlementStatus.FAILED,
            SettlementComparisonStatus.CONTRADICTED,
            "PROVIDER_FAILURE_TRANSFER_CONFIRMED",
        ),
        (
            IndependentSettlementStatus.FAILED,
            "RECEIPT_REVERTED",
            SettlementStatus.FAILED,
            SettlementComparisonStatus.MATCH,
            "PROVIDER_FAILURE_RECEIPT_REVERTED",
        ),
        (
            IndependentSettlementStatus.FAILED,
            "RECEIPT_REVERTED",
            SettlementStatus.SETTLED,
            SettlementComparisonStatus.CONTRADICTED,
            "PROVIDER_SETTLEMENT_RECEIPT_REVERTED",
        ),
        (
            IndependentSettlementStatus.INDETERMINATE,
            "TRANSFER_MISMATCH",
            SettlementStatus.SETTLED,
            SettlementComparisonStatus.CONTRADICTED,
            "PROVIDER_SETTLEMENT_TRANSFER_MISMATCH",
        ),
        (
            IndependentSettlementStatus.UNAVAILABLE,
            "OBSERVER_UNAVAILABLE",
            SettlementStatus.SETTLED,
            SettlementComparisonStatus.NOT_COMPARABLE,
            "NO_INDEPENDENT_SETTLEMENT_OBSERVATION",
        ),
        (
            IndependentSettlementStatus.INDETERMINATE,
            "RECEIPT_PENDING",
            SettlementStatus.SETTLED,
            SettlementComparisonStatus.NOT_COMPARABLE,
            "INDEPENDENT_OBSERVATION_INCONCLUSIVE",
        ),
        (
            IndependentSettlementStatus.CONFIRMED,
            "EXACT_TRANSFER_CONFIRMED",
            SettlementStatus.PENDING,
            SettlementComparisonStatus.NOT_COMPARABLE,
            "PROVIDER_SETTLEMENT_UNAVAILABLE",
        ),
        (
            IndependentSettlementStatus.CONFIRMED,
            "EXACT_TRANSFER_CONFIRMED",
            SettlementStatus.UNKNOWN,
            SettlementComparisonStatus.NOT_COMPARABLE,
            "PROVIDER_SETTLEMENT_UNAVAILABLE",
        ),
        (
            IndependentSettlementStatus.FAILED,
            "RECEIPT_REVERTED",
            SettlementStatus.PENDING,
            SettlementComparisonStatus.NOT_COMPARABLE,
            "PROVIDER_SETTLEMENT_UNAVAILABLE",
        ),
        (
            IndependentSettlementStatus.FAILED,
            "RECEIPT_REVERTED",
            SettlementStatus.UNKNOWN,
            SettlementComparisonStatus.NOT_COMPARABLE,
            "PROVIDER_SETTLEMENT_UNAVAILABLE",
        ),
    ],
)
def test_compare_settlement_rules(
    observer_status: IndependentSettlementStatus,
    observer_diagnostic: str,
    provider: SettlementStatus,
    expected: SettlementComparisonStatus,
    diagnostic: str,
) -> None:
    compared = compare_settlement(
        provider,
        observation(observer_status, observer_diagnostic),
        provider_evidence_ids=("provider:one",),
        observer_evidence_ids=("observer:one",),
    )

    assert compared.status is expected
    assert compared.diagnostic == diagnostic
    assert compared.provider_evidence_ids == ("provider:one",)
    assert compared.observer_evidence_ids == ("observer:one",)


def test_provider_settlement_status_prefers_receipt() -> None:
    execution = ExecutionRecord(
        vendor_slug=None,
        upstream_http_status=200,
        charge=None,
        asset=None,
        protocol="x402",
        chain=None,
        recipient=None,
        settlement_status=SettlementStatus.FAILED,
        transaction_id=None,
        session_id=None,
        transaction_hash=None,
        response_body=None,
        executed_at=NOW,
    )
    receipt = PaymentReceipt(
        amount=None,
        asset=None,
        protocol="x402",
        chain=None,
        recipient=None,
        settlement_status=SettlementStatus.SETTLED,
        transaction_id=None,
        session_id=None,
        transaction_hash=None,
        issued_at=NOW,
    )

    assert provider_settlement_status(None, None) is SettlementStatus.UNKNOWN
    assert provider_settlement_status(execution, None) is SettlementStatus.FAILED
    assert provider_settlement_status(execution, receipt) is SettlementStatus.SETTLED
