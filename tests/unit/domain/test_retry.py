from __future__ import annotations

import itertools
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import JsonValue

from settlediff.application.replay import replay_fixture
from settlediff.domain.models import (
    ArtifactType,
    EvidenceArtifact,
    MachineReport,
    RetrySafety,
)
from settlediff.domain.retry import (
    CONFIRMED_RECEIPT,
    CONFIRMED_TRANSFER,
    EVIDENCE_CONTRADICTION,
    EVIDENCE_MISSING,
    EXPLICIT_NON_SUBMISSION,
    NON_SUBMISSION_UNPROVEN,
    PROVIDER_PAYMENT_ATTEMPT,
    RECOVERY_INVALID,
    RECOVERY_PENDING,
    RECOVERY_UNAVAILABLE,
    REVERTED_RECEIPT,
    RUN_REFUSED,
    SUBMISSION_UNCERTAIN,
    TRANSMISSION_CONFIRMED,
    RetryRunStateSnapshot,
    analyze_retry,
)

NOW = datetime(2026, 9, 4, tzinfo=UTC)
RUN_ID = "syn_retry_run"


def artifact(
    artifact_type: ArtifactType,
    data: JsonValue,
    *,
    artifact_id: str | None = None,
    source: str = "syn.provider.recovery",
) -> EvidenceArtifact:
    return EvidenceArtifact(
        artifact_id=artifact_id or f"{RUN_ID}:{artifact_type.value}",
        artifact_type=artifact_type,
        source=source,
        collected_at=NOW,
        redacted=False,
        data=data,
    )


def state(value: str = "failed", *, submission_uncertain: bool = False) -> RetryRunStateSnapshot:
    return RetryRunStateSnapshot(
        run_id=RUN_ID, state=value, submission_uncertain=submission_uncertain
    )


def assess(
    artifacts: tuple[EvidenceArtifact, ...] = (),
    *,
    report: MachineReport | None = None,
    snapshot: RetryRunStateSnapshot | None = None,
):
    return analyze_retry(report, artifacts, snapshot or state())


def test_explicit_non_submission_proof_is_safe() -> None:
    result = assess(
        (
            artifact(
                ArtifactType.PAYMENT_RECEIPT,
                {
                    "status": "not_submitted",
                    "proof_of_non_submission": True,
                    "source_submission_state": "proven_not_submitted",
                    "diagnostic": None,
                },
            ),
        )
    )
    assert result.safety is RetrySafety.SAFE_TO_RETRY
    assert result.reason_codes == (EXPLICIT_NON_SUBMISSION,)
    assert result.evidence_ids == (f"{RUN_ID}:payment_receipt",)


def test_refused_run_is_safe() -> None:
    result = assess(snapshot=state("refused"))
    assert result.safety is RetrySafety.SAFE_TO_RETRY
    assert result.reason_codes == (RUN_REFUSED,)
    assert result.evidence_ids == (f"{RUN_ID}:run_state",)


def test_confirmed_receipt_is_do_not_retry() -> None:
    result = assess((artifact(ArtifactType.PAYMENT_RECEIPT, {"status": "confirmed"}),))
    assert result.safety is RetrySafety.DO_NOT_RETRY
    assert result.reason_codes == (CONFIRMED_RECEIPT,)


def test_confirmed_transfer_receipt_is_do_not_retry() -> None:
    result = assess(
        (
            artifact(
                ArtifactType.PAYMENT_RECEIPT,
                {"status": "confirmed", "transaction_hash": "syn_hash"},
                source="syn.provider.transaction_receipt",
            ),
        )
    )
    assert result.safety is RetrySafety.DO_NOT_RETRY
    assert result.reason_codes == (CONFIRMED_TRANSFER,)


def test_reverted_receipt_is_do_not_retry() -> None:
    result = assess((artifact(ArtifactType.PAYMENT_RECEIPT, {"status": "failed"}),))
    assert result.safety is RetrySafety.DO_NOT_RETRY
    assert result.reason_codes == (REVERTED_RECEIPT,)


def test_confirmed_transmission_state_is_do_not_retry() -> None:
    result = assess(
        (
            artifact(
                ArtifactType.PAYMENT_RECEIPT,
                {
                    "status": "unresolved",
                    "proof_of_non_submission": False,
                    "source_submission_state": "submitted_confirmed",
                    "diagnostic": None,
                },
            ),
        )
    )
    assert result.safety is RetrySafety.DO_NOT_RETRY
    assert result.reason_codes == (TRANSMISSION_CONFIRMED, RECOVERY_PENDING)


def test_unproven_non_submission_requires_human() -> None:
    result = assess(
        (
            artifact(
                ArtifactType.PAYMENT_RECEIPT,
                {"status": "not_submitted", "proof_of_non_submission": False},
            ),
        )
    )
    assert result.safety is RetrySafety.REQUIRES_HUMAN_DECISION
    assert result.reason_codes == (NON_SUBMISSION_UNPROVEN,)


def test_contradicting_non_submission_source_requires_human() -> None:
    result = assess(
        (
            artifact(
                ArtifactType.PAYMENT_RECEIPT,
                {
                    "status": "not_submitted",
                    "proof_of_non_submission": True,
                    "source_submission_state": "submission_uncertain",
                },
            ),
        )
    )
    assert result.safety is RetrySafety.REQUIRES_HUMAN_DECISION
    assert result.reason_codes == (EVIDENCE_CONTRADICTION,)


def test_pending_receipt_requires_human() -> None:
    result = assess((artifact(ArtifactType.PAYMENT_RECEIPT, {"status": "unresolved"}),))
    assert result.safety is RetrySafety.REQUIRES_HUMAN_DECISION
    assert result.reason_codes == (RECOVERY_PENDING,)


def test_rpc_unavailable_requires_human() -> None:
    result = assess(
        (
            artifact(
                ArtifactType.PAYMENT_RECEIPT,
                {
                    "status": "unresolved",
                    "proof_of_non_submission": False,
                    "source_submission_state": "submission_uncertain",
                    "diagnostic": "rpc_unavailable",
                },
            ),
        )
    )
    assert result.safety is RetrySafety.REQUIRES_HUMAN_DECISION
    assert result.reason_codes == (RECOVERY_PENDING, RECOVERY_UNAVAILABLE)


def test_malformed_receipt_requires_human() -> None:
    result = assess((artifact(ArtifactType.PAYMENT_RECEIPT, "not-an-object"),))
    assert result.safety is RetrySafety.REQUIRES_HUMAN_DECISION
    assert result.reason_codes == (RECOVERY_INVALID,)


def test_unknown_receipt_status_requires_human() -> None:
    result = assess((artifact(ArtifactType.PAYMENT_RECEIPT, {"status": "mystery"}),))
    assert result.safety is RetrySafety.REQUIRES_HUMAN_DECISION
    assert result.reason_codes == (RECOVERY_INVALID,)


def test_nonempty_activity_requires_human() -> None:
    result = assess(
        (
            artifact(
                ArtifactType.ACTIVITY,
                {"entries": [{"status": "listed", "id": "syn_payment"}]},
            ),
        )
    )
    assert result.safety is RetrySafety.REQUIRES_HUMAN_DECISION
    assert result.reason_codes == (PROVIDER_PAYMENT_ATTEMPT,)


def test_confirmed_activity_transfer_is_do_not_retry() -> None:
    result = assess(
        (
            artifact(
                ArtifactType.ACTIVITY,
                [{"status": "confirmed", "id": "syn_payment"}],
                source="syn.provider.transaction_receipt",
            ),
        )
    )
    assert result.safety is RetrySafety.DO_NOT_RETRY
    assert result.reason_codes == (CONFIRMED_TRANSFER, PROVIDER_PAYMENT_ATTEMPT)


def test_non_dict_activity_entry_requires_human() -> None:
    result = assess((artifact(ArtifactType.ACTIVITY, ["bad"]),))
    assert result.safety is RetrySafety.REQUIRES_HUMAN_DECISION
    assert result.reason_codes == (RECOVERY_INVALID,)


def test_malformed_nested_activity_entry_requires_human() -> None:
    result = assess((artifact(ArtifactType.ACTIVITY, {"records": ["bad"]}),))
    assert result.safety is RetrySafety.REQUIRES_HUMAN_DECISION
    assert result.reason_codes == (RECOVERY_INVALID,)


@pytest.mark.parametrize("nested", ["bad", None, {"status": "confirmed"}])
def test_non_list_nested_activity_value_requires_human(nested: JsonValue) -> None:
    result = assess((artifact(ArtifactType.ACTIVITY, {"records": nested}),))
    assert result.safety is RetrySafety.REQUIRES_HUMAN_DECISION
    assert result.reason_codes == (RECOVERY_INVALID,)


def test_canonical_provider_receipt_is_attempt_not_independent_proof() -> None:
    result = assess(
        (
            artifact(
                ArtifactType.PAYMENT_RECEIPT,
                {"settlement_status": "settled", "transaction_hash": "syn_hash"},
            ),
        )
    )
    assert result.safety is RetrySafety.REQUIRES_HUMAN_DECISION
    assert result.reason_codes == (PROVIDER_PAYMENT_ATTEMPT,)


def test_provider_receipt_failed_settlement_is_not_a_reverted_receipt() -> None:
    result = assess(
        (
            artifact(
                ArtifactType.PAYMENT_RECEIPT,
                {"settlement_status": "failed", "transaction_id": "syn_tx"},
            ),
        )
    )
    assert result.safety is RetrySafety.REQUIRES_HUMAN_DECISION
    assert result.reason_codes == (PROVIDER_PAYMENT_ATTEMPT,)


def test_generic_transfer_source_matching_is_case_insensitive() -> None:
    result = assess(
        (
            artifact(
                ArtifactType.PAYMENT_RECEIPT,
                {"status": "confirmed"},
                source="SYN.Provider.Transaction_Receipt",
            ),
        )
    )
    assert result.safety is RetrySafety.DO_NOT_RETRY
    assert result.reason_codes == (CONFIRMED_TRANSFER,)


def test_empty_activity_alone_is_no_safe_proof() -> None:
    result = assess((artifact(ArtifactType.ACTIVITY, []),))
    assert result.safety is RetrySafety.REQUIRES_HUMAN_DECISION
    assert result.reason_codes == (EVIDENCE_MISSING,)


def test_execution_attempt_requires_human() -> None:
    result = assess(
        (
            artifact(
                ArtifactType.EXECUTION,
                {"settlement_status": "pending", "transaction_hash": "syn_hash"},
            ),
        )
    )
    assert result.safety is RetrySafety.REQUIRES_HUMAN_DECISION
    assert result.reason_codes == (PROVIDER_PAYMENT_ATTEMPT,)


def test_execution_without_attempt_data_contributes_nothing() -> None:
    result = assess((artifact(ArtifactType.EXECUTION, {"settlement_status": "unknown"}),))
    assert result.safety is RetrySafety.REQUIRES_HUMAN_DECISION
    assert result.reason_codes == (EVIDENCE_MISSING,)


def test_submission_uncertain_run_state_requires_human() -> None:
    result = assess(snapshot=state("failed", submission_uncertain=True))
    assert result.safety is RetrySafety.REQUIRES_HUMAN_DECISION
    assert result.reason_codes == (SUBMISSION_UNCERTAIN,)
    assert result.evidence_ids == (f"{RUN_ID}:run_state",)


def test_report_provider_attempt_requires_human() -> None:
    report = replay_fixture(Path("fixtures/clean-success"))
    result = assess(report=report)
    assert result.safety is RetrySafety.REQUIRES_HUMAN_DECISION
    assert result.reason_codes == (PROVIDER_PAYMENT_ATTEMPT,)
    assert result.evidence_ids == (f"{RUN_ID}:report",)


def test_safe_signal_with_contrary_evidence_is_not_safe() -> None:
    result = assess(
        (
            artifact(
                ArtifactType.PAYMENT_RECEIPT,
                {"status": "not_submitted", "proof_of_non_submission": True},
            ),
            artifact(ArtifactType.PAYMENT_RECEIPT, {"status": "confirmed"}, artifact_id="r2"),
        )
    )
    assert result.safety is RetrySafety.DO_NOT_RETRY
    assert result.reason_codes == (
        CONFIRMED_RECEIPT,
        EXPLICIT_NON_SUBMISSION,
        EVIDENCE_CONTRADICTION,
    )
    assert result.evidence_ids == ("r2", f"{RUN_ID}:payment_receipt", f"{RUN_ID}:run_state")


def test_no_evidence_is_human_with_missing_evidence_reason() -> None:
    result = assess()
    assert result.safety is RetrySafety.REQUIRES_HUMAN_DECISION
    assert result.reason_codes == (EVIDENCE_MISSING,)
    assert result.evidence_ids == (f"{RUN_ID}:run_state",)


def test_artifact_order_does_not_change_assessment() -> None:
    artifacts = (
        artifact(ArtifactType.PAYMENT_RECEIPT, {"status": "confirmed"}, artifact_id="r1"),
        artifact(
            ArtifactType.PAYMENT_RECEIPT,
            {"status": "not_submitted", "proof_of_non_submission": True},
            artifact_id="r2",
        ),
        artifact(ArtifactType.ACTIVITY, [{"status": "listed"}], artifact_id="a1"),
        artifact(ArtifactType.PAYMENT_RECEIPT, {"status": "unresolved"}, artifact_id="r3"),
    )
    baseline = assess(artifacts)
    for permutation in itertools.permutations(artifacts):
        assert assess(tuple(permutation)) == baseline


def test_adding_evidence_is_monotone_in_safety_rank() -> None:
    rank = {
        RetrySafety.SAFE_TO_RETRY: 0,
        RetrySafety.REQUIRES_HUMAN_DECISION: 1,
        RetrySafety.DO_NOT_RETRY: 2,
    }
    safe = (
        artifact(
            ArtifactType.PAYMENT_RECEIPT,
            {"status": "not_submitted", "proof_of_non_submission": True},
            artifact_id="safe",
        ),
    )
    human = (artifact(ArtifactType.ACTIVITY, [{"status": "listed"}], artifact_id="human"),)
    blocking = (artifact(ArtifactType.PAYMENT_RECEIPT, {"status": "failed"}, artifact_id="bad"),)
    for additions in itertools.chain(
        itertools.permutations(human + blocking, 1),
        itertools.permutations(human + blocking, 2),
    ):
        combined = assess(safe + additions)
        assert rank[combined.safety] >= rank[assess(safe).safety]
        assert combined.safety is not RetrySafety.SAFE_TO_RETRY
