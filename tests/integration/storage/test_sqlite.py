from __future__ import annotations

from pathlib import Path

from settlediff.application.replay import replay_fixture
from settlediff.application.run import RunState, RunTimeline
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
