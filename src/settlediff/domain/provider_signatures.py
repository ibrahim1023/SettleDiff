"""Deterministic checks over captured provider-signature assessments."""

from __future__ import annotations

from settlediff.domain.models import CheckStatus, Finding, ProviderSignatureEvidence, Severity


def signature_findings(evidence: tuple[ProviderSignatureEvidence, ...]) -> tuple[Finding, ...]:
    findings: list[Finding] = []
    by_kind = {item.kind: item for item in evidence}
    for kind in ("offer", "receipt"):
        item = by_kind.get(kind)
        if item is None:
            continue
        if (
            item.signature_validity == "INVALID"
            or item.seller_identity_binding == "NOT_BOUND"
            or item.signed_terms_match is False
        ):
            status = CheckStatus.FAIL
        elif (
            item.signature_validity == "VALID"
            and item.seller_identity_binding == "BOUND"
            and item.signed_terms_match is True
        ):
            status = CheckStatus.PASS
        else:
            status = CheckStatus.UNKNOWN
        findings.append(
            _finding(
                f"signed_{kind}",
                status,
                item.diagnostic,
                item.evidence_ids,
                f"Signed provider {kind} authenticity and term assessment: {status.value}.",
            )
        )
    offer = by_kind.get("offer")
    receipt = by_kind.get("receipt")
    if receipt is not None:
        if (
            offer is None
            or offer.signer_identity_digest is None
            or receipt.signer_identity_digest is None
        ):
            continuity = CheckStatus.UNKNOWN
        elif offer.signer_identity_digest == receipt.signer_identity_digest:
            continuity = CheckStatus.PASS
        else:
            continuity = CheckStatus.FAIL
        findings.append(
            _finding(
                "signed_receipt_continuity",
                continuity,
                continuity.value,
                receipt.evidence_ids + (offer.evidence_ids if offer is not None else ()),
                f"Provider signing-key continuity assessment: {continuity.value}.",
            )
        )
        transaction = (
            CheckStatus.PASS
            if receipt.correlation == "TRANSACTION_BOUND"
            else CheckStatus.FAIL
            if receipt.correlation == "CONTRADICTED"
            else CheckStatus.WARN
        )
        findings.append(
            _finding(
                "signed_receipt_transaction",
                transaction,
                receipt.correlation,
                receipt.evidence_ids,
                f"Provider-signed receipt transaction-binding assessment: {transaction.value}; "
                "this is not independent settlement proof.",
            )
        )
    return tuple(findings)


def _finding(
    check_id: str,
    status: CheckStatus,
    observed: str | None,
    ids: tuple[str, ...],
    message: str,
) -> Finding:
    return Finding(
        finding_id=f"finding:{check_id}",
        check_id=check_id,
        severity=Severity.HIGH
        if status is CheckStatus.FAIL
        else Severity.INFO
        if status is CheckStatus.PASS
        else Severity.WARNING,
        status=status,
        expected="valid provider evidence",
        observed=observed,
        message=message,
        artifact_ids=ids,
        field_paths=("provider_signatures",),
    )
