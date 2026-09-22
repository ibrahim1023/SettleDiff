from __future__ import annotations

import json
import socket
from datetime import UTC, datetime
from pathlib import Path
from typing import NoReturn

import pytest
from fastapi.testclient import TestClient
from pydantic_ai import models
from typer.testing import CliRunner

from settlediff.api.app import create_app
from settlediff.application.bundle import (
    BundleError,
    EvidenceBundleV3,
    export_bundle,
    load_bundle,
    serialize_bundle,
    verify_bundle,
)
from settlediff.application.investigate import investigate_purchase
from settlediff.application.replay import replay_fixture
from settlediff.application.run import RunEvent, RunState
from settlediff.cli import app
from settlediff.domain.drift import (
    SOURCE_CHANGED,
    DriftStatus,
    build_contract_snapshot,
)
from settlediff.domain.models import (
    ArtifactType,
    DeliveryAssessment,
    DeliveryObservation,
    DeliveryStatus,
    EvidenceArtifact,
    MachineReport,
    ResponseContract,
    RetryAssessment,
    RetrySafety,
)
from settlediff.domain.redaction import redact_report
from settlediff.domain.retry import CONFIRMED_TRANSFER
from settlediff.storage.sqlite import SQLiteReportRepository
from settlediff.x402.bazaar import BazaarStatus, assess_bazaar
from settlediff.x402.models import PaymentRequired
from settlediff.x402.normalize import normalize_payment_required


def test_complete_fixture_path_remains_offline(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def block_network(*_args: object, **_kwargs: object) -> NoReturn:
        raise AssertionError("the offline release path attempted a network connection")

    monkeypatch.setattr(socket, "create_connection", block_network)
    monkeypatch.setattr(socket.socket, "connect", block_network)
    assert models.ALLOW_MODEL_REQUESTS is False

    runner = CliRunner()
    database = tmp_path / "offline-release.sqlite3"
    fixture_reports = tuple(
        (path, replay_fixture(path)) for path in sorted(Path("fixtures").iterdir()) if path.is_dir()
    )
    assert len(fixture_reports) == 17
    assert {path.name for path, _report in fixture_reports} >= {
        "confirmed-activity-charge",
        "x402-clean-success",
        "x402-paid-failure",
        "x402-uncertain-submission",
        "x402-provider-success-independent-failure",
        "x402-provider-failure-independent-confirmation",
        "x402-wrong-recipient",
        "x402-wrong-amount",
        "x402-wrong-asset",
        "x402-wrong-network",
    }

    for fixture_path, report in fixture_reports:
        human = runner.invoke(
            app,
            ["verify-fixture", str(fixture_path), "--database", str(database)],
        )
        assert human.exit_code == 0, human.output
        assert human.stdout.splitlines()[0] == report.verdict.value

        canonical = runner.invoke(app, ["verify-fixture", str(fixture_path), "--json"])
        assert canonical.exit_code == 0, canonical.output
        assert MachineReport.model_validate_json(canonical.stdout) == report
        persisted = runner.invoke(
            app, ["show", report.run_id, "--database", str(database), "--json"]
        )
        assert persisted.exit_code == 0, persisted.output
        persisted_report = MachineReport.model_validate_json(persisted.stdout)
        assert persisted_report == redact_report(report)

    expected_reports = tuple(report for _path, report in fixture_reports)
    repository = SQLiteReportRepository(database)
    client = TestClient(create_app(repository))
    listing = client.get("/runs")
    assert listing.status_code == 200
    for report in expected_reports:
        assert repository.get(report.run_id) == redact_report(report)
        detail = client.get(f"/runs/{report.run_id}")
        assert detail.status_code == 200
        assert report.verdict.value in detail.text
        assert "Expected · Executed · Recorded" in detail.text
        assert "Purchase assurance" in detail.text
        assert "Evidence timeline" in detail.text
        investigation = investigate_purchase(repository, report.run_id)
        assert investigation.verdict is report.verdict
        investigated = runner.invoke(
            app, ["investigate-purchase", report.run_id, "--database", str(database), "--json"]
        )
        assert investigated.exit_code == 0, investigated.output
        payload = json.loads(investigated.stdout)
        assert payload["verdict"] == report.verdict.value
        assert payload["bundle"]["status"] in {"AVAILABLE", "UNAVAILABLE"}
    repository.close()


def test_schema_3_bundle_round_trip_from_persisted_fixture(tmp_path: Path) -> None:
    fixture = Path("fixtures/clean-success")
    report = replay_fixture(fixture)
    artifacts = tuple(
        EvidenceArtifact(
            artifact_id=f"{report.run_id}:{name}",
            artifact_type=artifact_type,
            source="fixture",
            collected_at=datetime(2026, 8, 12, tzinfo=UTC),
            redacted=False,
            data=json.loads((fixture / filename).read_text()),
        )
        for filename, name, artifact_type in (
            ("contract.json", "service_contract", ArtifactType.SERVICE_CONTRACT),
            ("execution.json", "execution", ArtifactType.EXECUTION),
            ("activity.json", "activity", ArtifactType.ACTIVITY),
        )
    )
    repository = SQLiteReportRepository(tmp_path / "bundle.sqlite3")
    repository.save(
        report,
        events=(RunEvent(state=RunState.COMPLETE, occurred_at=datetime(2026, 8, 12, tzinfo=UTC)),),
        artifacts=artifacts,
    )

    bundle = load_bundle(serialize_bundle(export_bundle(repository, report.run_id)))

    assert isinstance(bundle, EvidenceBundleV3)
    assert bundle.schema_version == 3
    assert {
        "report.json",
        "timeline.json",
        "run-events.json",
    } <= set(bundle.objects)
    assert not any(path in bundle.objects for path in ("manifest.json", "bundle_sha256"))
    assert sum(path.startswith("artifacts/a-") for path in bundle.objects) == 3
    assert verify_bundle(bundle) == redact_report(report)
    repository.close()


def test_publication_of_persisted_fixture_remains_offline(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def block_network(*_args: object, **_kwargs: object) -> NoReturn:
        raise AssertionError("publication attempted a network connection")

    monkeypatch.setattr(socket, "create_connection", block_network)
    monkeypatch.setattr(socket.socket, "connect", block_network)

    report = replay_fixture(Path("fixtures/x402-clean-success"))
    repository = SQLiteReportRepository(tmp_path / "publish.sqlite3")
    repository.save(report)
    output = tmp_path.resolve() / "public"

    result = CliRunner().invoke(
        app,
        [
            "publish",
            report.run_id,
            "--database",
            str(tmp_path / "publish.sqlite3"),
            "--output",
            str(output),
        ],
    )
    repository.close()

    assert result.exit_code == 0, result.stderr
    files = sorted(entry.name for entry in output.iterdir())
    assert files == ["index.html", "public-manifest.json", "report.json"]
    html = (output / "index.html").read_bytes().lower()
    assert b"<script" not in html and b"href=" not in html
    manifest = json.loads((output / "public-manifest.json").read_text())
    assert [entry["path"] for entry in manifest["objects"]] == [
        "index.html",
        "report.json",
    ]
    assert b"syn_x402_clean" not in (output / "report.json").read_bytes()


def test_cross_feature_assurance_demo_remains_offline(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def block_network(*_args: object, **_kwargs: object) -> NoReturn:
        raise AssertionError("the assurance demo attempted a network connection")

    monkeypatch.setattr(socket, "create_connection", block_network)
    monkeypatch.setattr(socket.socket, "connect", block_network)
    assert models.ALLOW_MODEL_REQUESTS is False

    database = tmp_path / "assurance.sqlite3"
    repository = SQLiteReportRepository(database)
    report = replay_fixture(Path("fixtures/x402-clean-success"))
    assert report.contract is not None

    response_contract = ResponseContract(
        media_type="application/json",
        source_fields=("resource.mimeType",),
    )
    contract = report.contract.model_copy(
        update={"schema_version": 3, "response_contract": response_contract}
    )
    delivery = DeliveryAssessment(
        status=DeliveryStatus.SATISFIED,
        reason_code="DELIVERY_SATISFIED",
        evidence_ids=(f"{report.run_id}:service_response",),
        observation=DeliveryObservation(
            observed_at=datetime(2026, 8, 31, 0, 0, 2, tzinfo=UTC),
            status_code=200,
            media_type="application/json",
            received_bytes=30,
            truncated=False,
            parsed_body={"result": "synthetic success"},
            evidence_ids=(f"{report.run_id}:service_response",),
        ),
        response_contract_digest=response_contract.digest,
    )
    retry = RetryAssessment(
        safety=RetrySafety.DO_NOT_RETRY,
        reason_codes=(CONFIRMED_TRANSFER,),
        evidence_ids=(f"{report.run_id}:payment_receipt",),
    )
    report = report.model_copy(
        update={
            "schema_version": 3,
            "adapter_id": "x402",
            "contract": contract,
            "delivery": delivery,
            "retry": retry,
        }
    )

    fixture_dir = Path("fixtures/x402-clean-success")
    artifact_files = {
        "contract.json": ArtifactType.SERVICE_CONTRACT,
        "execution.json": ArtifactType.EXECUTION,
        "receipt.json": ArtifactType.PAYMENT_RECEIPT,
        "activity.json": ArtifactType.ACTIVITY,
    }
    collected_at = datetime(2026, 8, 31, 0, 0, 2, tzinfo=UTC)
    artifacts = tuple(
        EvidenceArtifact(
            artifact_id=f"{report.run_id}:{artifact_type.value}",
            artifact_type=artifact_type,
            source="fixture",
            collected_at=collected_at,
            redacted=False,
            data=json.loads((fixture_dir / name).read_text()),
        )
        for name, artifact_type in artifact_files.items()
    ) + (
        EvidenceArtifact(
            artifact_id=f"{report.run_id}:service_response",
            artifact_type=ArtifactType.SERVICE_RESPONSE,
            source="x402_signer",
            collected_at=collected_at,
            redacted=False,
            data={
                "status_code": 200,
                "media_type": "application/json",
                "body": {"result": "synthetic success"},
            },
        ),
    )
    repository.save(
        report,
        events=(
            RunEvent(
                state=RunState.COMPLETE,
                occurred_at=datetime(2026, 8, 12, tzinfo=UTC),
            ),
        ),
        artifacts=artifacts,
    )

    assert report.contract is not None
    snapshot_one = build_contract_snapshot(
        report.contract.url,
        "x402",
        report.contract,
        {"synthetic": True, "revision": 1},
    )
    snapshot_two = build_contract_snapshot(
        report.contract.url,
        "x402",
        report.contract,
        {"synthetic": True, "revision": 2},
    )
    repository.save_contract_snapshot(snapshot_one, datetime(2026, 9, 1, tzinfo=UTC))
    repository.save_contract_snapshot(snapshot_two, datetime(2026, 9, 2, tzinfo=UTC))

    required = PaymentRequired.model_validate(
        json.loads(Path("tests/contract/x402/fixtures/payment-required-bazaar-v2.json").read_text())
    )
    normalized = normalize_payment_required(
        required, request_schema={"method": "GET", "body": None}
    )
    assessment = assess_bazaar(required, request_method="GET", current_contract=normalized)
    assert assessment.status is BazaarStatus.MATCH
    bazaar_checks = {check.check_id: check.status for check in assessment.checks}
    assert bazaar_checks["PAID_EVIDENCE"] is BazaarStatus.UNAVAILABLE

    investigation = investigate_purchase(repository, report.run_id)
    assert investigation.delivery == delivery
    assert investigation.retry == retry
    assert investigation.timeline and all(event.source for event in investigation.timeline)
    assert investigation.bundle.status == "AVAILABLE"
    assert investigation.drift is not None
    assert investigation.drift.status is DriftStatus.DIFF
    assert investigation.drift.change_codes == (SOURCE_CHANGED,)
    assert "facilitator" not in investigation.model_dump(mode="json")

    runner = CliRunner()
    human = runner.invoke(app, ["investigate-purchase", report.run_id, "--database", str(database)])
    assert human.exit_code == 0, human.output
    for heading in (
        f"Purchase investigation: {report.run_id}",
        "Verdict:",
        "What failed or remains unresolved:",
        "Could money have moved?",
        "Amount agreement:",
        "Recipient agreement:",
        "Delivery:",
        "Activity agreement:",
        "Contract drift:",
        "Retry safety:",
        "Evidence bundle:",
    ):
        assert heading in human.stdout, heading
    json_result = runner.invoke(
        app, ["investigate-purchase", report.run_id, "--database", str(database), "--json"]
    )
    assert json_result.exit_code == 0, json_result.output
    assert json.loads(json_result.stdout) == investigation.model_dump(mode="json")

    first = serialize_bundle(export_bundle(repository, report.run_id))
    second = serialize_bundle(export_bundle(repository, report.run_id))
    assert first == second
    bundle = load_bundle(first)
    assert verify_bundle(bundle).run_id == report.run_id
    tampered = json.loads(first)
    tampered["bundle_sha256"] = "0" * 64
    with pytest.raises(BundleError, match="integrity"):
        verify_bundle(load_bundle(json.dumps(tampered).encode()))

    output = tmp_path / "public"
    published = runner.invoke(
        app, ["publish", report.run_id, "--database", str(database), "--output", str(output)]
    )
    assert published.exit_code == 0, published.output
    assert sorted(entry.name for entry in output.iterdir()) == [
        "index.html",
        "public-manifest.json",
        "report.json",
    ]
    public_report = json.loads((output / "report.json").read_text())
    assert set(public_report) == {
        "schema_version",
        "source_report_schema_version",
        "public_run_id",
        "evidence_through",
        "verdict",
        "findings",
        "delivery",
        "retry",
        "timeline",
    }
    assert public_report["public_run_id"] != report.run_id
    for name in ("index.html", "public-manifest.json", "report.json"):
        assert report.run_id not in (output / name).read_text()
    html = (output / "index.html").read_text().lower()
    assert "<script" not in html and "href=" not in html
    assert public_report["delivery"]["status"] == "SATISFIED"
    assert public_report["retry"]["safety"] == "DO_NOT_RETRY"

    repository.close()
