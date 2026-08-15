from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha384
from pathlib import Path

from fastapi.testclient import TestClient

from settlediff.api.app import create_app
from settlediff.application.replay import replay_fixture
from settlediff.application.run import RunState, RunTimeline
from settlediff.domain.models import ArtifactType, EvidenceArtifact
from settlediff.storage.sqlite import SQLiteReportRepository


def test_root_redirects_to_run_records(tmp_path: Path) -> None:
    repository = SQLiteReportRepository(tmp_path / "reports.sqlite3")
    client = TestClient(create_app(repository))

    response = client.get("/", follow_redirects=False)

    assert response.status_code == 307
    assert response.headers["location"] == "/runs"
    repository.close()


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
    assert client.get("/static/htmx.min.js").status_code == 200
    detail = client.get(f"/runs/{report.run_id}")
    assert detail.status_code == 200
    assert "hx-get" in detail.text
    assert "0.01 USDC" in detail.text
    assert "syn_recipient" not in detail.text
    assert 'class="verdict verdict-verified"' in detail.text
    assert "Expected · Executed · Recorded" in detail.text
    events = client.get(f"/runs/{report.run_id}/events")
    assert events.status_code == 200
    assert events.json()[-1]["state"] == "authorized"
    event_fragment = client.get(f"/runs/{report.run_id}/events-fragment")
    assert event_fragment.status_code == 200
    assert "authorized" in event_fragment.text
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
    assert "Local evidence ledger" in listing.text
    for report in reports:
        detail = client.get(f"/runs/{report.run_id}")
        assert detail.status_code == 200
        assert report.verdict.value in detail.text
        for heading in ("Expected", "Executed", "Recorded"):
            assert f"<th>{heading}</th>" in detail.text


def test_runs_support_search_verdict_filter_and_deterministic_sort(tmp_path: Path) -> None:
    repository = SQLiteReportRepository(tmp_path / "reports.sqlite3")
    clean = replay_fixture(Path("fixtures/clean-success"))
    paid_failure = replay_fixture(Path("fixtures/paid-failure"))
    repository.save(clean)
    repository.save(paid_failure)
    client = TestClient(create_app(repository))

    searched = client.get("/runs", params={"q": "paid failure"})
    assert searched.status_code == 200
    assert paid_failure.run_id in searched.text
    assert clean.run_id not in searched.text
    assert 'name="q"' in searched.text

    filtered = client.get("/runs", params={"verdict": "PAID_FAILURE"})
    assert filtered.status_code == 200
    assert paid_failure.run_id in filtered.text
    assert clean.run_id not in filtered.text
    assert 'option value="PAID_FAILURE" selected' in filtered.text

    sorted_response = client.get("/runs", params={"sort": "verdict"})
    assert sorted_response.status_code == 200
    assert sorted_response.text.index(paid_failure.run_id) < sorted_response.text.index(
        clean.run_id
    )
    repository.close()


def test_evidence_diff_uses_persisted_findings_and_links_citations(tmp_path: Path) -> None:
    repository = SQLiteReportRepository(tmp_path / "reports.sqlite3")
    report = replay_fixture(Path("fixtures/chain-diff"))
    repository.save(report)
    client = TestClient(create_app(repository))

    detail = client.get(f"/runs/{report.run_id}", params={"differences": "1"})

    assert detail.status_code == 200
    assert 'id="evidence-chain"' in detail.text
    assert 'data-status="DIFF"' in detail.text
    assert 'href="#evidence-chain"' in detail.text
    assert 'id="evidence-price"' not in detail.text
    assert "Showing persisted non-pass findings only" in detail.text
    repository.close()


def test_event_task_rows_stop_polling_after_terminal_state(tmp_path: Path) -> None:
    repository = SQLiteReportRepository(tmp_path / "reports.sqlite3")
    report = replay_fixture(Path("fixtures/clean-success"))
    active = RunTimeline()
    active.transition(RunState.AUTHORIZED)
    repository.save(report, events=active.events)
    client = TestClient(create_app(repository))

    active_fragment = client.get(f"/runs/{report.run_id}/events-fragment")
    assert active_fragment.status_code == 200
    assert 'hx-trigger="every 10s"' in active_fragment.text
    assert 'data-current="true"' in active_fragment.text
    assert "authorized" in active_fragment.text

    complete = RunTimeline()
    complete.transition(RunState.AUTHORIZED)
    complete.transition(RunState.EXECUTING)
    complete.transition(RunState.VERIFYING)
    complete.transition(RunState.COMPLETE)
    repository.save(report, events=complete.events)

    terminal_fragment = client.get(f"/runs/{report.run_id}/events-fragment")
    assert terminal_fragment.status_code == 200
    assert 'hx-trigger="every 10s"' not in terminal_fragment.text
    assert 'data-terminal="true"' in terminal_fragment.text
    assert "complete" in terminal_fragment.text
    repository.close()


def test_vendored_htmx_checksum_is_recorded() -> None:
    static = Path("src/settlediff/ui/static")
    expected = (
        "1f94ab71fca01e602e4c366984c1ea0492dcdc586cb0a8c6ef0fc2782a4545e49"
        "fc015834caa64ccf3fc73e70bb0af95"
    )
    assert sha384((static / "htmx.min.js").read_bytes()).hexdigest() == expected
    assert expected in (static / "HTMX-SOURCE.md").read_text()
