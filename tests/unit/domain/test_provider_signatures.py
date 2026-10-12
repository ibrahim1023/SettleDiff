from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from settlediff.application.replay import replay_fixture
from settlediff.domain.models import CheckStatus, ProviderSignatureEvidence, Verdict
from settlediff.domain.provider_signatures import signature_findings
from settlediff.domain.verdict import derive_verdict


def evidence(
    kind: str, *, signature_validity: str = "VALID", correlation: str | None = None
) -> ProviderSignatureEvidence:
    return ProviderSignatureEvidence.model_validate(
        {
            "kind": kind,
            "signature_validity": signature_validity,
            "seller_identity_binding": "BOUND",
            "signed_terms_match": True,
            "correlation": correlation,
            "diagnostic": "SIGNED_EVIDENCE_VALID",
            "signature_digest": "a" * 64,
            "signer_identity_digest": "b" * 64,
            "payload_digest": "c" * 64,
            "observed_at": datetime(2026, 10, 12, tzinfo=UTC),
            "evidence_ids": (f"syn_run:signed_{kind}",),
        }
    )


def test_signed_provider_claims_do_not_upgrade_missing_independent_settlement() -> None:
    report = replay_fixture(Path("fixtures/perflo-v8-provider-only-success"))
    extra = signature_findings(
        (evidence("offer"), evidence("receipt", correlation="TRANSACTION_BOUND"))
    )
    assert all(finding.status is CheckStatus.PASS for finding in extra)
    assert derive_verdict(report.findings + extra) is Verdict.UNVERIFIABLE


def test_invalid_signature_or_mismatched_transaction_is_unverifiable_not_payment_failure() -> None:
    report = replay_fixture(Path("fixtures/x402-independent-confirmed"))
    extra = signature_findings((evidence("offer"), evidence("receipt", correlation="CONTRADICTED")))
    assert derive_verdict(report.findings + extra) is Verdict.UNVERIFIABLE
    extra = signature_findings((evidence("offer", signature_validity="INVALID"),))
    assert extra[0].status is CheckStatus.FAIL
    assert derive_verdict(report.findings + extra) is Verdict.UNVERIFIABLE


def test_optional_transaction_and_missing_receipt_remain_explicit() -> None:
    findings = signature_findings(
        (evidence("offer"), evidence("receipt", correlation="NOT_COMPARABLE"))
    )
    assert (
        next(f for f in findings if f.check_id == "signed_receipt_transaction").status
        is CheckStatus.WARN
    )
    assert signature_findings(()) == ()


def test_bundle_rejects_signature_assessments_that_contradict_their_findings() -> None:
    import pytest

    from settlediff import __version__
    from settlediff.application.bundle import (
        DATABASE_SCHEMA_VERSION,
        BundleError,
        CompatibilityMetadata,
        EvidenceBundle,
        verify_bundle,
    )
    from settlediff.domain.integrity import sha256_digest

    report = replay_fixture(Path("fixtures/x402-independent-confirmed"))
    signatures = (evidence("offer"), evidence("receipt", correlation="TRANSACTION_BOUND"))
    findings = report.findings + signature_findings(signatures)
    inconsistent = report.model_copy(
        update={
            "provider_signatures": (
                signatures[0].model_copy(update={"signature_validity": "INVALID"}),
                signatures[1],
            ),
            "findings": findings,
        }
    )
    bundle = EvidenceBundle(
        run_id=report.run_id,
        report=inconsistent,
        explanation=None,
        events=(),
        artifacts=(),
        compatibility=CompatibilityMetadata(
            settlediff_version=__version__,
            report_schema_version=4,
            database_schema_version=DATABASE_SCHEMA_VERSION,
            contextdev_api_path="/web/scrape/markdown",
            hyperfusion_model=None,
            perflo_cli_version=None,
            payment_adapter_id=None,
            x402_protocol_version=None,
            x402_signer_schema_version=None,
        ),
        integrity="0" * 64,
    )
    bundle = bundle.model_copy(
        update={"integrity": sha256_digest(bundle.model_dump(mode="json", exclude={"integrity"}))}
    )
    with pytest.raises(BundleError, match="signature"):
        verify_bundle(bundle)
