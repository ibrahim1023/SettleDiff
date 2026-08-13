from __future__ import annotations

from pathlib import Path

from settlediff.application.replay import replay_fixture
from settlediff.storage.sqlite import SQLiteReportRepository


def test_report_round_trips_through_sqlite(tmp_path: Path) -> None:
    report = replay_fixture(Path("fixtures/clean-success"))
    repository = SQLiteReportRepository(tmp_path / "reports.sqlite3")
    repository.save(report)
    loaded = repository.get(report.run_id)
    assert loaded is not None
    assert loaded.model_dump(mode="json") == report.model_dump(mode="json")
    repository.close()
