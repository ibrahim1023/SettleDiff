from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import cast

import pytest

from settlediff.application.replay import replay_fixture
from settlediff.application.run import RunState, RunTimeline
from settlediff.domain.models import (
    ArtifactType,
    AssetIdentity,
    EvidenceArtifact,
    ExplanationRecord,
    ExplanationSource,
    InvestigationExplanation,
    PaymentReceipt,
    SettlementStatus,
)
from settlediff.domain.money import Money
from settlediff.domain.redaction import redact_report
from settlediff.storage.sqlite import SQLiteReportRepository

CANARY = "syn_canary_secret_never_persist"


def test_report_round_trips_through_sqlite(tmp_path: Path) -> None:
    report = replay_fixture(Path("fixtures/clean-success"))
    repository = SQLiteReportRepository(tmp_path / "reports.sqlite3")
    repository.save(report)
    loaded = repository.get(report.run_id)
    assert loaded is not None
    assert loaded.model_dump(mode="json") == redact_report(report).model_dump(mode="json")
    repository.close()


def test_nested_response_secrets_are_redacted_without_mutating_report(tmp_path: Path) -> None:
    database = tmp_path / "reports.sqlite3"
    original = replay_fixture(Path("fixtures/clean-success"))
    assert original.execution is not None
    response_body = {
        "result": "synthetic",
        "metadata": {
            "api_key": CANARY,
            "nested": [{"refreshToken": CANARY}],
        },
    }
    report = original.model_copy(
        update={"execution": original.execution.model_copy(update={"response_body": response_body})}
    )
    repository = SQLiteReportRepository(database)

    repository.save(report)

    with closing(sqlite3.connect(database)) as connection:
        stored_json = cast(
            str,
            connection.execute(
                "SELECT report_json FROM reports WHERE run_id = ?", (report.run_id,)
            ).fetchone()[0],
        )
    loaded = repository.get(report.run_id)
    assert loaded is not None
    assert loaded == redact_report(report)
    assert loaded.verdict is report.verdict
    assert tuple(finding.status for finding in loaded.findings) == tuple(
        finding.status for finding in report.findings
    )
    assert CANARY not in stored_json
    assert stored_json.count("[REDACTED]") >= 2
    assert CANARY in report.model_dump_json()
    repository.close()


def test_network_asset_and_receipt_identifiers_are_redacted_before_storage(
    tmp_path: Path,
) -> None:
    recipient = "0x1111111111111111111111111111111111111111"
    asset_reference = "0x036CbD53842c5426634e7929541eC2318f3dCF7e"
    transaction_hash = "0x2222222222222222222222222222222222222222222222222222222222222222"
    identity = AssetIdentity(
        symbol="USDC",
        network="eip155:84532",
        reference=asset_reference,
        decimals=6,
    )
    original = replay_fixture(Path("fixtures/clean-success"))
    assert original.contract is not None
    assert original.execution is not None
    assert original.ledger is not None
    receipt = PaymentReceipt(
        amount=Money(amount=Decimal("0.001"), unit="USDC"),
        asset="USDC",
        asset_identity=identity,
        protocol="x402",
        scheme="exact",
        chain=None,
        network="eip155:84532",
        recipient=recipient,
        settlement_status=SettlementStatus.SETTLED,
        transaction_id=None,
        session_id=None,
        transaction_hash=transaction_hash,
        issued_at=datetime(2026, 8, 31, tzinfo=UTC),
    )
    report = original.model_copy(
        update={
            "contract": original.contract.model_copy(
                update={
                    "asset_identity": identity,
                    "network": "eip155:84532",
                    "recipient": recipient,
                    "scheme": "exact",
                }
            ),
            "execution": original.execution.model_copy(
                update={
                    "asset_identity": identity,
                    "network": "eip155:84532",
                    "recipient": recipient,
                }
            ),
            "receipt": receipt,
            "ledger": original.ledger.model_copy(
                update={
                    "asset_identity": identity,
                    "network": "eip155:84532",
                    "recipient": recipient,
                    "transaction_hash": transaction_hash,
                }
            ),
        }
    )
    database = tmp_path / "reports.sqlite3"
    repository = SQLiteReportRepository(database)

    repository.save(report)

    with closing(sqlite3.connect(database)) as connection:
        stored_json = cast(
            str,
            connection.execute(
                "SELECT report_json FROM reports WHERE run_id = ?", (report.run_id,)
            ).fetchone()[0],
        )
    loaded = repository.get(report.run_id)
    assert loaded is not None
    assert recipient not in stored_json
    assert asset_reference not in stored_json
    assert transaction_hash not in stored_json
    assert loaded.contract is not None
    assert loaded.contract.recipient == "0x1111…1111"
    assert loaded.contract.asset_identity is not None
    assert loaded.contract.asset_identity.reference == "0x036C…CF7e"
    assert loaded.receipt is not None
    assert loaded.receipt.transaction_hash == "0x2222…2222"
    repository.close()


def test_nonhex_keyed_identifiers_are_masked_across_persisted_report(tmp_path: Path) -> None:
    report = replay_fixture(Path("fixtures/x402-clean-success"))
    repository = SQLiteReportRepository(tmp_path / "reports.sqlite3")

    repository.save(report)

    loaded = repository.get(report.run_id)
    assert loaded is not None
    serialized = loaded.model_dump_json()
    assert "syn_x402_recipient" not in serialized
    assert loaded.contract is not None
    assert loaded.contract.recipient == "syn_…ient"
    recipient = next(finding for finding in loaded.findings if finding.check_id == "recipient")
    assert recipient.expected == "syn_…ient"
    assert recipient.observed == "syn_…ient"
    repository.close()


def test_storage_failure_does_not_mutate_report(tmp_path: Path) -> None:
    report = replay_fixture(Path("fixtures/clean-success"))
    before = report.model_dump_json()
    repository = SQLiteReportRepository(tmp_path / "reports.sqlite3")
    repository.close()

    with pytest.raises(sqlite3.ProgrammingError):
        repository.save(report)

    assert report.model_dump_json() == before


def test_events_are_ordered_and_deleted_with_report(tmp_path: Path) -> None:
    report = replay_fixture(Path("fixtures/clean-success"))
    timeline = RunTimeline()
    timeline.transition(RunState.AUTHORIZED)
    repository = SQLiteReportRepository(tmp_path / "reports.sqlite3")
    repository.save(report, events=timeline.events)
    assert [event.state for event in repository.events(report.run_id)] == [
        RunState.PREFLIGHT,
        RunState.AUTHORIZED,
    ]
    assert repository.delete(report.run_id)
    assert repository.events(report.run_id) == ()


def test_artifacts_are_redacted_before_insert_and_migrations_are_idempotent(tmp_path: Path) -> None:
    report = replay_fixture(Path("fixtures/clean-success"))
    repository = SQLiteReportRepository(tmp_path / "reports.sqlite3")
    artifact = EvidenceArtifact(
        artifact_id="artifact:raw",
        artifact_type=ArtifactType.SERVICE_RESPONSE,
        source="test",
        collected_at=datetime(2026, 8, 13, tzinfo=UTC),
        redacted=False,
        data={"authorization": "secret", "recipient": "0123456789abcdef"},
    )
    repository.save(report, artifacts=(artifact,))
    stored = repository.artifacts(report.run_id)[0]
    data = cast(dict[str, str], stored.data)
    assert stored.redacted
    assert data["authorization"] == "[REDACTED]"
    SQLiteReportRepository(tmp_path / "reports.sqlite3").close()


def test_explanation_round_trips_strict_json_with_only_narrative_identifiers_redacted(
    tmp_path: Path,
) -> None:
    database = tmp_path / "reports.sqlite3"
    report = replay_fixture(Path("fixtures/clean-success"))
    summary_email = "operator@example.com"
    summary_transaction = "0x0123456789abcdef0123456789abcdef"
    next_step_identifier = "abcdef0123456789abcdef0123456789"
    explanation = ExplanationRecord(
        explanation=InvestigationExplanation(
            run_id=report.run_id,
            summary=f"Ask {summary_email} about transaction {summary_transaction}.",
            evidence_used=("artifact:0xfedcba9876543210fedcba9876543210",),
            finding_ids=tuple(finding.finding_id for finding in report.findings),
            deterministic_verdict=report.verdict,
            recommended_next_step=f"Review account {next_step_identifier}.",
        ),
        source=ExplanationSource.PROVIDER,
        tool_calls=2,
        model_requests=1,
        input_tokens=321,
        output_tokens=54,
        rejected_output=(
            f'{{"api_key":"{CANARY}","transaction_id":"{summary_transaction}",'
            f'"contact":"{summary_email}"}}'
        ),
    )
    before = explanation.model_dump_json()
    repository = SQLiteReportRepository(database)

    repository.save(report, explanation=explanation)

    loaded = repository.explanation(report.run_id)
    assert loaded is not None
    assert loaded.explanation.run_id == explanation.explanation.run_id
    assert loaded.explanation.deterministic_verdict is explanation.explanation.deterministic_verdict
    assert loaded.explanation.finding_ids == explanation.explanation.finding_ids
    assert loaded.explanation.evidence_used == explanation.explanation.evidence_used
    assert loaded.source is ExplanationSource.PROVIDER
    assert loaded.tool_calls == 2
    assert loaded.model_requests == 1
    assert loaded.input_tokens == 321
    assert loaded.output_tokens == 54
    assert loaded.rejected_output is not None
    assert CANARY not in loaded.rejected_output
    assert summary_transaction not in loaded.rejected_output
    assert summary_email not in loaded.rejected_output
    assert loaded.explanation.summary == "Ask o***@example.com about transaction 0x0123…cdef."
    assert loaded.explanation.recommended_next_step == "Review account abcd…6789."
    with closing(sqlite3.connect(database)) as connection:
        stored_json = cast(
            str,
            connection.execute(
                "SELECT explanation_json FROM explanations WHERE run_id = ?", (report.run_id,)
            ).fetchone()[0],
        )
    assert summary_email not in stored_json
    assert summary_transaction not in stored_json
    assert next_step_identifier not in stored_json
    assert explanation.model_dump_json() == before
    repository.close()


def test_replacing_report_without_explanation_removes_stale_row_and_delete_cascades(
    tmp_path: Path,
) -> None:
    report = replay_fixture(Path("fixtures/clean-success"))
    explanation = ExplanationRecord(
        explanation=InvestigationExplanation(
            run_id=report.run_id,
            summary="Deterministic verification completed.",
            evidence_used=(),
            finding_ids=tuple(finding.finding_id for finding in report.findings),
            deterministic_verdict=report.verdict,
            recommended_next_step=None,
        ),
        source=ExplanationSource.FALLBACK,
        tool_calls=0,
    )
    repository = SQLiteReportRepository(tmp_path / "reports.sqlite3")

    repository.save(report, explanation=explanation)
    repository.save(report)
    assert repository.explanation(report.run_id) is None

    repository.save(report, explanation=explanation)
    assert repository.delete(report.run_id)
    assert repository.explanation(report.run_id) is None
    repository.close()


def test_explanation_failure_rolls_back_report_events_and_artifacts(tmp_path: Path) -> None:
    database = tmp_path / "reports.sqlite3"
    report = replay_fixture(Path("fixtures/clean-success"))
    assert report.execution is not None
    timeline = RunTimeline()
    timeline.transition(RunState.AUTHORIZED)
    artifact = EvidenceArtifact(
        artifact_id="artifact:original",
        artifact_type=ArtifactType.SERVICE_RESPONSE,
        source="test",
        collected_at=datetime(2026, 8, 13, tzinfo=UTC),
        redacted=False,
        data={"result": "original"},
    )
    explanation = ExplanationRecord(
        explanation=InvestigationExplanation(
            run_id=report.run_id,
            summary="Original explanation.",
            evidence_used=(artifact.artifact_id,),
            finding_ids=tuple(finding.finding_id for finding in report.findings),
            deterministic_verdict=report.verdict,
            recommended_next_step=None,
        ),
        source=ExplanationSource.FALLBACK,
        tool_calls=0,
    )
    repository = SQLiteReportRepository(database)
    repository.save(report, events=timeline.events, artifacts=(artifact,), explanation=explanation)
    replacement = report.model_copy(
        update={
            "execution": report.execution.model_copy(
                update={"response_body": {"result": "replacement"}}
            )
        }
    )
    with closing(sqlite3.connect(database)) as connection:
        connection.execute(
            """
            CREATE TRIGGER reject_explanations
            BEFORE INSERT ON explanations
            BEGIN
                SELECT RAISE(ABORT, 'synthetic explanation failure');
            END
            """
        )

    with pytest.raises(sqlite3.IntegrityError, match="synthetic explanation failure"):
        repository.save(replacement, explanation=explanation)

    assert repository.get(report.run_id) == redact_report(report)
    assert repository.events(report.run_id) == timeline.events
    assert repository.artifacts(report.run_id) == (artifact.model_copy(update={"redacted": True}),)
    assert repository.explanation(report.run_id) == explanation
    repository.close()
