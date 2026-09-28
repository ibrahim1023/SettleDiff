from __future__ import annotations

import base64
import json
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any, cast

import pytest
from pydantic import BaseModel, JsonValue

from settlediff.agent.grounding import fallback_explanation
from settlediff.application.bundle import (
    BundleError,
    BundleManifestEntry,
    CompatibilityMetadata,
    EvidenceBundle,
    EvidenceBundleV3,
    export_bundle,
    load_bundle,
    serialize_bundle,
    verify_bundle,
)
from settlediff.application.replay import replay_fixture
from settlediff.application.run import RunEvent, RunState
from settlediff.domain.drift import ContractSnapshot, build_contract_snapshot
from settlediff.domain.integrity import canonical_json_bytes
from settlediff.domain.models import (
    ArtifactType,
    EvidenceArtifact,
    ExplanationRecord,
    ExplanationSource,
    MachineReport,
    Verdict,
)
from settlediff.domain.redaction import redact_artifact, redact_report
from settlediff.storage.sqlite import SQLiteReportRepository

FIXTURES = Path(__file__).parents[3] / "fixtures"
EVENT = RunEvent(state=RunState.COMPLETE, occurred_at=datetime(2026, 8, 12, tzinfo=UTC))

_ARTIFACT_FILES = {
    "contract.json": ArtifactType.SERVICE_CONTRACT,
    "execution.json": ArtifactType.EXECUTION,
    "receipt.json": ArtifactType.PAYMENT_RECEIPT,
    "activity.json": ArtifactType.ACTIVITY,
}


def _fixture_artifacts(scenario: str, run_id: str) -> tuple[EvidenceArtifact, ...]:
    return tuple(
        EvidenceArtifact(
            artifact_id=f"{run_id}:{artifact_type.value}",
            artifact_type=artifact_type,
            source="fixture",
            collected_at=datetime(2026, 8, 12, tzinfo=UTC),
            redacted=False,
            data=json.loads((FIXTURES / scenario / name).read_text()),
        )
        for name, artifact_type in _ARTIFACT_FILES.items()
        if (FIXTURES / scenario / name).is_file()
    )


def _persist(
    tmp_path: Path,
    scenario: str,
    *,
    adapter_id: str | None = None,
    explain: bool = True,
) -> tuple[SQLiteReportRepository, MachineReport]:
    repository = SQLiteReportRepository(tmp_path / f"{scenario}.sqlite3")
    report = replay_fixture(FIXTURES / scenario)
    if adapter_id is not None:
        report = report.model_copy(update={"adapter_id": adapter_id})
    artifacts = _fixture_artifacts(scenario, report.run_id)
    explanation = (
        ExplanationRecord(
            explanation=fallback_explanation(
                report, {artifact.artifact_id for artifact in artifacts}
            ),
            source=ExplanationSource.FALLBACK,
            tool_calls=0,
        )
        if explain
        else None
    )
    repository.save(report, events=(EVENT,), artifacts=artifacts, explanation=explanation)
    return repository, report


def _v3_payload(bundle: EvidenceBundleV3) -> dict[str, Any]:
    return cast(
        dict[str, Any],
        json.loads(canonical_json_bytes(bundle.model_dump(mode="json"))),
    )


def _jsonable(value: Any) -> Any:
    if isinstance(value, EvidenceBundleV3):
        return value.model_dump(mode="json")
    if isinstance(value, (tuple, list)):
        return [_jsonable(item) for item in cast(tuple[Any, ...], value)]
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in cast(dict[str, Any], value).items()}
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    return value


def _resign_v3(bundle: EvidenceBundleV3, **updates: object) -> EvidenceBundleV3:
    payload = _v3_payload(bundle)
    for key, value in updates.items():
        payload[key] = _jsonable(value)
    payload["bundle_sha256"] = "0" * 64
    changed = EvidenceBundleV3.model_validate_json(json.dumps(payload))
    unsigned = changed.model_dump(mode="json", exclude={"bundle_sha256"})
    return EvidenceBundleV3.model_validate_json(
        json.dumps(changed.model_dump(mode="json") | {"bundle_sha256": sha256_digest(unsigned)})
    )


def sha256_digest(payload: object) -> str:
    return sha256(canonical_json_bytes(payload)).hexdigest()


def _v2_bundle(
    report: MachineReport,
    *,
    artifacts: tuple[EvidenceArtifact, ...] = (),
    explanation: ExplanationRecord | None = None,
    integrity: str | None = None,
) -> EvidenceBundle:
    bundle = EvidenceBundle(
        run_id=report.run_id,
        report=report,
        explanation=explanation,
        events=(EVENT,),
        artifacts=artifacts,
        compatibility=CompatibilityMetadata(
            settlediff_version="0.1.0",
            report_schema_version=report.schema_version,
            database_schema_version=6,
            contextdev_api_path="/web/scrape/markdown",
            hyperfusion_model=None,
            perflo_cli_version=None,
            payment_adapter_id=report.adapter_id,
            x402_protocol_version="2" if report.adapter_id == "x402" else None,
            x402_signer_schema_version=3 if report.adapter_id == "x402" else None,
        ),
        integrity="0" * 64,
    )
    if integrity is None:
        integrity = sha256(
            json.dumps(
                bundle.model_dump(mode="json", exclude={"integrity"}),
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode()
        ).hexdigest()
    return bundle.model_copy(update={"integrity": integrity})


def test_export_load_verify_round_trip(tmp_path: Path) -> None:
    repository, report = _persist(tmp_path, "clean-success")

    exported = export_bundle(repository, report.run_id)
    loaded = load_bundle(serialize_bundle(exported))

    assert exported.schema_version == 3
    assert loaded == exported
    assert isinstance(loaded, EvidenceBundleV3)
    paths = sorted(loaded.objects)
    assert {"report.json", "timeline.json", "run-events.json", "explanation.json"} <= set(paths)
    artifact_paths = [path for path in paths if path.startswith("artifacts/")]
    assert len(artifact_paths) == 3
    for path in artifact_paths:
        assert path.startswith("artifacts/a-") and path.endswith(".json")
    assert [entry.path for entry in loaded.manifest] == paths
    assert loaded.compatibility == CompatibilityMetadata(
        settlediff_version="0.1.0",
        report_schema_version=2,
        database_schema_version=6,
        contextdev_api_path="/web/scrape/markdown",
        hyperfusion_model=None,
        perflo_cli_version=None,
        payment_adapter_id=None,
        x402_protocol_version=None,
        x402_signer_schema_version=None,
    )
    assert verify_bundle(loaded) == redact_report(report)
    repository.close()


def test_artifact_paths_round_trip_exact_ids(tmp_path: Path) -> None:
    repository, report = _persist(tmp_path, "clean-success")
    bundle = export_bundle(repository, report.run_id)

    decoded: dict[str, str] = {}
    for path, value in bundle.objects.items():
        if path.startswith("artifacts/"):
            encoded = path.removeprefix("artifacts/a-").removesuffix(".json")
            decoded[path] = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)).decode()
            assert cast(dict[str, Any], value)["artifact_id"] == decoded[path]
    assert set(decoded.values()) == {
        f"{report.run_id}:service_contract",
        f"{report.run_id}:execution",
        f"{report.run_id}:activity",
    }
    repository.close()


def test_export_missing_run_raises_bundle_error(tmp_path: Path) -> None:
    repository = SQLiteReportRepository(tmp_path / "reports.sqlite3")

    with pytest.raises(BundleError, match="not found"):
        export_bundle(repository, "run:missing")

    repository.close()


def test_export_incomplete_repository_fails_verification(tmp_path: Path) -> None:
    repository = SQLiteReportRepository(tmp_path / "reports.sqlite3")
    repository.save(replay_fixture(FIXTURES / "clean-success"))

    with pytest.raises(BundleError, match="cites unavailable evidence"):
        export_bundle(repository, "syn_run_clean")

    repository.close()


def test_verify_rejects_bundle_digest_tampering(tmp_path: Path) -> None:
    repository, report = _persist(tmp_path, "clean-success")
    bundle = export_bundle(repository, report.run_id)

    tampered = EvidenceBundleV3.model_validate_json(
        json.dumps(bundle.model_dump(mode="json") | {"bundle_sha256": "0" * 64})
    )

    with pytest.raises(BundleError, match="integrity"):
        verify_bundle(tampered)
    repository.close()


def test_verify_rejects_resigned_object_and_manifest_tampering(tmp_path: Path) -> None:
    repository, report = _persist(tmp_path, "clean-success")
    bundle = export_bundle(repository, report.run_id)

    changed_objects = dict(bundle.objects)
    report_object = cast(dict[str, Any], json.loads(json.dumps(changed_objects["report.json"])))
    report_object["verdict"] = "PAID_FAILURE"
    changed_objects["report.json"] = cast(JsonValue, report_object)
    with pytest.raises(BundleError, match="manifest digest"):
        verify_bundle(_resign_v3(bundle, objects=changed_objects))

    changed_manifest = tuple(
        entry.model_copy(update={"sha256": "0" * 64}) if entry.path == "report.json" else entry
        for entry in bundle.manifest
    )
    with pytest.raises(BundleError, match="manifest digest"):
        verify_bundle(_resign_v3(bundle, manifest=changed_manifest))
    repository.close()


def test_verify_rejects_resigned_verdict_inconsistency(tmp_path: Path) -> None:
    repository, report = _persist(tmp_path, "clean-success")
    bundle = export_bundle(repository, report.run_id)
    objects = dict(bundle.objects)
    report_object = cast(dict[str, Any], json.loads(json.dumps(objects["report.json"])))
    findings = cast(list[Any], report_object["findings"])
    findings[0]["status"] = "UNKNOWN"
    objects["report.json"] = cast(JsonValue, report_object)
    manifest = tuple(
        entry.model_copy(update={"sha256": sha256_digest(objects[entry.path])})
        if entry.path == "report.json"
        else entry
        for entry in bundle.manifest
    )

    with pytest.raises(BundleError, match="verdict"):
        verify_bundle(_resign_v3(bundle, objects=objects, manifest=manifest))
    repository.close()


@pytest.mark.parametrize(
    "path",
    [
        "/absolute/report.json",
        "../traversal.json",
        "a/../b.json",
        "back\\slash.json",
        "with:colon.json",
        "double//slash.json",
        "manifest.json",
        "bundle_sha256",
        "nonascii-é.json",
    ],
)
def test_load_rejects_unsafe_logical_paths(tmp_path: Path, path: str) -> None:
    repository, report = _persist(tmp_path, "clean-success")
    bundle = export_bundle(repository, report.run_id)
    payload = _v3_payload(bundle)
    objects = cast(dict[str, Any], payload["objects"])
    objects[path] = objects.pop("run-events.json")
    payload["manifest"] = [
        {"path": key, "sha256": sha256_digest(objects[key])} for key in sorted(objects)
    ]
    unsigned = {key: value for key, value in payload.items() if key != "bundle_sha256"}
    payload["bundle_sha256"] = sha256_digest(unsigned)

    with pytest.raises(BundleError):
        load_bundle(json.dumps(payload).encode())
    repository.close()


def test_verify_rejects_unexpected_object_class(tmp_path: Path) -> None:
    repository, report = _persist(tmp_path, "clean-success")
    bundle = export_bundle(repository, report.run_id)
    objects = dict(bundle.objects)
    objects["unexpected.txt"] = objects.pop("run-events.json")
    manifest = tuple(
        BundleManifestEntry(path=path, sha256=sha256_digest(objects[path]))
        for path in sorted(objects)
    )

    with pytest.raises(BundleError, match="unexpected object"):
        verify_bundle(_resign_v3(bundle, objects=objects, manifest=manifest))
    repository.close()


def test_load_rejects_duplicate_json_keys_and_non_object(tmp_path: Path) -> None:
    repository, report = _persist(tmp_path, "clean-success")
    bundle = export_bundle(repository, report.run_id)
    serialized = serialize_bundle(bundle)
    duplicate = serialized.replace(b'"run_id":', b'"run_id":"dup","run_id":', 1)

    with pytest.raises(BundleError, match="bundle"):
        load_bundle(duplicate)
    with pytest.raises(BundleError, match="bundle"):
        load_bundle(b"[1,2,3]")
    with pytest.raises(BundleError, match="bundle"):
        load_bundle(b"x" * (16 * 1024 * 1024 + 1))
    repository.close()


def test_verify_rejects_artifact_path_content_mismatch(tmp_path: Path) -> None:
    repository, report = _persist(tmp_path, "clean-success")
    bundle = export_bundle(repository, report.run_id)
    objects = dict(bundle.objects)
    artifact_path = next(path for path in objects if path.startswith("artifacts/"))
    artifact = cast(dict[str, Any], json.loads(json.dumps(objects[artifact_path])))
    artifact["artifact_id"] = "run:other"
    objects[artifact_path] = cast(JsonValue, artifact)
    manifest = tuple(
        entry.model_copy(update={"sha256": sha256_digest(objects[entry.path])})
        for entry in bundle.manifest
    )

    with pytest.raises(BundleError, match="does not match its content"):
        verify_bundle(_resign_v3(bundle, objects=objects, manifest=manifest))
    repository.close()


def test_verify_rejects_timeline_sequence_gap(tmp_path: Path) -> None:
    repository, report = _persist(tmp_path, "clean-success")
    bundle = export_bundle(repository, report.run_id)
    objects = dict(bundle.objects)
    timeline = cast(list[Any], json.loads(json.dumps(objects["timeline.json"])))
    timeline[1]["sequence"] = 99
    objects["timeline.json"] = cast(JsonValue, timeline)
    manifest = tuple(
        entry.model_copy(update={"sha256": sha256_digest(objects[entry.path])})
        for entry in bundle.manifest
    )

    with pytest.raises(BundleError, match="sequence"):
        verify_bundle(_resign_v3(bundle, objects=objects, manifest=manifest))
    repository.close()


def test_verify_rejects_explanation_citation_mismatch(tmp_path: Path) -> None:
    repository, report = _persist(tmp_path, "clean-success")
    bundle = export_bundle(repository, report.run_id)
    objects = dict(bundle.objects)
    explanation = cast(dict[str, Any], json.loads(json.dumps(objects["explanation.json"])))
    cast(dict[str, Any], explanation["explanation"])["evidence_used"] = ["ghost:missing"]
    objects["explanation.json"] = cast(JsonValue, explanation)
    manifest = tuple(
        entry.model_copy(update={"sha256": sha256_digest(objects[entry.path])})
        for entry in bundle.manifest
    )

    with pytest.raises(BundleError, match="cites unavailable evidence"):
        verify_bundle(_resign_v3(bundle, objects=objects, manifest=manifest))
    repository.close()


def test_export_includes_matching_contract_snapshots(tmp_path: Path) -> None:
    repository, report = _persist(tmp_path, "x402-clean-success", adapter_id="x402")
    assert report.contract is not None
    snapshot = build_contract_snapshot(
        cast(str, report.contract.url),
        "x402",
        report.contract,
        cast(JsonValue, {"synthetic": True}),
    )
    repository.save_contract_snapshot(snapshot, datetime(2026, 9, 1, tzinfo=UTC))

    bundle = export_bundle(repository, report.run_id)

    snapshot_paths = [p for p in bundle.objects if p.startswith("snapshots/")]
    assert snapshot_paths == [f"snapshots/{snapshot.snapshot_digest}.json"]
    assert verify_bundle(bundle).run_id == report.run_id
    repository.close()


def test_verify_rejects_snapshot_target_mismatch(tmp_path: Path) -> None:
    repository, report = _persist(tmp_path, "x402-clean-success", adapter_id="x402")
    assert report.contract is not None
    snapshot = build_contract_snapshot(
        cast(str, report.contract.url),
        "x402",
        report.contract,
        cast(JsonValue, {"synthetic": True}),
    )
    repository.save_contract_snapshot(snapshot, datetime(2026, 9, 1, tzinfo=UTC))
    bundle = export_bundle(repository, report.run_id)

    objects = dict(bundle.objects)
    snapshot_path = f"snapshots/{snapshot.snapshot_digest}.json"
    tampered = snapshot.model_dump(mode="json")
    tampered["target"] = "https://example.invalid/other"
    tampered["snapshot_digest"] = snapshot.snapshot_digest
    objects[snapshot_path] = cast(JsonValue, tampered)
    manifest = tuple(
        entry.model_copy(update={"sha256": sha256_digest(objects[entry.path])})
        for entry in bundle.manifest
    )

    with pytest.raises(BundleError, match="not valid"):
        verify_bundle(_resign_v3(bundle, objects=objects, manifest=manifest))
    repository.close()


def test_x402_report_round_trips_with_settlement_evidence(tmp_path: Path) -> None:
    repository, report = _persist(tmp_path, "x402-clean-success", adapter_id="x402")

    verified = verify_bundle(
        load_bundle(serialize_bundle(export_bundle(repository, report.run_id)))
    )

    assert verified == redact_report(report)
    assert verified.adapter_id == "x402"
    assert verified.receipt is not None
    assert verified.ledger is not None
    compatibility = export_bundle(repository, report.run_id).compatibility
    assert compatibility.payment_adapter_id == "x402"
    assert compatibility.x402_protocol_version == "2"
    assert compatibility.x402_signer_schema_version == 3
    repository.close()


@pytest.mark.parametrize(
    "updates",
    [
        {"payment_adapter_id": "perflo"},
        {"x402_protocol_version": None},
        {"x402_signer_schema_version": None},
        {"database_schema_version": 7},
    ],
)
def test_verify_rejects_resigned_inconsistent_compatibility_metadata(
    tmp_path: Path, updates: dict[str, object]
) -> None:
    repository, report = _persist(tmp_path, "x402-clean-success", adapter_id="x402")
    bundle = export_bundle(repository, report.run_id)
    compatibility = bundle.compatibility.model_copy(update=updates)

    with pytest.raises(BundleError, match="compatibility"):
        verify_bundle(_resign_v3(bundle, compatibility=compatibility))
    repository.close()


@pytest.mark.parametrize("scenario", ["missing-activity", "ambiguous-activity"])
def test_verify_accepts_internally_consistent_unverifiable_fixture_report(
    tmp_path: Path, scenario: str
) -> None:
    repository, report = _persist(tmp_path, scenario)

    verified = verify_bundle(export_bundle(repository, report.run_id))

    assert verified == redact_report(report)
    assert verified.verdict is Verdict.UNVERIFIABLE
    repository.close()


# --- schema-2 compatibility (constructed directly; export no longer emits v2) ---


def test_schema_v2_bundle_bytes_and_verify_unchanged() -> None:
    report = replay_fixture(FIXTURES / "clean-success")
    artifacts = tuple(
        redact_artifact(a) for a in _fixture_artifacts("clean-success", report.run_id)
    )
    bundle = _v2_bundle(report, artifacts=artifacts)

    serialized = serialize_bundle(bundle)
    assert sha256(serialized).hexdigest() == SCHEMA_V2_CANONICAL_DIGEST
    loaded = load_bundle(serialized)
    assert isinstance(loaded, EvidenceBundle)
    assert loaded == bundle
    assert verify_bundle(loaded) == report


def _legacy_v2_payload(bundle: EvidenceBundle) -> dict[str, object]:
    payload = cast(dict[str, object], bundle.model_dump(mode="json"))
    compatibility = cast(dict[str, object], payload["compatibility"])
    for field in (
        "payment_adapter_id",
        "x402_protocol_version",
        "x402_signer_schema_version",
    ):
        if field not in bundle.compatibility.model_fields_set:
            compatibility.pop(field, None)
    report = cast(dict[str, object], payload["report"])
    if report["schema_version"] == 1:
        report.pop("receipt", None)
        report.pop("adapter_id", None)
        for name in ("contract", "execution", "ledger"):
            record_value = report.get(name)
            if not isinstance(record_value, dict):
                continue
            record = cast(dict[str, object], record_value)
            for field in ("scheme", "network", "asset_identity"):
                record.pop(field, None)
        contract_value = report.get("contract")
        if isinstance(contract_value, dict):
            contract = cast(dict[str, object], contract_value)
            contract.pop("recipient", None)
            contract.pop("max_timeout_seconds", None)
    return payload


def test_schema_v2_serialization_matches_legacy_projection() -> None:
    report = replay_fixture(FIXTURES / "clean-success")
    artifacts = tuple(
        redact_artifact(a) for a in _fixture_artifacts("clean-success", report.run_id)
    )
    bundle = _v2_bundle(report, artifacts=artifacts)

    legacy = canonical_json_bytes(_legacy_v2_payload(bundle))

    assert serialize_bundle(bundle) == legacy
    assert sha256(legacy).hexdigest() == SCHEMA_V2_CANONICAL_DIGEST


def test_schema_v2_verify_rejects_tamper_and_unredacted() -> None:
    report = replay_fixture(FIXTURES / "clean-success")
    artifact = _fixture_artifacts("clean-success", report.run_id)[0].model_copy(
        update={"redacted": False}
    )
    bundle = _v2_bundle(report, artifacts=(artifact,))

    with pytest.raises(BundleError, match="redacted"):
        verify_bundle(bundle)
    with pytest.raises(BundleError, match="integrity"):
        verify_bundle(bundle.model_copy(update={"integrity": "1" * 64}))


def test_schema_v2_explanation_mismatch() -> None:
    report = replay_fixture(FIXTURES / "clean-success")
    explanation = fallback_explanation(report, set()).model_copy(
        update={
            "run_id": "run:other",
            "finding_ids": ("finding:missing",),
            "deterministic_verdict": Verdict.PAID_FAILURE,
        }
    )
    bundle = _v2_bundle(
        report,
        explanation=ExplanationRecord(
            explanation=explanation, source=ExplanationSource.FALLBACK, tool_calls=0
        ),
    )

    with pytest.raises(BundleError, match="explanation"):
        verify_bundle(bundle)


def test_current_bundle_reads_schema_v1_report_without_v2_fields() -> None:
    report = replay_fixture(FIXTURES / "clean-success")
    bundle = _v2_bundle(report)
    payload = json.loads(serialize_bundle(bundle))
    report_payload = cast(dict[str, Any], payload["report"])
    report_payload["schema_version"] = 1
    report_payload.pop("receipt", None)
    report_payload.pop("adapter_id", None)
    for name in ("contract", "execution", "ledger"):
        record = cast(dict[str, Any], report_payload[name])
        record["schema_version"] = 1
        for field in ("scheme", "network", "asset_identity"):
            record.pop(field, None)
    contract = cast(dict[str, Any], report_payload["contract"])
    contract.pop("recipient", None)
    contract.pop("max_timeout_seconds", None)
    compatibility = cast(dict[str, Any], payload["compatibility"])
    compatibility["report_schema_version"] = 1
    unsigned = {key: value for key, value in payload.items() if key != "integrity"}
    payload["integrity"] = sha256(
        json.dumps(unsigned, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()

    loaded = load_bundle(json.dumps(payload).encode())

    assert isinstance(loaded, EvidenceBundle)
    assert loaded.report.schema_version == 1
    assert loaded.report.receipt is None
    assert verify_bundle(loaded) == loaded.report


def test_load_rejects_unknown_schema_version() -> None:
    report = replay_fixture(FIXTURES / "clean-success")
    payload = json.loads(serialize_bundle(_v2_bundle(report)))
    payload["schema_version"] = 1

    with pytest.raises(BundleError, match="bundle"):
        load_bundle(json.dumps(payload).encode())


SCHEMA_V2_CANONICAL_DIGEST = "e9267a7fd54fd196077bda1f243d87935896aedda7b6a893b86159467a418133"


def _resigned_object(bundle: EvidenceBundleV3, path: str, value: Any) -> EvidenceBundleV3:
    objects = dict(bundle.objects)
    objects[path] = cast(JsonValue, value)
    manifest = tuple(
        BundleManifestEntry(path=p, sha256=sha256_digest(objects[p])) for p in sorted(objects)
    )
    return _resign_v3(bundle, objects=objects, manifest=manifest)


@pytest.mark.parametrize("missing", ["timeline.json", "run-events.json"])
def test_verify_rejects_missing_base_object(tmp_path: Path, missing: str) -> None:
    repository, report = _persist(tmp_path, "clean-success")
    bundle = export_bundle(repository, report.run_id)
    objects = dict(bundle.objects)
    objects.pop(missing)
    manifest = tuple(entry for entry in bundle.manifest if entry.path != missing)

    with pytest.raises(BundleError, match="missing required object"):
        verify_bundle(_resign_v3(bundle, objects=objects, manifest=manifest))
    repository.close()


def test_verify_rejects_noncanonical_artifact_path(tmp_path: Path) -> None:
    repository, report = _persist(tmp_path, "clean-success")
    bundle = export_bundle(repository, report.run_id)
    objects = dict(bundle.objects)
    artifact_path = next(p for p in objects if p.startswith("artifacts/"))
    artifact = objects.pop(artifact_path)
    decoded = base64.urlsafe_b64decode("AA_=").decode("utf-8")
    artifact = json.loads(json.dumps(artifact))
    artifact["artifact_id"] = decoded
    objects["artifacts/a-AA_.json"] = cast(JsonValue, artifact)
    manifest = tuple(
        BundleManifestEntry(path=p, sha256=sha256_digest(objects[p])) for p in sorted(objects)
    )

    with pytest.raises(BundleError, match="not canonical|does not match"):
        verify_bundle(_resign_v3(bundle, objects=objects, manifest=manifest))
    repository.close()


@pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity"])
def test_load_rejects_json_constants(tmp_path: Path, constant: str) -> None:
    repository, report = _persist(tmp_path, "clean-success")
    bundle = export_bundle(repository, report.run_id)
    serialized = serialize_bundle(bundle).replace(
        b'"tool_calls":0', f'"tool_calls":{constant}'.encode(), 1
    )
    assert constant.encode() in serialized

    with pytest.raises(BundleError, match="bundle"):
        load_bundle(serialized)
    repository.close()


def test_verify_rejects_oversized_bundle(tmp_path: Path) -> None:
    repository = SQLiteReportRepository(tmp_path / "reports.sqlite3")
    report = replay_fixture(FIXTURES / "clean-success")
    artifacts = list(_fixture_artifacts("clean-success", report.run_id))
    artifacts[0] = artifacts[0].model_copy(update={"data": {"pad": "x" * (17 * 1024 * 1024)}})
    repository.save(report, events=(EVENT,), artifacts=tuple(artifacts))

    with pytest.raises(BundleError, match="maximum size"):
        export_bundle(repository, report.run_id)
    repository.close()


def test_verify_rejects_secret_bearing_redacted_flagged_artifact(tmp_path: Path) -> None:
    repository, report = _persist(tmp_path, "clean-success")
    bundle = export_bundle(repository, report.run_id)
    artifact_path = next(p for p in bundle.objects if p.startswith("artifacts/"))
    artifact = json.loads(json.dumps(bundle.objects[artifact_path]))
    artifact["data"] = {"apiKey": "live-secret-value"}

    with pytest.raises(BundleError, match="unredacted"):
        verify_bundle(_resigned_object(bundle, artifact_path, artifact))
    repository.close()


def test_verify_rejects_unredacted_report_content(tmp_path: Path) -> None:
    repository, report = _persist(tmp_path, "clean-success")
    bundle = export_bundle(repository, report.run_id)
    report_object = json.loads(json.dumps(bundle.objects["report.json"]))
    cast(dict[str, Any], report_object["intent"])["task"] = (
        "email me at secret@example.com about the task"
    )
    with pytest.raises(BundleError):
        verify_bundle(_resigned_object(bundle, "report.json", report_object))
    repository.close()


def test_verify_rejects_unredacted_timeline_attribute(tmp_path: Path) -> None:
    repository, report = _persist(tmp_path, "clean-success")
    bundle = export_bundle(repository, report.run_id)
    timeline = json.loads(json.dumps(bundle.objects["timeline.json"]))
    cast(dict[str, Any], timeline[-1]["attributes"])["note"] = (
        "token 0x155463b78af48b2db07583c266b18e35bee4eed7"
    )

    with pytest.raises(BundleError, match="unredacted"):
        verify_bundle(_resigned_object(bundle, "timeline.json", timeline))
    repository.close()


def test_verify_rejects_unredacted_explanation_rejected_output(tmp_path: Path) -> None:
    repository, report = _persist(tmp_path, "clean-success")
    bundle = export_bundle(repository, report.run_id)
    explanation = json.loads(json.dumps(bundle.objects["explanation.json"]))
    explanation["rejected_output"] = '{"apiKey": "live-secret"}'

    with pytest.raises(BundleError, match="unredacted|explanation"):
        verify_bundle(_resigned_object(bundle, "explanation.json", explanation))
    repository.close()


def _x402_bundle_with_snapshot(
    tmp_path: Path,
) -> tuple[SQLiteReportRepository, EvidenceBundleV3]:
    repository, report = _persist(tmp_path, "x402-clean-success", adapter_id="x402")
    assert report.contract is not None
    snapshot = build_contract_snapshot(
        cast(str, report.contract.url),
        "x402",
        report.contract,
        cast(JsonValue, {"note": "synthetic"}),
    )
    repository.save_contract_snapshot(snapshot, datetime(2026, 9, 1, tzinfo=UTC))
    return repository, export_bundle(repository, report.run_id)


def _snapshot_path(bundle: EvidenceBundleV3) -> str:
    return next(path for path in bundle.objects if path.startswith("snapshots/"))


def _replace_snapshot_object(
    bundle: EvidenceBundleV3, snapshot: ContractSnapshot
) -> EvidenceBundleV3:
    objects = dict(bundle.objects)
    objects.pop(_snapshot_path(bundle))
    objects[f"snapshots/{snapshot.snapshot_digest}.json"] = cast(
        JsonValue, snapshot.model_dump(mode="json")
    )
    manifest = tuple(
        BundleManifestEntry(path=path, sha256=sha256_digest(objects[path]))
        for path in sorted(objects)
    )
    return _resign_v3(bundle, objects=objects, manifest=manifest)


def test_verify_rejects_unredacted_snapshot_source(tmp_path: Path) -> None:
    repository, bundle = _x402_bundle_with_snapshot(tmp_path)
    original = ContractSnapshot.model_validate_json(
        json.dumps(bundle.objects[_snapshot_path(bundle)]), strict=True
    )
    source_contract = cast(JsonValue, {"apiKey": "live-secret"})
    source_digest = sha256_digest(source_contract)
    snapshot_digest = sha256_digest(
        {
            "target": original.target,
            "rail": original.rail,
            "semantic_fingerprint": original.semantic_fingerprint,
            "source_digest": source_digest,
        }
    )
    malicious = ContractSnapshot.model_validate_json(
        json.dumps(
            original.model_dump(mode="json")
            | {
                "source_contract": source_contract,
                "source_digest": source_digest,
                "snapshot_digest": snapshot_digest,
            }
        ),
        strict=True,
    )

    with pytest.raises(BundleError, match="unredacted contract snapshot"):
        verify_bundle(_replace_snapshot_object(bundle, malicious))
    repository.close()


def test_verify_rejects_unredacted_snapshot_contract(tmp_path: Path) -> None:
    repository, bundle = _x402_bundle_with_snapshot(tmp_path)
    original = ContractSnapshot.model_validate_json(
        json.dumps(bundle.objects[_snapshot_path(bundle)]), strict=True
    )
    contract = original.contract.model_dump(mode="json")
    contract["recipient"] = "0x2222222222222222222222222222222222222222"
    malicious = ContractSnapshot.model_validate_json(
        json.dumps(original.model_dump(mode="json") | {"contract": contract}),
        strict=True,
    )
    assert malicious.snapshot_digest == original.snapshot_digest

    with pytest.raises(BundleError, match="unredacted contract snapshot"):
        verify_bundle(_replace_snapshot_object(bundle, malicious))
    repository.close()


def test_aliases_resolve_only_when_artifact_type_included(tmp_path: Path) -> None:
    repository = SQLiteReportRepository(tmp_path / "reports.sqlite3")
    report = replay_fixture(FIXTURES / "clean-success")
    artifacts = tuple(
        artifact
        for artifact in _fixture_artifacts("clean-success", report.run_id)
        if artifact.artifact_type is not ArtifactType.ACTIVITY
    )
    repository.save(report, events=(EVENT,), artifacts=artifacts)

    with pytest.raises(BundleError, match="cites unavailable evidence"):
        export_bundle(repository, report.run_id)
    repository.close()


def test_perflo_bundle_includes_snapshot_by_vendor_slug(tmp_path: Path) -> None:
    repository, report = _persist(tmp_path, "clean-success", adapter_id="perflo")
    assert report.contract is not None
    contract = report.contract.model_copy(update={"schema_version": 4, "url": None})
    report = report.model_copy(update={"contract": contract})
    artifacts = _fixture_artifacts("clean-success", report.run_id)
    repository.save(
        report,
        events=(EVENT,),
        artifacts=artifacts,
        explanation=ExplanationRecord(
            explanation=fallback_explanation(
                report, {artifact.artifact_id for artifact in artifacts}
            ),
            source=ExplanationSource.FALLBACK,
            tool_calls=0,
        ),
    )
    snapshot = build_contract_snapshot(
        cast(str, contract.vendor_slug),
        "perflo",
        contract,
        cast(JsonValue, {"note": "synthetic"}),
    )
    repository.save_contract_snapshot(snapshot, datetime(2026, 9, 1, tzinfo=UTC))

    bundle = export_bundle(repository, report.run_id)

    assert f"snapshots/{snapshot.snapshot_digest}.json" in bundle.objects
    repository.close()
