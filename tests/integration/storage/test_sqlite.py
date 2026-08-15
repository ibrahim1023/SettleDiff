from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest

from settlediff.application.replay import replay_fixture
from settlediff.application.run import RunState, RunTimeline
from settlediff.domain.models import ArtifactType, EvidenceArtifact
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
    assert loaded.findings == report.findings
    assert CANARY not in stored_json
    assert stored_json.count("[REDACTED]") == 2
    assert CANARY in report.model_dump_json()
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
