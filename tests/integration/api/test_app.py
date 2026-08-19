from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha384
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from settlediff.api.app import create_app
from settlediff.application.replay import replay_fixture
from settlediff.application.run import RunState, RunTimeline
from settlediff.domain.models import (
    ArtifactType,
    EvidenceArtifact,
    ExplanationRecord,
    ExplanationSource,
    InvestigationExplanation,
    MachineReport,
)
from settlediff.storage.sqlite import SQLiteReportRepository


def explanation_record(
    report: MachineReport,
    *,
    source: ExplanationSource = ExplanationSource.PROVIDER,
    summary: str = "The persisted evidence supports the deterministic verdict.",
    evidence_used: tuple[str, ...] = ("artifact:explanation",),
    finding_ids: tuple[str, ...] | None = None,
    recommended_next_step: str | None = "Review the cited settlement record.",
    tool_calls: int = 2,
) -> ExplanationRecord:
    return ExplanationRecord(
        explanation=InvestigationExplanation(
            run_id=report.run_id,
            summary=summary,
            evidence_used=evidence_used,
            finding_ids=finding_ids or (report.findings[0].finding_id,),
            deterministic_verdict=report.verdict,
            recommended_next_step=recommended_next_step,
        ),
        source=source,
        tool_calls=tool_calls,
    )


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


def test_run_detail_renders_context_evidence_and_links_artifacts(tmp_path: Path) -> None:
    repository = SQLiteReportRepository(tmp_path / "reports.sqlite3")
    report = replay_fixture(Path("fixtures/clean-success"))
    context = EvidenceArtifact(
        artifact_id=f"{report.run_id}:contextdev",
        artifact_type=ArtifactType.CONTEXT_EVIDENCE,
        source="contextdev",
        collected_at=datetime(2026, 8, 18, tzinfo=UTC),
        redacted=True,
        data={
            "state": "PRESENT",
            "status_url": "https://status.example.invalid/outage",
            "diagnostic": "exact_claim_present",
            "observed_at": "2026-08-18T00:00:00Z",
            "error_class": None,
            "body_bytes": 812,
            "excerpt": "Synthetic status evidence",
        },
    )
    repository.save(report, artifacts=(context,))
    client = TestClient(create_app(repository))

    detail = client.get(f"/runs/{report.run_id}")

    assert detail.status_code == 200
    assert "Context evidence" in detail.text
    assert "PRESENT" in detail.text
    assert "Exact Claim Present" in detail.text
    assert "https://status.example.invalid/outage" in detail.text
    assert "812 bytes" in detail.text
    assert "Synthetic status evidence" in detail.text
    assert (
        f"/runs/{report.run_id}/artifacts#{context.artifact_id.replace(':', '-')}"
    ) in detail.text
    repository.close()


def test_artifact_viewer_groups_redacted_artifacts_and_links_metadata(tmp_path: Path) -> None:
    repository = SQLiteReportRepository(tmp_path / "reports.sqlite3")
    report = replay_fixture(Path("fixtures/clean-success"))
    artifact = EvidenceArtifact(
        artifact_id=f"{report.run_id}:contextdev",
        artifact_type=ArtifactType.CONTEXT_EVIDENCE,
        source="contextdev",
        collected_at=datetime(2026, 8, 18, tzinfo=UTC),
        redacted=True,
        data={"note": "synthetic"},
    )
    repository.save(report, artifacts=(artifact,))
    client = TestClient(create_app(repository))

    page = client.get(f"/runs/{report.run_id}/artifacts")

    assert page.status_code == 200
    assert "Evidence artifacts" in page.text
    assert "contextdev" in page.text
    assert "context evidence" in page.text
    assert "Redacted" in page.text
    assert "2026-08-18" in page.text
    assert 'id="syn_run_clean-contextdev"' in page.text
    assert "<pre" in page.text
    assert "&#34;note&#34;" in page.text
    repository.close()


def test_run_detail_renders_persisted_recovery_evidence(tmp_path: Path) -> None:
    repository = SQLiteReportRepository(tmp_path / "reports.sqlite3")
    report = replay_fixture(Path("fixtures/clean-success"))
    artifact = EvidenceArtifact(
        artifact_id=f"{report.run_id}:recovery",
        artifact_type=ArtifactType.ACTIVITY,
        source="perflo.activity",
        collected_at=datetime(2026, 8, 18, tzinfo=UTC),
        redacted=True,
        data={"records": []},
    )
    repository.save(report, artifacts=(artifact,))
    client = TestClient(create_app(repository))

    detail = client.get(f"/runs/{report.run_id}")

    assert detail.status_code == 200
    assert "Submission recovery" in detail.text
    assert "Unresolved" in detail.text
    assert "No proof of non-submission" in detail.text
    assert "activity history" in detail.text
    repository.close()


def test_run_detail_renders_provider_explanation(tmp_path: Path) -> None:
    repository = SQLiteReportRepository(tmp_path / "reports.sqlite3")
    report = replay_fixture(Path("fixtures/clean-success"))
    record = explanation_record(report)
    repository.save(report, explanation=record)
    client = TestClient(create_app(repository))

    detail = client.get(f"/runs/{report.run_id}")

    assert detail.status_code == 200
    assert "Grounded explanation" in detail.text
    assert 'class="explanation-source explanation-source-provider">PROVIDER</span>' in detail.text
    assert record.explanation.summary in detail.text
    assert record.explanation.recommended_next_step is not None
    assert record.explanation.recommended_next_step in detail.text
    assert "2 tool calls" in detail.text
    repository.close()


def test_run_detail_renders_fallback_explanation_provenance(tmp_path: Path) -> None:
    repository = SQLiteReportRepository(tmp_path / "reports.sqlite3")
    report = replay_fixture(Path("fixtures/clean-success"))
    record = explanation_record(
        report,
        source=ExplanationSource.FALLBACK,
        summary="Deterministic fallback retained the recorded verdict.",
        recommended_next_step=None,
        tool_calls=0,
    )
    repository.save(report, explanation=record)
    client = TestClient(create_app(repository))

    detail = client.get(f"/runs/{report.run_id}")

    assert detail.status_code == 200
    assert 'class="explanation-source explanation-source-fallback">FALLBACK</span>' in detail.text
    assert record.explanation.summary in detail.text
    assert "Recommended next step" not in detail.text
    assert "0 tool calls" in detail.text
    repository.close()


def test_run_detail_renders_missing_explanation_state(tmp_path: Path) -> None:
    repository = SQLiteReportRepository(tmp_path / "reports.sqlite3")
    report = replay_fixture(Path("fixtures/clean-success"))
    repository.save(report)
    client = TestClient(create_app(repository))

    detail = client.get(f"/runs/{report.run_id}")

    assert detail.status_code == 200
    assert "Grounded explanation" in detail.text
    assert "No persisted explanation" in detail.text
    assert "PROVIDER" not in detail.text
    assert "FALLBACK" not in detail.text
    repository.close()


def test_run_detail_escapes_provider_explanation_content(tmp_path: Path) -> None:
    repository = SQLiteReportRepository(tmp_path / "reports.sqlite3")
    report = replay_fixture(Path("fixtures/clean-success"))
    record = explanation_record(
        report,
        summary='<script>alert("summary")</script>',
        evidence_used=("<img src=x onerror=alert(1)>",),
        finding_ids=("<script>finding</script>",),
        recommended_next_step='<a href="https://example.invalid">continue</a>',
    )
    repository.save(report, explanation=record)
    client = TestClient(create_app(repository))

    detail = client.get(f"/runs/{report.run_id}")

    assert detail.status_code == 200
    assert '<script>alert("summary")</script>' not in detail.text
    assert "&lt;script&gt;alert(&#34;summary&#34;)&lt;/script&gt;" in detail.text
    assert "<img src=x onerror=alert(1)>" not in detail.text
    assert "&lt;img src=x onerror=alert(1)&gt;" in detail.text
    assert '<a href="https://example.invalid">continue</a>' not in detail.text
    assert "&lt;a href=&#34;https://example.invalid&#34;&gt;continue&lt;/a&gt;" in detail.text
    repository.close()


def test_run_detail_links_findings_and_only_mapped_evidence_citations(tmp_path: Path) -> None:
    repository = SQLiteReportRepository(tmp_path / "reports.sqlite3")
    report = replay_fixture(Path("fixtures/chain-diff"))
    artifact = EvidenceArtifact(
        artifact_id="artifact:linked",
        artifact_type=ArtifactType.CONTEXT_EVIDENCE,
        source="test",
        collected_at=datetime(2026, 8, 13, tzinfo=UTC),
        redacted=True,
        data={"status": "persisted"},
    )
    finding = report.findings[0]
    record = explanation_record(
        report,
        evidence_used=(artifact.artifact_id, "artifact:missing"),
        finding_ids=(finding.finding_id,),
    )
    repository.save(report, artifacts=(artifact,), explanation=record)
    client = TestClient(create_app(repository))

    detail = client.get(f"/runs/{report.run_id}")

    assert detail.status_code == 200
    assert (
        f'<a class="citation-chip" href="#finding-{finding.check_id}">{finding.finding_id}</a>'
        in detail.text
    )
    assert (
        f'<a class="citation-chip" href="/runs/{report.run_id}/artifacts#artifact-linked">'
        f"{artifact.artifact_id}</a>"
    ) in detail.text
    assert '<span class="citation-chip">artifact:missing</span>' in detail.text
    assert 'href="#artifact:missing"' not in detail.text
    repository.close()


def test_run_detail_loads_persisted_explanation_without_recomputation(tmp_path: Path) -> None:
    repository = SQLiteReportRepository(tmp_path / "reports.sqlite3")
    report = replay_fixture(Path("fixtures/clean-success"))
    record = explanation_record(report)
    repository.save(report, explanation=record)
    client = TestClient(create_app(repository))

    with (
        patch.object(repository, "explanation", wraps=repository.explanation) as load,
        patch("settlediff.domain.checks.run_checks") as run_checks,
        patch("settlediff.agent.investigator.investigate") as investigate,
    ):
        detail = client.get(f"/runs/{report.run_id}")

    assert detail.status_code == 200
    load.assert_called_once_with(report.run_id)
    run_checks.assert_not_called()
    investigate.assert_not_called()
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
    complete.transition(RunState.EXPLAINING)
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
