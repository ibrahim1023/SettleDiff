"""Pure settlement provenance comparison."""

from __future__ import annotations

from settlediff.domain.models import (
    ExecutionRecord,
    IndependentSettlementObservation,
    IndependentSettlementStatus,
    PaymentReceipt,
    SettlementComparison,
    SettlementComparisonStatus,
    SettlementStatus,
)


def provider_settlement_status(
    execution: ExecutionRecord | None, receipt: PaymentReceipt | None
) -> SettlementStatus:
    if receipt is not None:
        return receipt.settlement_status
    return execution.settlement_status if execution is not None else SettlementStatus.UNKNOWN


def compare_settlement(
    provider_status: SettlementStatus,
    observation: IndependentSettlementObservation,
    *,
    provider_evidence_ids: tuple[str, ...],
    observer_evidence_ids: tuple[str, ...],
) -> SettlementComparison:
    if observation.status is IndependentSettlementStatus.UNAVAILABLE:
        status = SettlementComparisonStatus.NOT_COMPARABLE
        diagnostic = "NO_INDEPENDENT_SETTLEMENT_OBSERVATION"
    elif observation.status is IndependentSettlementStatus.INDETERMINATE:
        if (
            observation.diagnostic == "TRANSFER_MISMATCH"
            and provider_status is SettlementStatus.SETTLED
        ):
            status = SettlementComparisonStatus.CONTRADICTED
            diagnostic = "PROVIDER_SETTLEMENT_TRANSFER_MISMATCH"
        else:
            status = SettlementComparisonStatus.NOT_COMPARABLE
            diagnostic = "INDEPENDENT_OBSERVATION_INCONCLUSIVE"
    elif provider_status in {SettlementStatus.PENDING, SettlementStatus.UNKNOWN}:
        status = SettlementComparisonStatus.NOT_COMPARABLE
        diagnostic = "PROVIDER_SETTLEMENT_UNAVAILABLE"
    elif observation.status is IndependentSettlementStatus.CONFIRMED:
        if provider_status is SettlementStatus.SETTLED:
            status = SettlementComparisonStatus.MATCH
            diagnostic = "PROVIDER_SETTLEMENT_CONFIRMED"
        else:
            status = SettlementComparisonStatus.CONTRADICTED
            diagnostic = "PROVIDER_FAILURE_TRANSFER_CONFIRMED"
    elif provider_status is SettlementStatus.FAILED:
        status = SettlementComparisonStatus.MATCH
        diagnostic = "PROVIDER_FAILURE_RECEIPT_REVERTED"
    else:
        status = SettlementComparisonStatus.CONTRADICTED
        diagnostic = "PROVIDER_SETTLEMENT_RECEIPT_REVERTED"
    return SettlementComparison(
        status=status,
        diagnostic=diagnostic,
        provider_evidence_ids=provider_evidence_ids,
        observer_evidence_ids=observer_evidence_ids,
    )
