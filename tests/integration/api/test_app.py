from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient

from settlediff.api.app import create_app
from settlediff.application.replay import replay_fixture
from settlediff.application.run import RunState, RunTimeline
from settlediff.domain.models import ArtifactType, EvidenceArtifact
from settlediff.storage.sqlite import SQLiteReportRepository


def test_runs_and_detail_are_persisted_and_escaped(tmp_path: Path) -> None:
    repository = SQLiteReportRepository(tmp_path / "reports.sqlite3")
    report = replay_fixture(Path("fixtures/clean-success"))
    timeline = RunTimeline()
    timeline.transition(RunState.AUTHORIZED)
    repository.save(
        report,
        events=timeline.events,
        artifacts=(
            EvidenceArtifact(
                artifact_id="artifact:unsafe",
                artifact_type=ArtifactType.SERVICE_RESPONSE,
                source="test",
                collected_at=datetime(2026, 8, 13, tzinfo=UTC),
                redacted=False,
                data={"message": "<script>alert(1)</script>"},
            ),
        ),
    )
    client = TestClient(create_app(repository))
    response = client.get("/runs")
    assert response.status_code == 200
    assert report.run_id in response.text
    assert response.headers["content-security-policy"].startswith("default-src")
    assert client.get("/static/settlediff.css").status_code == 200
    assert client.get(f"/runs/{report.run_id}").status_code == 200
    events = client.get(f"/runs/{report.run_id}/events")
    assert events.status_code == 200
    assert events.json()[-1]["state"] == "authorized"
    artifacts = client.get(f"/runs/{report.run_id}/artifacts")
    assert artifacts.status_code == 200
    assert "&lt;script&gt;" in artifacts.text
    assert client.get("/runs/not-found").status_code == 404


def test_all_fixture_reports_render_without_recomputing(tmp_path: Path) -> None:
    repository = SQLiteReportRepository(tmp_path / "reports.sqlite3")
    reports = tuple(
        replay_fixture(path) for path in sorted(Path("fixtures").iterdir()) if path.is_dir()
    )
    for report in reports:
        repository.save(report)
    client = TestClient(create_app(repository))
    listing = client.get("/runs")
    assert listing.status_code == 200
    for report in reports:
        detail = client.get(f"/runs/{report.run_id}")
        assert detail.status_code == 200
        assert report.verdict.value in detail.text
        for section in ("Expected", "Executed", "Recorded"):
            assert f"<h2>{section}</h2>" in detail.text
