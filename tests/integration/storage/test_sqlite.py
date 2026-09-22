from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import cast

import pytest
from pydantic import JsonValue

from settlediff.application.replay import replay_fixture
from settlediff.application.run import RunEvent, RunFailure, RunProvenance, RunState, RunTimeline
from settlediff.application.timeline import (
    EvidenceTimelineEvent,
    build_evidence_timeline,
)
from settlediff.domain.drift import build_contract_snapshot
from settlediff.domain.models import (
    ArtifactType,
    AssetIdentity,
    DeliveryAssessment,
    DeliveryObservation,
    DeliveryStatus,
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


def test_live_run_is_durable_before_final_report(tmp_path: Path) -> None:
    report = replay_fixture(Path("fixtures/clean-success"))
    repository = SQLiteReportRepository(tmp_path / "reports.sqlite3")
    created_at = datetime(2026, 9, 3, tzinfo=UTC)
    artifact = EvidenceArtifact(
        artifact_id=f"{report.run_id}:preflight",
        artifact_type=ArtifactType.SERVICE_CONTRACT,
        source="synthetic.live",
        collected_at=created_at,
        redacted=False,
        data={"recipient": "syn_live_recipient"},
    )

    repository.begin_run(
        report.run_id,
        task=report.intent.task,
        provenance=RunProvenance.EXTERNAL_LIVE,
        created_at=created_at,
    )
    repository.append_event(
        report.run_id, RunEvent(state=RunState.AUTHORIZED, occurred_at=created_at)
    )
    repository.save_artifacts(report.run_id, (artifact,))
    failure = RunFailure(
        stage=RunState.EXECUTING,
        error_class="SyntheticFailure",
        diagnostic="synthetic safe failure",
        submission_uncertain=True,
        occurred_at=created_at,
    )
    repository.record_failure(report.run_id, failure)

    active = repository.record(report.run_id)
    assert active is not None
    assert active.report is None
    assert active.provenance is RunProvenance.EXTERNAL_LIVE
    assert active.latest_state is RunState.FAILED
    assert active.failure == failure
    assert repository.artifacts(report.run_id)[0].redacted

    timeline = build_evidence_timeline(
        report, repository.events(report.run_id), repository.artifacts(report.run_id)
    )
    repository.finalize_run(report, explanation=None, timeline=timeline)

    completed = repository.record(report.run_id)
    assert completed is not None
    assert completed.report == redact_report(report)
    assert repository.timeline(report.run_id) == timeline
    assert completed.latest_state is RunState.COMPLETE
    assert completed.failure is None
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


def test_schema3_delivery_observation_is_redacted_without_losing_exact_evidence(
    tmp_path: Path,
) -> None:
    database = tmp_path / "reports.sqlite3"
    original = replay_fixture(Path("fixtures/clean-success"))
    observation = DeliveryObservation(
        observed_at=datetime(2026, 9, 1, tzinfo=UTC),
        status_code=200,
        media_type="application/json",
        received_bytes=64,
        truncated=False,
        parsed_body={
            "result": "synthetic",
            "api_key": CANARY,
            "wallet_address": "0x3333333333333333333333333333333333333333",
        },
        evidence_ids=("syn_run:execution",),
    )
    delivery = DeliveryAssessment(
        status=DeliveryStatus.SATISFIED,
        reason_code="DELIVERY_SATISFIED",
        evidence_ids=("syn_run:contract", "syn_run:execution"),
        observation=observation,
        response_contract_digest="a" * 64,
    )
    report = original.model_copy(update={"schema_version": 3, "delivery": delivery})
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
    assert loaded.delivery is not None
    assert loaded.delivery.observation is not None
    assert loaded.delivery.observation.status_code == 200
    assert loaded.delivery.observation.received_bytes == 64
    assert loaded.delivery.observation.truncated is False
    parsed = cast(dict[str, object], loaded.delivery.observation.parsed_body)
    assert CANARY not in stored_json
    assert "0x3333333333333333333333333333333333333333" not in stored_json
    assert parsed["api_key"] == "[REDACTED]"
    assert parsed["wallet_address"] == "0x3333…3333"
    repository.close()


def test_migration_five_is_applied_and_idempotent(tmp_path: Path) -> None:
    database = tmp_path / "reports.sqlite3"
    repository = SQLiteReportRepository(database)

    with closing(sqlite3.connect(database)) as connection:
        versions = {row[0] for row in connection.execute("SELECT version FROM schema_migrations")}
        columns = connection.execute("PRAGMA table_info(evidence_timeline_events)").fetchall()

    assert 5 in versions
    assert {column[1] for column in columns} == {"run_id", "sequence", "event_json"}
    SQLiteReportRepository(database).close()
    repository.close()


def test_save_persists_timeline_and_repeat_save_never_mutates_it(tmp_path: Path) -> None:
    report = replay_fixture(Path("fixtures/clean-success"))
    repository = SQLiteReportRepository(tmp_path / "reports.sqlite3")

    repository.save(report)
    first = repository.timeline(report.run_id)
    repository.save(report)
    second = repository.timeline(report.run_id)

    assert first
    assert first == second
    assert first[0].source == "settlediff.intent"
    assert [event.sequence for event in first] == list(range(len(first)))
    repository.close()


def test_finalize_run_persists_report_and_timeline_atomically(tmp_path: Path) -> None:
    report = replay_fixture(Path("fixtures/clean-success"))
    repository = SQLiteReportRepository(tmp_path / "reports.sqlite3")
    created_at = datetime(2026, 9, 3, tzinfo=UTC)
    repository.begin_run(
        report.run_id,
        task=report.intent.task,
        provenance=RunProvenance.EXTERNAL_LIVE,
        created_at=created_at,
    )
    timeline = build_evidence_timeline(report, repository.events(report.run_id), ())

    repository.finalize_run(report, explanation=None, timeline=timeline)

    assert repository.get(report.run_id) == redact_report(report)
    persisted = repository.timeline(report.run_id)
    assert persisted == build_evidence_timeline(
        redact_report(report), repository.events(report.run_id), ()
    )
    assert [event.sequence for event in persisted] == list(range(len(persisted)))
    repository.close()


def test_invalid_timeline_insert_rolls_back_finalization(tmp_path: Path) -> None:
    database = tmp_path / "reports.sqlite3"
    report = replay_fixture(Path("fixtures/clean-success"))
    repository = SQLiteReportRepository(database)
    created_at = datetime(2026, 9, 3, tzinfo=UTC)
    repository.begin_run(
        report.run_id,
        task=report.intent.task,
        provenance=RunProvenance.EXTERNAL_LIVE,
        created_at=created_at,
    )
    valid = build_evidence_timeline(report, repository.events(report.run_id), ())
    duplicate = valid[:1] + valid[:1]

    with pytest.raises((ValueError, sqlite3.IntegrityError)):
        repository.finalize_run(report, explanation=None, timeline=duplicate)

    record = repository.record(report.run_id)
    assert record is not None
    assert record.report is None
    assert repository.timeline(report.run_id) == ()
    repository.close()


def test_empty_final_timeline_rolls_back_finalization(tmp_path: Path) -> None:
    report = replay_fixture(Path("fixtures/clean-success"))
    repository = SQLiteReportRepository(tmp_path / "reports.sqlite3")
    created_at = datetime(2026, 9, 3, tzinfo=UTC)
    repository.begin_run(
        report.run_id,
        task=report.intent.task,
        provenance=RunProvenance.EXTERNAL_LIVE,
        created_at=created_at,
    )

    with pytest.raises(ValueError, match="timeline cannot be empty"):
        repository.finalize_run(report, explanation=None, timeline=())

    record = repository.record(report.run_id)
    assert record is not None
    assert record.report is None
    assert repository.timeline(report.run_id) == ()
    repository.close()


def test_manually_built_timeline_events_are_redacted_before_insert(
    tmp_path: Path,
) -> None:
    database = tmp_path / "reports.sqlite3"
    report = replay_fixture(Path("fixtures/clean-success"))
    repository = SQLiteReportRepository(database)
    created_at = datetime(2026, 9, 3, tzinfo=UTC)
    repository.begin_run(
        report.run_id,
        task=report.intent.task,
        provenance=RunProvenance.EXTERNAL_LIVE,
        created_at=created_at,
    )
    event = EvidenceTimelineEvent(
        sequence=0,
        source_time=None,
        observed_at=created_at,
        source="synthetic.manual",
        artifact_ids=(),
        finding_ids=(),
        attributes={
            "event": "artifact_observed",
            "status_code": 200,
            "api_key": CANARY,
            "wallet_address": "0x3333333333333333333333333333333333333333",
            "note": "saw 0x9999999999999999999999999999999999999999 embedded",
        },
    )

    repository.finalize_run(report, explanation=None, timeline=(event,))

    persisted = repository.timeline(report.run_id)
    assert len(persisted) == 1
    assert persisted != (event,)
    attributes = persisted[0].attributes
    assert attributes["status_code"] == 200
    assert attributes["api_key"] == "[REDACTED]"
    assert attributes["wallet_address"] == "0x3333…3333"
    assert attributes["note"] == "saw 0x9999…9999 embedded"
    assert event.attributes["api_key"] == CANARY
    with closing(sqlite3.connect(database)) as connection:
        stored_json = cast(
            str,
            connection.execute(
                "SELECT event_json FROM evidence_timeline_events WHERE run_id = ?",
                (report.run_id,),
            ).fetchone()[0],
        )
    assert CANARY not in stored_json
    assert "0x3333333333333333333333333333333333333333" not in stored_json
    repository.close()


def test_timeline_sequence_order_and_run_delete_cascade(tmp_path: Path) -> None:
    report = replay_fixture(Path("fixtures/clean-success"))
    database = tmp_path / "reports.sqlite3"
    repository = SQLiteReportRepository(database)
    repository.save(report)
    persisted = repository.timeline(report.run_id)
    assert [event.sequence for event in persisted] == list(range(len(persisted)))

    assert repository.delete(report.run_id)

    assert repository.timeline(report.run_id) == ()
    with closing(sqlite3.connect(database)) as connection:
        remaining = connection.execute(
            "SELECT COUNT(*) FROM evidence_timeline_events WHERE run_id = ?",
            (report.run_id,),
        ).fetchone()[0]
    assert remaining == 0
    repository.close()


def test_partial_run_exposes_events_and_artifacts_with_empty_timeline(
    tmp_path: Path,
) -> None:
    report = replay_fixture(Path("fixtures/clean-success"))
    repository = SQLiteReportRepository(tmp_path / "reports.sqlite3")
    created_at = datetime(2026, 9, 3, tzinfo=UTC)
    artifact = EvidenceArtifact(
        artifact_id=f"{report.run_id}:preflight",
        artifact_type=ArtifactType.SERVICE_CONTRACT,
        source="synthetic.live",
        collected_at=created_at,
        redacted=False,
        data={"recipient": "syn_live_recipient"},
    )
    repository.begin_run(
        report.run_id,
        task=report.intent.task,
        provenance=RunProvenance.EXTERNAL_LIVE,
        created_at=created_at,
    )
    repository.save_artifacts(report.run_id, (artifact,))

    assert repository.timeline(report.run_id) == ()
    assert repository.events(report.run_id)
    assert repository.artifacts(report.run_id)
    repository.close()


def test_observed_contract_snapshots_preserve_observation_order(
    tmp_path: Path,
) -> None:
    report = replay_fixture(Path("fixtures/x402-clean-success"))
    repository = SQLiteReportRepository(tmp_path / "reports.sqlite3")
    repository.save(report)
    assert report.contract is not None
    snapshot_a = build_contract_snapshot(
        report.contract.url, "x402", report.contract, cast(JsonValue, {"v": 1})
    )
    snapshot_b = build_contract_snapshot(
        report.contract.url, "x402", report.contract, cast(JsonValue, {"v": 2})
    )
    repository.save_contract_snapshot(snapshot_a, datetime(2026, 9, 1, tzinfo=UTC))
    repository.save_contract_snapshot(snapshot_b, datetime(2026, 9, 2, tzinfo=UTC))
    repository.save_contract_snapshot(snapshot_a, datetime(2026, 9, 3, tzinfo=UTC))

    observed = repository.observed_contract_snapshots(report.contract.url, "x402")

    assert [s.snapshot_digest for s in observed] == [
        snapshot_a.snapshot_digest,
        snapshot_b.snapshot_digest,
        snapshot_a.snapshot_digest,
    ]
    assert [
        s.snapshot_digest for s in repository.contract_snapshots(report.contract.url, "x402")
    ] == [
        snapshot_a.snapshot_digest,
        snapshot_b.snapshot_digest,
    ]
    repository.close()


def test_observed_contract_snapshots_read_does_not_mutate(tmp_path: Path) -> None:
    report = replay_fixture(Path("fixtures/x402-clean-success"))
    repository = SQLiteReportRepository(tmp_path / "reports.sqlite3")
    repository.save(report)
    assert report.contract is not None
    snapshot = build_contract_snapshot(
        report.contract.url, "x402", report.contract, cast(JsonValue, {"v": 1})
    )
    repository.save_contract_snapshot(snapshot, datetime(2026, 9, 1, tzinfo=UTC))
    repository.save_contract_snapshot(snapshot, datetime(2026, 9, 2, tzinfo=UTC))

    database = tmp_path / "reports.sqlite3"
    with closing(sqlite3.connect(database)) as connection:
        counts_before = {
            table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("contract_snapshots", "contract_snapshot_observations")
        }
        rows_before = connection.execute(
            "SELECT snapshot_digest, observed_at FROM contract_snapshot_observations "
            "ORDER BY observation_id"
        ).fetchall()

    repository.observed_contract_snapshots(report.contract.url, "x402")

    with closing(sqlite3.connect(database)) as connection:
        counts_after = {
            table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("contract_snapshots", "contract_snapshot_observations")
        }
        rows_after = connection.execute(
            "SELECT snapshot_digest, observed_at FROM contract_snapshot_observations "
            "ORDER BY observation_id"
        ).fetchall()
    assert counts_after == counts_before
    assert rows_after == rows_before
    repository.close()


def test_observed_contract_snapshots_rejects_unsupported_rail(tmp_path: Path) -> None:
    repository = SQLiteReportRepository(tmp_path / "reports.sqlite3")

    with pytest.raises(ValueError, match="unsupported contract snapshot rail"):
        repository.observed_contract_snapshots("https://example.invalid/x", "other")
    repository.close()


def test_database_schema_four_copy_migrates_through_every_new_migration(
    tmp_path: Path,
) -> None:
    migrations = Path("src/settlediff/storage/migrations")
    database = tmp_path / "legacy.sqlite3"
    report = replay_fixture(Path("fixtures/clean-success"))
    event = RunEvent(state=RunState.COMPLETE, occurred_at=report.intent.created_at)
    with closing(sqlite3.connect(database)) as connection:
        for version in range(1, 4):
            sql = next(migrations.glob(f"{version:03d}_*.sql")).read_text()
            connection.executescript(sql)
            connection.execute("INSERT INTO schema_migrations(version) VALUES (?)", (version,))
        connection.execute(
            "INSERT INTO reports(run_id, report_json) VALUES (?, ?)",
            (report.run_id, redact_report(report).model_dump_json()),
        )
        connection.execute(
            "INSERT INTO run_events(run_id, position, event_json) VALUES (?, 0, ?)",
            (report.run_id, event.model_dump_json()),
        )
        artifact = EvidenceArtifact(
            artifact_id=f"{report.run_id}:migration_contract",
            artifact_type=ArtifactType.SERVICE_CONTRACT,
            source="synthetic.migration",
            collected_at=report.intent.created_at,
            redacted=True,
            data={"synthetic": True},
        )
        connection.execute(
            "INSERT INTO artifacts(run_id, artifact_id, artifact_json) VALUES (?, ?, ?)",
            (report.run_id, artifact.artifact_id, artifact.model_dump_json()),
        )
        explanation = ExplanationRecord(
            explanation=InvestigationExplanation(
                run_id=report.run_id,
                summary="Synthetic migration explanation.",
                evidence_used=(artifact.artifact_id,),
                finding_ids=tuple(f.finding_id for f in report.findings),
                deterministic_verdict=report.verdict,
                recommended_next_step=None,
            ),
            source=ExplanationSource.FALLBACK,
            tool_calls=0,
        )
        connection.execute(
            "INSERT INTO explanations(run_id, explanation_json) VALUES (?, ?)",
            (report.run_id, explanation.model_dump_json()),
        )
        migration_four = next(migrations.glob("004_*.sql"))
        connection.executescript(migration_four.read_text())
        connection.execute("INSERT INTO schema_migrations(version) VALUES (4)")
        connection.commit()

    repository = SQLiteReportRepository(database)
    with closing(sqlite3.connect(database)) as connection:
        versions = {
            row[0] for row in connection.execute("SELECT version FROM schema_migrations").fetchall()
        }
        assert versions == {1, 2, 3, 4, 5, 6}
        legacy_events = connection.execute(
            "SELECT event_json FROM run_events WHERE run_id = ?", (report.run_id,)
        ).fetchall()
        record_events = connection.execute(
            "SELECT event_json FROM run_record_events WHERE run_id = ?", (report.run_id,)
        ).fetchall()
        legacy_artifacts = connection.execute(
            "SELECT artifact_json FROM artifacts WHERE run_id = ?", (report.run_id,)
        ).fetchall()
        record_artifacts = connection.execute(
            "SELECT artifact_json FROM run_record_artifacts WHERE run_id = ?",
            (report.run_id,),
        ).fetchall()
        legacy_explanations = connection.execute(
            "SELECT explanation_json FROM explanations WHERE run_id = ?", (report.run_id,)
        ).fetchall()
        record_explanations = connection.execute(
            "SELECT explanation_json FROM run_record_explanations WHERE run_id = ?",
            (report.run_id,),
        ).fetchall()
        timeline_rows = connection.execute(
            "SELECT COUNT(*) FROM evidence_timeline_events WHERE run_id = ?",
            (report.run_id,),
        ).fetchone()[0]
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master").fetchall()}
        triggers = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'trigger'"
            ).fetchall()
        }
    assert len(legacy_events) == 1 and len(record_events) == 1
    assert len(legacy_artifacts) == 1 and len(record_artifacts) == 1
    assert len(legacy_explanations) == 1 and len(record_explanations) == 1
    assert repository.get(report.run_id) == redact_report(report)
    assert repository.events(report.run_id) == (event,)
    assert repository.artifacts(report.run_id) == (artifact,)
    assert repository.explanation(report.run_id) == explanation
    assert "evidence_timeline_events" in tables
    assert {"contract_snapshots", "contract_snapshot_observations"} <= tables
    assert triggers == {
        "contract_snapshots_no_update",
        "contract_snapshots_no_delete",
        "contract_snapshot_observations_no_update",
        "contract_snapshot_observations_no_delete",
    }

    timeline = repository.timeline(report.run_id)
    assert timeline
    assert timeline_rows == len(timeline)
    migrated = next(e for e in timeline if e.source == "synthetic.migration")
    assert artifact.artifact_id in migrated.artifact_ids
    assert migrated.source_time is None

    assert report.contract is not None
    snapshot = build_contract_snapshot(
        report.contract.url, "perflo", report.contract, cast(JsonValue, {"v": 1})
    )
    repository.save_contract_snapshot(snapshot, datetime(2026, 9, 1, tzinfo=UTC))
    assert repository.latest_contract_snapshot(report.contract.url, "perflo") == snapshot
    repository.close()

    reopened = SQLiteReportRepository(database)
    with closing(sqlite3.connect(database)) as connection:
        versions = {
            row[0] for row in connection.execute("SELECT version FROM schema_migrations").fetchall()
        }
        reopened_rows = connection.execute(
            "SELECT COUNT(*) FROM evidence_timeline_events WHERE run_id = ?",
            (report.run_id,),
        ).fetchone()[0]
    assert versions == {1, 2, 3, 4, 5, 6}
    assert reopened_rows == len(timeline)
    assert reopened.timeline(report.run_id) == timeline
    reopened.close()
