from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from settlediff.api.app import create_app
from settlediff.application.replay import replay_fixture
from settlediff.storage.sqlite import SQLiteReportRepository


def test_runs_and_detail_are_persisted_and_escaped(tmp_path: Path) -> None:
    repository = SQLiteReportRepository(tmp_path / "reports.sqlite3")
    report = replay_fixture(Path("fixtures/clean-success"))
    repository.save(report)
    client = TestClient(create_app(repository))
    response = client.get("/runs")
    assert response.status_code == 200
    assert report.run_id in response.text
    assert response.headers["content-security-policy"].startswith("default-src")
    assert client.get(f"/runs/{report.run_id}").status_code == 200
    assert client.get("/runs/not-found").status_code == 404
