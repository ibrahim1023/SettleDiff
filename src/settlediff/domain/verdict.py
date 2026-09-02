"""Deterministic verdict precedence for independent verification findings."""

from __future__ import annotations

from settlediff.domain.models import CheckStatus, Finding, Verdict

PRECEDENCE = (
    Verdict.PAYMENT_FAILURE,
    Verdict.PAID_FAILURE,
    Verdict.UNVERIFIABLE,
    Verdict.VERIFIED_WITH_WARNINGS,
    Verdict.VERIFIED,
)


def derive_verdict(findings: tuple[Finding, ...]) -> Verdict:
    """Apply explicit precedence independent of finding order."""
    check_statuses = {finding.check_id: finding.status for finding in findings}
    if check_statuses.get("settlement") is CheckStatus.FAIL:
        return Verdict.PAYMENT_FAILURE
    if check_statuses.get("paid_failure") is CheckStatus.FAIL:
        return Verdict.PAID_FAILURE
    if check_statuses.get("ledger_outcome") is CheckStatus.FAIL:
        return Verdict.UNVERIFIABLE
    if any(finding.status is CheckStatus.FAIL for finding in findings):
        return Verdict.UNVERIFIABLE
    if any(finding.status is CheckStatus.UNKNOWN for finding in findings):
        return Verdict.UNVERIFIABLE
    if any(finding.status in {CheckStatus.WARN, CheckStatus.DIFF} for finding in findings):
        return Verdict.VERIFIED_WITH_WARNINGS
    return Verdict.VERIFIED
