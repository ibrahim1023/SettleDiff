from __future__ import annotations

import json
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any, cast

import pytest

from settlediff.agent.grounding import fallback_explanation
from settlediff.application.bundle import (
    BundleError,
    CompatibilityMetadata,
    EvidenceBundle,
    export_bundle,
    load_bundle,
    serialize_bundle,
    verify_bundle,
)
from settlediff.application.replay import replay_fixture
from settlediff.application.run import RunEvent, RunState
from settlediff.domain.models import (
    ArtifactType,
    CheckStatus,
    EvidenceArtifact,
    ExplanationRecord,
    ExplanationSource,
    Verdict,
)
from settlediff.storage.sqlite import SQLiteReportRepository

FIXTURES = Path(__file__).parents[3] / "fixtures"


def _artifact(scenario: str, name: str, artifact_type: ArtifactType) -> EvidenceArtifact:
    return EvidenceArtifact(
        artifact_id=f"{scenario}:{name}",
        artifact_type=artifact_type,
        source="fixture",
        collected_at=datetime(2026, 8, 12, tzinfo=UTC),
        redacted=False,
        data=json.loads((FIXTURES / scenario / name).read_text()),
    )


def _resign(bundle: EvidenceBundle, **updates: object) -> EvidenceBundle:
    changed = bundle.model_copy(update=updates)
    payload = changed.model_dump(mode="json", exclude={"integrity"})
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()
    return changed.model_copy(update={"integrity": sha256(encoded).hexdigest()})


def _json(bundle: EvidenceBundle) -> dict[str, Any]:
    return json.loads(serialize_bundle(bundle))


def test_export_load_verify_round_trip(tmp_path: Path) -> None:
    report = replay_fixture(FIXTURES / "clean-success")
    artifacts = (
        _artifact("clean-success", "contract.json", ArtifactType.SERVICE_CONTRACT),
        _artifact("clean-success", "execution.json", ArtifactType.EXECUTION),
        _artifact("clean-success", "activity.json", ArtifactType.ACTIVITY),
    )
    event = RunEvent(state=RunState.COMPLETE, occurred_at=datetime(2026, 8, 12, tzinfo=UTC))
    explanation = ExplanationRecord(
        explanation=fallback_explanation(report, {artifact.artifact_id for artifact in artifacts}),
        source=ExplanationSource.FALLBACK,
        tool_calls=0,
    )
    repository = SQLiteReportRepository(tmp_path / "reports.sqlite3")
    repository.save(report, events=(event,), artifacts=artifacts, explanation=explanation)

    exported = export_bundle(repository, report.run_id)
    loaded = load_bundle(serialize_bundle(exported))

    assert loaded == exported
    assert loaded.events == (event,)
    assert loaded.explanation == explanation
    assert loaded.compatibility == CompatibilityMetadata(
        settlediff_version="0.1.0",
        report_schema_version=2,
        database_schema_version=3,
        contextdev_api_path="/web/scrape/markdown",
        hyperfusion_model=None,
        perflo_cli_version=None,
    )
    assert all(artifact.redacted for artifact in loaded.artifacts)
    assert verify_bundle(loaded) == report
    repository.close()


def test_current_bundle_reads_schema_v1_report_without_v2_fields(tmp_path: Path) -> None:
    repository = SQLiteReportRepository(tmp_path / "reports.sqlite3")
    report = replay_fixture(FIXTURES / "clean-success")
    repository.save(report)
    payload = _json(export_bundle(repository, report.run_id))
    report_payload = cast(dict[str, Any], payload["report"])
    report_payload["schema_version"] = 1
    report_payload.pop("receipt", None)
    for name in ("contract", "execution", "ledger"):
        record = cast(dict[str, Any], report_payload[name])
        record["schema_version"] = 1
        for field in ("scheme", "network", "asset_identity"):
            record.pop(field, None)
    contract = cast(dict[str, Any], report_payload["contract"])
    contract.pop("recipient", None)
    compatibility = cast(dict[str, Any], payload["compatibility"])
    compatibility["report_schema_version"] = 1
    unsigned = {key: value for key, value in payload.items() if key != "integrity"}
    encoded = json.dumps(
        unsigned, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()
    payload["integrity"] = sha256(encoded).hexdigest()

    bundle = load_bundle(json.dumps(payload).encode())

    assert bundle.report.schema_version == 1
    assert bundle.report.receipt is None
    assert verify_bundle(bundle) == bundle.report
    repository.close()


def test_export_missing_run_raises_bundle_error(tmp_path: Path) -> None:
    repository = SQLiteReportRepository(tmp_path / "reports.sqlite3")

    with pytest.raises(BundleError, match="not found"):
        export_bundle(repository, "run:missing")

    repository.close()


def test_verify_rejects_digest_tampering(tmp_path: Path) -> None:
    repository = SQLiteReportRepository(tmp_path / "reports.sqlite3")
    report = replay_fixture(FIXTURES / "clean-success")
    repository.save(report)
    bundle = export_bundle(repository, report.run_id)

    tampered = bundle.model_copy(update={"integrity": "0" * 64})

    with pytest.raises(BundleError, match="integrity"):
        verify_bundle(tampered)
    repository.close()


@pytest.mark.parametrize("field", ["report", "finding", "verdict"])
def test_verify_rejects_report_finding_and_verdict_tampering(tmp_path: Path, field: str) -> None:
    repository = SQLiteReportRepository(tmp_path / "reports.sqlite3")
    report = replay_fixture(FIXTURES / "clean-success")
    repository.save(report)
    bundle = export_bundle(repository, report.run_id)
    if field == "report":
        changed_report = report.model_copy(update={"run_id": "run:tampered"})
    elif field == "finding":
        changed_finding = report.findings[0].model_copy(update={"message": "tampered"})
        changed_report = report.model_copy(
            update={"findings": (changed_finding, *report.findings[1:])}
        )
    else:
        changed_report = report.model_copy(update={"verdict": Verdict.PAID_FAILURE})
    tampered = bundle.model_copy(update={"report": changed_report})

    with pytest.raises(BundleError, match="integrity"):
        verify_bundle(tampered)
    repository.close()


def test_verify_rejects_internally_inconsistent_resigned_report(tmp_path: Path) -> None:
    repository = SQLiteReportRepository(tmp_path / "reports.sqlite3")
    report = replay_fixture(FIXTURES / "clean-success")
    repository.save(report)
    bundle = export_bundle(repository, report.run_id)
    changed_finding = report.findings[0].model_copy(update={"status": CheckStatus.UNKNOWN})
    changed_report = report.model_copy(update={"findings": (changed_finding, *report.findings[1:])})

    with pytest.raises(BundleError, match="verdict"):
        verify_bundle(_resign(bundle, report=changed_report))
    repository.close()


def test_verify_rejects_unredacted_artifact() -> None:
    report = replay_fixture(FIXTURES / "clean-success")
    artifact = _artifact("clean-success", "execution.json", ArtifactType.EXECUTION)
    bundle = EvidenceBundle(
        run_id=report.run_id,
        report=report,
        explanation=None,
        events=(),
        artifacts=(artifact,),
        compatibility=CompatibilityMetadata(
            settlediff_version="0.1.0",
            report_schema_version=report.schema_version,
            database_schema_version=3,
            contextdev_api_path="/web/scrape/markdown",
            hyperfusion_model=None,
            perflo_cli_version=None,
        ),
        integrity="0" * 64,
    )

    with pytest.raises(BundleError, match="redacted"):
        verify_bundle(_resign(bundle))


def test_verify_rejects_explanation_mismatch(tmp_path: Path) -> None:
    repository = SQLiteReportRepository(tmp_path / "reports.sqlite3")
    report = replay_fixture(FIXTURES / "clean-success")
    repository.save(report)
    bundle = export_bundle(repository, report.run_id)
    explanation = fallback_explanation(report, set()).model_copy(
        update={
            "run_id": "run:other",
            "finding_ids": ("finding:missing",),
            "deterministic_verdict": Verdict.PAID_FAILURE,
        }
    )
    record = ExplanationRecord(
        explanation=explanation,
        source=ExplanationSource.FALLBACK,
        tool_calls=0,
    )

    with pytest.raises(BundleError, match="explanation"):
        verify_bundle(_resign(bundle, explanation=record))
    repository.close()


def test_load_rejects_unknown_schema_version(tmp_path: Path) -> None:
    repository = SQLiteReportRepository(tmp_path / "reports.sqlite3")
    report = replay_fixture(FIXTURES / "clean-success")
    repository.save(report)
    payload = _json(export_bundle(repository, report.run_id))
    payload["schema_version"] = 1

    with pytest.raises(BundleError, match="bundle"):
        load_bundle(json.dumps(payload).encode())
    repository.close()


@pytest.mark.parametrize("scenario", ["missing-activity", "ambiguous-activity"])
def test_verify_accepts_internally_consistent_unverifiable_fixture_report(
    tmp_path: Path, scenario: str
) -> None:
    repository = SQLiteReportRepository(tmp_path / f"{scenario}.sqlite3")
    report = replay_fixture(FIXTURES / scenario)
    repository.save(report)

    verified = verify_bundle(export_bundle(repository, report.run_id))

    assert verified == report
    assert verified.verdict is Verdict.UNVERIFIABLE
    repository.close()
