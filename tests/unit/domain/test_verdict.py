from __future__ import annotations

from settlediff.domain.models import CheckStatus, Finding, Severity, Verdict
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
