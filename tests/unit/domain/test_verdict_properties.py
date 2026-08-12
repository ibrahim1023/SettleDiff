from __future__ import annotations

from hypothesis import given
from hypothesis import strategies as st

from settlediff.domain.models import CheckStatus, Finding, Severity, Verdict
from settlediff.domain.verdict import derive_verdict


def finding(index: int, status: CheckStatus) -> Finding:
    return Finding(
        finding_id=f"syn:{index}",
        check_id=f"check:{index}",
        severity=Severity.INFO,
        status=status,
        expected=None,
        observed=None,
        message="synthetic",
        artifact_ids=(),
        field_paths=(f"check:{index}",),
    )


@given(
    st.permutations(
        (
            finding(1, CheckStatus.PASS),
            finding(2, CheckStatus.DIFF),
            finding(3, CheckStatus.UNKNOWN),
        )
    )
)
def test_verdict_is_invariant_to_finding_order(findings: tuple[Finding, ...]) -> None:
    assert derive_verdict(findings) == derive_verdict(
        (
            finding(1, CheckStatus.PASS),
            finding(2, CheckStatus.DIFF),
            finding(3, CheckStatus.UNKNOWN),
        )
    )


@given(
    st.lists(
        st.sampled_from(
            [CheckStatus.PASS, CheckStatus.WARN, CheckStatus.DIFF, CheckStatus.UNKNOWN]
        ),
        min_size=1,
        max_size=8,
    )
)
def test_adding_payment_failure_cannot_improve_verdict(statuses: list[CheckStatus]) -> None:
    baseline = derive_verdict(
        tuple(finding(index, status) for index, status in enumerate(statuses))
    )
    payment_failure = finding(99, CheckStatus.FAIL).model_copy(update={"check_id": "settlement"})
    with_failure = derive_verdict(
        tuple(finding(index, status) for index, status in enumerate(statuses)) + (payment_failure,)
    )

    rank = {
        Verdict.VERIFIED: 0,
        Verdict.VERIFIED_WITH_WARNINGS: 1,
        Verdict.UNVERIFIABLE: 2,
        Verdict.PAID_FAILURE: 3,
        Verdict.PAYMENT_FAILURE: 4,
    }
    assert rank[with_failure] >= rank[baseline]
