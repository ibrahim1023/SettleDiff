from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from settlediff.application.replay import replay_fixture
from settlediff.application.run import RunState, RunTimeline
from settlediff.domain.models import ArtifactType, EvidenceArtifact
from settlediff.storage.sqlite import SQLiteReportRepository


def test_report_round_trips_through_sqlite(tmp_path: Path) -> None:
    report = replay_fixture(Path("fixtures/clean-success"))
    repository = SQLiteReportRepository(tmp_path / "reports.sqlite3")
    repository.save(report)
    loaded = repository.get(report.run_id)
    assert loaded is not None
    assert loaded.model_dump(mode="json") == report.model_dump(mode="json")
    repository.close()


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
