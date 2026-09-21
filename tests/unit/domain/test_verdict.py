from __future__ import annotations

from datetime import UTC, datetime

from settlediff.domain.models import (
    CheckStatus,
    DeliveryAssessment,
    DeliveryObservation,
    DeliveryStatus,
    Finding,
    Severity,
    Verdict,
)
from settlediff.domain.verdict import PRECEDENCE, derive_verdict


def finding(check_id: str, status: CheckStatus) -> Finding:
    return Finding(
        finding_id=f"syn:{check_id}",
        check_id=check_id,
        severity=Severity.INFO,
        status=status,
        expected=None,
        observed=None,
        message="synthetic",
        artifact_ids=(),
        field_paths=(check_id,),
    )


def test_verdict_precedence_and_paid_failure() -> None:
    assert PRECEDENCE == (
        Verdict.PAYMENT_FAILURE,
        Verdict.PAID_FAILURE,
        Verdict.UNVERIFIABLE,
        Verdict.VERIFIED_WITH_WARNINGS,
        Verdict.VERIFIED,
    )
    assert (
        derive_verdict(
            (finding("paid_failure", CheckStatus.FAIL), finding("settlement", CheckStatus.PASS))
        )
        is Verdict.PAID_FAILURE
    )
    assert (
        derive_verdict(
            (finding("settlement", CheckStatus.FAIL), finding("paid_failure", CheckStatus.FAIL))
        )
        is Verdict.PAYMENT_FAILURE
    )
    assert (
        derive_verdict(
            (
                finding("settlement", CheckStatus.PASS),
                finding("ledger_outcome", CheckStatus.FAIL),
            )
        )
        is Verdict.UNVERIFIABLE
    )
    assert (
        derive_verdict(
            (
                finding("settlement", CheckStatus.FAIL),
                finding("ledger_outcome", CheckStatus.FAIL),
            )
        )
        is Verdict.PAYMENT_FAILURE
    )
    assert derive_verdict((finding("budget", CheckStatus.FAIL),)) is Verdict.UNVERIFIABLE
    assert derive_verdict((finding("future_check", CheckStatus.FAIL),)) is Verdict.UNVERIFIABLE
    assert derive_verdict((finding("chain", CheckStatus.DIFF),)) is Verdict.VERIFIED_WITH_WARNINGS
    assert derive_verdict((finding("chain", CheckStatus.UNKNOWN),)) is Verdict.UNVERIFIABLE
    assert derive_verdict((finding("chain", CheckStatus.PASS),)) is Verdict.VERIFIED


def _delivery(status: DeliveryStatus) -> DeliveryAssessment:
    if status is DeliveryStatus.NOT_ASSESSED:
        return DeliveryAssessment(
            status=status,
            reason_code="SYNTHETIC",
            evidence_ids=("syn:contract",),
        )
    return DeliveryAssessment(
        status=status,
        reason_code="SYNTHETIC",
        evidence_ids=("syn:contract", "syn:execution"),
        observation=DeliveryObservation(
            observed_at=datetime(2026, 9, 1, tzinfo=UTC),
            status_code=200,
            media_type="application/json",
            received_bytes=21,
            truncated=False,
            parsed_body={"result": "synthetic"},
            evidence_ids=("syn:execution",),
        ),
        response_contract_digest="a" * 64,
    )


def test_settled_payment_with_failed_delivery_is_paid_failure() -> None:
    findings = (finding("settlement", CheckStatus.PASS),)

    assert (
        derive_verdict(findings, delivery=_delivery(DeliveryStatus.FAILED)) is Verdict.PAID_FAILURE
    )


def test_failed_delivery_without_proven_settlement_is_unverifiable() -> None:
    findings = (finding("settlement", CheckStatus.UNKNOWN),)

    assert (
        derive_verdict(findings, delivery=_delivery(DeliveryStatus.FAILED)) is Verdict.UNVERIFIABLE
    )
    assert (
        derive_verdict(
            (finding("delivery", CheckStatus.FAIL),),
            delivery=_delivery(DeliveryStatus.FAILED),
        )
        is Verdict.UNVERIFIABLE
    )


def test_arbitrary_failure_and_satisfied_delivery_do_not_create_paid_failure() -> None:
    findings = (finding("settlement", CheckStatus.PASS), finding("future_check", CheckStatus.FAIL))

    assert (
        derive_verdict(findings, delivery=_delivery(DeliveryStatus.FAILED)) is Verdict.PAID_FAILURE
    )
    assert (
        derive_verdict(findings, delivery=_delivery(DeliveryStatus.SATISFIED))
        is Verdict.UNVERIFIABLE
    )
    assert derive_verdict(findings) is Verdict.UNVERIFIABLE


def test_settlement_failure_still_dominates_delivery() -> None:
    findings = (finding("settlement", CheckStatus.FAIL),)

    assert (
        derive_verdict(findings, delivery=_delivery(DeliveryStatus.FAILED))
        is Verdict.PAYMENT_FAILURE
    )
