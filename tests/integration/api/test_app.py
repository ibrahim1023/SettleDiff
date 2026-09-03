from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from hashlib import sha384
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from settlediff.api.app import create_app
from settlediff.application.replay import replay_fixture
from settlediff.application.run import RunEvent, RunFailure, RunProvenance, RunState, RunTimeline
from settlediff.domain.models import (
    ArtifactType,
    AssetIdentity,
    CheckStatus,
    EvidenceArtifact,
    ExplanationRecord,
    ExplanationSource,
    Finding,
    InvestigationExplanation,
    MachineReport,
    PaymentReceipt,
    SettlementStatus,
    Severity,
)
from settlediff.domain.money import Money
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


def test_diagnostics_show_safe_contract_versions(tmp_path: Path) -> None:
    repository = SQLiteReportRepository(tmp_path / "reports.sqlite3")
    client = TestClient(create_app(repository))

    response = client.get("/diagnostics")

    assert response.status_code == 200
    assert "SettleDiff 0.1.0" in response.text
    assert "Report schema" in response.text and ">2<" in response.text
    assert "Database schema" in response.text and ">4<" in response.text
    assert "Bundle schema" in response.text and ">2<" in response.text
    assert "/web/scrape/markdown" in response.text
    assert "x402 protocol" in response.text and ">2<" in response.text
    assert "<dt>x402 signer schema</dt><dd>2</dd>" in response.text
    assert "Not recorded" in response.text
    repository.close()


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
    csp = response.headers["content-security-policy"]
    assert csp == "default-src 'self'; script-src 'self'; style-src 'self'"
    assert "unsafe-inline" not in csp
    assert client.get("/static/settlediff.css").status_code == 200
    assert client.get("/static/htmx.min.js").status_code == 200
    assert client.get("/static/settlediff.js").status_code == 200
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


def test_x402_detail_labels_adapter_and_separate_settlement_evidence(tmp_path: Path) -> None:
    repository = SQLiteReportRepository(tmp_path / "reports.sqlite3")
    report = replay_fixture(Path("fixtures/x402-clean-success")).model_copy(
        update={"adapter_id": "x402"}
    )
    repository.save(report)

    detail = TestClient(create_app(repository)).get(f"/runs/{report.run_id}")

    assert detail.status_code == 200
    assert "Payment rail: x402" in detail.text
    assert "Provider receipt: settled" in detail.text
    assert "Independent record: confirmed" in detail.text
    repository.close()


def test_runs_support_search_verdict_filter_and_deterministic_sort(tmp_path: Path) -> None:
    repository = SQLiteReportRepository(tmp_path / "reports.sqlite3")
    clean = replay_fixture(Path("fixtures/clean-success"))
    paid_failure = replay_fixture(Path("fixtures/paid-failure"))
    timeline = RunTimeline()
    timeline.transition(RunState.AUTHORIZED)
    timeline.transition(RunState.EXECUTING)
    timeline.transition(RunState.VERIFYING)
    timeline.transition(RunState.EXPLAINING)
    timeline.transition(RunState.COMPLETE)
    repository.save(clean, events=timeline.events)
    repository.save(paid_failure)
    repository.begin_run(
        "live_active",
        task="External active search",
        provenance=RunProvenance.EXTERNAL_LIVE,
        created_at=datetime(2026, 9, 3, tzinfo=UTC),
    )
    client = TestClient(create_app(repository))

    searched = client.get("/runs", params={"q": "paid failure"})
    assert searched.status_code == 200
    assert paid_failure.run_id in searched.text
    assert clean.run_id not in searched.text
    assert 'name="q"' in searched.text
    assert 'placeholder="Task text or run ID"' in searched.text

    blank_filters = client.get(
        "/runs",
        params={"q": clean.run_id, "verdict": "", "state": "", "sort": "newest"},
    )
    assert blank_filters.status_code == 200
    assert clean.run_id in blank_filters.text
    assert paid_failure.run_id not in blank_filters.text

    filtered = client.get("/runs", params={"verdict": "PAID_FAILURE"})
    assert filtered.status_code == 200
    assert paid_failure.run_id in filtered.text
    assert clean.run_id not in filtered.text
    assert 'option value="PAID_FAILURE" selected' in filtered.text

    state_filtered = client.get("/runs", params={"state": "complete"})
    assert state_filtered.status_code == 200
    assert clean.run_id in state_filtered.text
    assert paid_failure.run_id in state_filtered.text
    assert "live_active" not in state_filtered.text
    assert 'option value="complete" selected' in state_filtered.text

    provenance_filtered = client.get("/runs", params={"provenance": "external_live"})
    assert provenance_filtered.status_code == 200
    assert "live_active" in provenance_filtered.text
    assert clean.run_id not in provenance_filtered.text
    assert 'option value="external_live" selected' in provenance_filtered.text

    sorted_response = client.get("/runs", params={"sort": "verdict"})
    assert sorted_response.status_code == 200
    assert sorted_response.text.index(paid_failure.run_id) < sorted_response.text.index(
        clean.run_id
    )
    repository.close()


def test_incomplete_live_run_remains_visible_with_safe_failure_and_artifacts(
    tmp_path: Path,
) -> None:
    repository = SQLiteReportRepository(tmp_path / "reports.sqlite3")
    created_at = datetime(2026, 9, 3, tzinfo=UTC)
    repository.begin_run(
        "live_failed",
        task="External x402 request",
        provenance=RunProvenance.EXTERNAL_LIVE,
        created_at=created_at,
    )
    repository.save_artifacts(
        "live_failed",
        (
            EvidenceArtifact(
                artifact_id="live_failed:contract",
                artifact_type=ArtifactType.SERVICE_CONTRACT,
                source="x402.challenge",
                collected_at=created_at,
                redacted=False,
                data={"recipient": "syn_sensitive_recipient"},
            ),
        ),
    )
    repository.append_event("live_failed", RunEvent(state=RunState.FAILED, occurred_at=created_at))
    repository.record_failure(
        "live_failed",
        RunFailure(
            stage=RunState.EXECUTING,
            error_class="PermissionError",
            diagnostic="executing failed",
            submission_uncertain=False,
            occurred_at=created_at,
        ),
    )
    client = TestClient(create_app(repository))

    listing = client.get("/runs")
    detail = client.get("/runs/live_failed")
    artifacts = client.get("/runs/live_failed/artifacts")

    assert listing.status_code == 200
    assert "live_failed" in listing.text
    assert "external live" in listing.text
    assert "No final report" in listing.text
    assert 'hx-trigger="every 3s"' in listing.text
    assert detail.status_code == 200
    assert "Safe failure evidence" in detail.text
    assert "PermissionError" in detail.text
    assert "Submission uncertain</dt><dd>No" in detail.text
    assert artifacts.status_code == 200
    assert "syn_sensitive_recipient" not in artifacts.text
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


def test_run_detail_renders_rail_neutral_payment_evidence(tmp_path: Path) -> None:
    report = replay_fixture(Path("fixtures/clean-success"))
    assert report.contract is not None
    assert report.execution is not None
    assert report.ledger is not None
    identity = AssetIdentity(
        symbol="USDC",
        network="eip155:84532",
        reference="syn_usdc_base_sepolia",
        decimals=6,
    )
    receipt = PaymentReceipt(
        amount=Money(amount=Decimal("0.001"), unit="USDC"),
        asset="USDC",
        protocol="x402",
        chain=None,
        recipient="syn_recipient",
        scheme="exact",
        network="eip155:84532",
        asset_identity=identity,
        settlement_status=SettlementStatus.SETTLED,
        transaction_id=None,
        session_id=None,
        transaction_hash="syn_hash",
        issued_at=datetime(2026, 8, 31, tzinfo=UTC),
    )
    network_finding = Finding(
        finding_id="check:network",
        check_id="network",
        severity=Severity.INFO,
        status=CheckStatus.PASS,
        expected="eip155:84532",
        observed="eip155:84532",
        message="Network values agree across available evidence.",
        artifact_ids=("contract", "receipt", "activity"),
        field_paths=("contract.network", "receipt.network", "activity.network"),
    )
    identity_finding = Finding(
        finding_id="check:asset_identity",
        check_id="asset_identity",
        severity=Severity.INFO,
        status=CheckStatus.PASS,
        expected=identity.model_dump(mode="json"),
        observed=identity.model_dump(mode="json"),
        message="Asset identities agree across available evidence.",
        artifact_ids=("contract", "receipt", "activity"),
        field_paths=(
            "contract.asset_identity",
            "receipt.asset_identity",
            "activity.asset_identity",
        ),
    )
    report = report.model_copy(
        update={
            "contract": report.contract.model_copy(
                update={
                    "protocol": "x402",
                    "chain": None,
                    "scheme": "exact",
                    "network": "eip155:84532",
                    "asset_identity": identity,
                    "recipient": "syn_recipient",
                }
            ),
            "execution": report.execution.model_copy(
                update={
                    "protocol": "x402",
                    "chain": None,
                    "scheme": "exact",
                    "network": "eip155:84532",
                    "asset_identity": identity,
                    "settlement_status": SettlementStatus.UNKNOWN,
                }
            ),
            "receipt": receipt,
            "ledger": report.ledger.model_copy(
                update={
                    "protocol": "x402",
                    "chain": None,
                    "scheme": "exact",
                    "network": "eip155:84532",
                    "asset_identity": identity,
                }
            ),
            "findings": (*report.findings, network_finding, identity_finding),
        }
    )
    repository = SQLiteReportRepository(tmp_path / "reports.sqlite3")
    repository.save(report)
    client = TestClient(create_app(repository))

    detail = client.get(f"/runs/{report.run_id}")

    assert detail.status_code == 200
    assert 'id="evidence-network"' in detail.text
    assert 'id="evidence-asset_identity"' in detail.text
    assert "eip155:84532" in detail.text
    assert "USDC · eip155:84532 · syn_…olia" in detail.text
    assert "Provider receipt: settled" in detail.text
    assert "Independent record: confirmed" in detail.text
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


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ("failed", "Submitted, but the transaction failed or reverted."),
        ("not_submitted", "Not submitted."),
    ],
)
def test_transaction_recovery_distinguishes_revert_from_non_submission(
    tmp_path: Path, status: str, expected: str
) -> None:
    repository = SQLiteReportRepository(tmp_path / "reports.sqlite3")
    report = replay_fixture(Path("fixtures/clean-success"))
    artifact = EvidenceArtifact(
        artifact_id=f"{report.run_id}:recovery",
        artifact_type=ArtifactType.PAYMENT_RECEIPT,
        source="x402.transaction",
        collected_at=datetime(2026, 8, 18, tzinfo=UTC),
        redacted=True,
        data={"status": status},
    )
    repository.save(report, artifacts=(artifact,))
    assert repository.artifacts(report.run_id) == (artifact,)

    detail = TestClient(create_app(repository)).get(f"/runs/{report.run_id}")

    assert detail.status_code == 200
    assert expected in detail.text
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
    assert "Model requests" in detail.text
    assert "Tool calls" in detail.text
    assert "Input tokens" in detail.text
    assert "Output tokens" in detail.text
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
    assert "Tool calls" in detail.text
    assert "<dd>0</dd>" in detail.text
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


def test_delete_confirmation_requires_valid_csrf_and_cascades(tmp_path: Path) -> None:
    repository = SQLiteReportRepository(tmp_path / "reports.sqlite3")
    report = replay_fixture(Path("fixtures/clean-success"))
    repository.save(report)
    client = TestClient(create_app(repository))

    confirmation = client.get(f"/runs/{report.run_id}/delete")
    assert confirmation.status_code == 200
    assert "Delete local report" in confirmation.text
    assert report.run_id in confirmation.text
    token_marker = 'name="csrf_token" value="'
    token_start = confirmation.text.index(token_marker) + len(token_marker)
    token = confirmation.text[token_start : confirmation.text.index('"', token_start)]

    rejected = client.post(
        f"/runs/{report.run_id}/delete",
        content="csrf_token=wrong",
        headers={"content-type": "application/x-www-form-urlencoded"},
        follow_redirects=False,
    )
    assert rejected.status_code == 403
    assert repository.get(report.run_id) is not None

    deleted = client.post(
        f"/runs/{report.run_id}/delete",
        content=f"csrf_token={token}",
        headers={"content-type": "application/x-www-form-urlencoded"},
        follow_redirects=False,
    )
    assert deleted.status_code == 303
    assert deleted.headers["location"] == "/runs"
    assert repository.get(report.run_id) is None
    repository.close()


def test_vendored_htmx_checksum_is_recorded() -> None:
    static = Path("src/settlediff/ui/static")
    expected = (
        "1f94ab71fca01e602e4c366984c1ea0492dcdc586cb0a8c6ef0fc2782a4545e49"
        "fc015834caa64ccf3fc73e70bb0af95"
    )
    assert sha384((static / "htmx.min.js").read_bytes()).hexdigest() == expected
    assert expected in (static / "HTMX-SOURCE.md").read_text()
