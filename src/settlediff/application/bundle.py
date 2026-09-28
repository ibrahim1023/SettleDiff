"""Portable, integrity-checked exports of persisted investigation evidence."""

from __future__ import annotations

import base64
import binascii
import json
import re
from typing import Annotated, Literal, NoReturn, Protocol, cast

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    ValidationError,
    model_validator,
)

from settlediff import __version__
from settlediff.application.run import RunEvent
from settlediff.application.timeline import EvidenceTimelineEvent, redact_timeline_event
from settlediff.domain.drift import ContractSnapshot
from settlediff.domain.integrity import Sha256Digest, canonical_json_bytes, sha256_digest
from settlediff.domain.models import (
    ArtifactType,
    EvidenceArtifact,
    ExplanationRecord,
    MachineReport,
    NonEmptyStr,
)
from settlediff.domain.redaction import (
    redact_artifact,
    redact_contract,
    redact_explanation_record,
    redact_report,
    redact_value,
)
from settlediff.domain.verdict import derive_verdict

DATABASE_SCHEMA_VERSION = 6
X402_PROTOCOL_VERSION = "2"
X402_SIGNER_SCHEMA_VERSION = 3

_BUNDLE_MAX_BYTES = 16 * 1024 * 1024
_PATH_PATTERN = re.compile(r"[A-Za-z0-9._~/-]+")
_SNAPSHOT_PATH = re.compile(r"snapshots/[0-9a-f]{64}\.json")
_ARTIFACT_PATH = re.compile(r"artifacts/a-[A-Za-z0-9_-]+\.json")
_SUPPORTED_SNAPSHOT_RAILS = frozenset({"perflo", "x402"})
_ARTIFACT_CITATION_ALIASES = {
    ArtifactType.SERVICE_CONTRACT: "contract",
    ArtifactType.EXECUTION: "execution",
    ArtifactType.PAYMENT_RECEIPT: "receipt",
    ArtifactType.ACTIVITY: "activity",
    ArtifactType.CONTEXT_EVIDENCE: "context",
}


class BundleError(ValueError):
    """A bundle could not be exported, loaded, or deterministically verified."""


class CompatibilityMetadata(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    settlediff_version: NonEmptyStr
    report_schema_version: int = Field(ge=1)
    database_schema_version: int = Field(ge=1)
    contextdev_api_path: NonEmptyStr
    hyperfusion_model: NonEmptyStr | None
    perflo_cli_version: NonEmptyStr | None
    payment_adapter_id: NonEmptyStr | None = None
    x402_protocol_version: Literal["2"] | None = None
    x402_signer_schema_version: Literal[2, 3] | None = None


def _validate_logical_path(path: str) -> str:
    if not path or len(path) > 512 or not path.isascii() or _PATH_PATTERN.fullmatch(path) is None:
        raise ValueError("bundle path must be bounded portable ASCII")
    if path.startswith("/") or any(segment in ("", ".", "..") for segment in path.split("/")):
        raise ValueError("bundle path must be relative without traversal")
    return path


LogicalPath = Annotated[str, AfterValidator(_validate_logical_path)]


class BundleManifestEntry(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    path: LogicalPath
    sha256: Sha256Digest


class EvidenceBundle(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    schema_version: Literal[2] = 2
    run_id: NonEmptyStr
    report: MachineReport
    explanation: ExplanationRecord | None
    events: tuple[RunEvent, ...]
    artifacts: tuple[EvidenceArtifact, ...]
    compatibility: CompatibilityMetadata
    integrity: Sha256Digest


class EvidenceBundleV3(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    schema_version: Literal[3] = 3
    run_id: NonEmptyStr
    objects: dict[LogicalPath, JsonValue] = Field(min_length=3, max_length=4096)
    manifest: tuple[BundleManifestEntry, ...] = Field(min_length=3, max_length=4096)
    compatibility: CompatibilityMetadata
    bundle_sha256: Sha256Digest

    @model_validator(mode="after")
    def require_coherent_manifest(self) -> EvidenceBundleV3:
        paths = [entry.path for entry in self.manifest]
        if len(set(paths)) != len(paths):
            raise ValueError("bundle manifest contains duplicate paths")
        if paths != sorted(paths):
            raise ValueError("bundle manifest entries must sort by path")
        if set(paths) != set(self.objects):
            raise ValueError("bundle manifest paths must equal object keys")
        if "manifest.json" in self.objects or "bundle_sha256" in self.objects:
            raise ValueError("bundle objects cannot contain reserved names")
        return self


Bundle = EvidenceBundle | EvidenceBundleV3


class BundleRepository(Protocol):
    def get(self, run_id: str) -> MachineReport | None: ...

    def events(self, run_id: str) -> tuple[RunEvent, ...]: ...

    def timeline(self, run_id: str) -> tuple[EvidenceTimelineEvent, ...]: ...

    def artifacts(self, run_id: str) -> tuple[EvidenceArtifact, ...]: ...

    def explanation(self, run_id: str) -> ExplanationRecord | None: ...

    def contract_snapshots(self, target: str, rail: str) -> tuple[ContractSnapshot, ...]: ...


def _bundle_payload(bundle: EvidenceBundle, *, include_integrity: bool) -> dict[str, object]:
    exclude: set[str] = set() if include_integrity else {"integrity"}
    payload = cast(dict[str, object], bundle.model_dump(mode="json", exclude=exclude))
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


def _payload(bundle: EvidenceBundle) -> dict[str, object]:
    return _bundle_payload(bundle, include_integrity=False)


def _digest(bundle: EvidenceBundle) -> str:
    return sha256_digest(_payload(bundle))


def _v3_payload(bundle: EvidenceBundleV3) -> dict[str, object]:
    return cast(
        dict[str, object],
        bundle.model_dump(mode="json", exclude={"bundle_sha256"}),
    )


def _v3_digest(bundle: EvidenceBundleV3) -> str:
    return sha256_digest(_v3_payload(bundle))


def _artifact_path(artifact_id: str) -> str:
    encoded = base64.urlsafe_b64encode(artifact_id.encode("utf-8")).rstrip(b"=").decode()
    return f"artifacts/a-{encoded}.json"


def _decode_artifact_path(path: str) -> str:
    encoded = path.removeprefix("artifacts/a-").removesuffix(".json")
    try:
        return base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError) as error:
        raise BundleError(f"artifact path {path!r} does not encode an artifact ID") from error


def _compatibility(report: MachineReport) -> CompatibilityMetadata:
    return CompatibilityMetadata(
        settlediff_version=__version__,
        report_schema_version=report.schema_version,
        database_schema_version=DATABASE_SCHEMA_VERSION,
        contextdev_api_path="/web/scrape/markdown",
        hyperfusion_model=None,
        perflo_cli_version=None,
        payment_adapter_id=report.adapter_id,
        x402_protocol_version=(X402_PROTOCOL_VERSION if report.adapter_id == "x402" else None),
        x402_signer_schema_version=(
            X402_SIGNER_SCHEMA_VERSION if report.adapter_id == "x402" else None
        ),
    )


def export_bundle(repository: BundleRepository, run_id: str) -> EvidenceBundleV3:
    """Export one persisted run as a canonical, redacted schema-3 evidence bundle."""
    report = repository.get(run_id)
    if report is None:
        raise BundleError(f"run {run_id!r} not found")

    report = redact_report(report)
    artifacts = tuple(redact_artifact(artifact) for artifact in repository.artifacts(run_id))
    if any(not artifact.redacted for artifact in artifacts):
        raise BundleError("bundle contains an unredacted artifact")
    artifact_ids = [artifact.artifact_id for artifact in artifacts]
    if len(set(artifact_ids)) != len(artifact_ids):
        raise BundleError("persisted run contains duplicate artifact IDs")

    objects: dict[str, JsonValue] = {
        "report.json": cast(JsonValue, report.model_dump(mode="json")),
        "timeline.json": cast(
            JsonValue,
            [
                redact_timeline_event(event).model_dump(mode="json")
                for event in repository.timeline(run_id)
            ],
        ),
        "run-events.json": cast(
            JsonValue,
            [event.model_dump(mode="json") for event in repository.events(run_id)],
        ),
    }
    explanation = repository.explanation(run_id)
    if explanation is not None:
        objects["explanation.json"] = cast(
            JsonValue, redact_explanation_record(explanation).model_dump(mode="json")
        )
    for artifact in artifacts:
        path = _artifact_path(artifact.artifact_id)
        if path in objects:
            raise BundleError("bundle object path collision")
        objects[path] = cast(JsonValue, artifact.model_dump(mode="json"))

    snapshots: tuple[ContractSnapshot, ...] = ()
    contract = report.contract
    if contract is not None and report.adapter_id in _SUPPORTED_SNAPSHOT_RAILS:
        target = contract.vendor_slug if report.adapter_id == "perflo" else contract.url
        if target is not None:
            snapshots = repository.contract_snapshots(target, report.adapter_id)
    snapshot_digests = [snapshot.snapshot_digest for snapshot in snapshots]
    if len(set(snapshot_digests)) != len(snapshot_digests):
        raise BundleError("persisted run contains duplicate snapshot digests")
    for snapshot in snapshots:
        path = f"snapshots/{snapshot.snapshot_digest}.json"
        if path in objects:
            raise BundleError("bundle object path collision")
        objects[path] = cast(JsonValue, snapshot.model_dump(mode="json"))

    manifest = tuple(
        BundleManifestEntry(path=path, sha256=sha256_digest(objects[path]))
        for path in sorted(objects)
    )
    unsigned = EvidenceBundleV3(
        run_id=run_id,
        objects=objects,
        manifest=manifest,
        compatibility=_compatibility(report),
        bundle_sha256="0" * 64,
    )
    bundle = EvidenceBundleV3(
        run_id=unsigned.run_id,
        objects=unsigned.objects,
        manifest=unsigned.manifest,
        compatibility=unsigned.compatibility,
        bundle_sha256=_v3_digest(unsigned),
    )
    verify_bundle(bundle)
    return bundle


def _check_compatibility(compatibility: CompatibilityMetadata, report: MachineReport) -> None:
    if compatibility.report_schema_version != report.schema_version:
        raise BundleError("bundle compatibility metadata does not match report schema")
    if compatibility.database_schema_version > DATABASE_SCHEMA_VERSION:
        raise BundleError("bundle compatibility metadata requires a newer database schema")
    if (
        compatibility.payment_adapter_id is not None
        and compatibility.payment_adapter_id != report.adapter_id
    ):
        raise BundleError("bundle compatibility adapter does not match report provenance")
    x402_fields_present = bool(
        {"x402_protocol_version", "x402_signer_schema_version"} & compatibility.model_fields_set
    )
    if compatibility.payment_adapter_id == "x402" and x402_fields_present:
        if (
            compatibility.x402_protocol_version != X402_PROTOCOL_VERSION
            or compatibility.x402_signer_schema_version != X402_SIGNER_SCHEMA_VERSION
        ):
            raise BundleError("bundle compatibility x402 contract is incomplete")
    elif (
        compatibility.x402_protocol_version is not None
        or compatibility.x402_signer_schema_version is not None
    ):
        raise BundleError("bundle compatibility x402 contract lacks x402 adapter provenance")


def _check_report_consistency(
    run_id: str, report: MachineReport, explanation: ExplanationRecord | None
) -> None:
    if run_id != report.run_id or report.run_id != report.intent.run_id:
        raise BundleError("bundle, report, and intent run IDs do not match")
    if derive_verdict(report.findings, delivery=report.delivery) is not report.verdict:
        raise BundleError("report verdict does not match its persisted findings")
    finding_ids = tuple(finding.finding_id for finding in report.findings)
    if len(set(finding_ids)) != len(finding_ids):
        raise BundleError("report contains duplicate finding IDs")
    if explanation is not None:
        record = explanation.explanation
        known_finding_ids = set(finding_ids)
        if (
            record.run_id != report.run_id
            or record.deterministic_verdict is not report.verdict
            or any(finding_id not in known_finding_ids for finding_id in record.finding_ids)
        ):
            raise BundleError("explanation does not match the deterministic report")


def _verify_v2(bundle: EvidenceBundle) -> MachineReport:
    if _digest(bundle) != bundle.integrity:
        raise BundleError("bundle integrity digest does not match its payload")

    report = bundle.report
    _check_compatibility(bundle.compatibility, report)
    _check_report_consistency(bundle.run_id, report, bundle.explanation)
    if any(not artifact.redacted for artifact in bundle.artifacts):
        raise BundleError("bundle contains an unredacted artifact")

    return report


def _decode_object(path: str, value: JsonValue, model: type[BaseModel]) -> BaseModel:
    try:
        return model.model_validate_json(json.dumps(value), strict=True)
    except ValidationError as error:
        raise BundleError(f"bundle object {path!r} is not valid {model.__name__}") from error


def _verify_v3(bundle: EvidenceBundleV3) -> MachineReport:
    if _v3_digest(bundle) != bundle.bundle_sha256:
        raise BundleError("bundle integrity digest does not match its payload")
    if len(serialize_bundle(bundle)) > _BUNDLE_MAX_BYTES:
        raise BundleError("bundle exceeds maximum size")
    for entry in bundle.manifest:
        if sha256_digest(bundle.objects[entry.path]) != entry.sha256:
            raise BundleError(f"bundle object {entry.path!r} does not match its manifest digest")

    report: MachineReport | None = None
    explanation: ExplanationRecord | None = None
    timeline: list[EvidenceTimelineEvent] = []
    artifacts: list[EvidenceArtifact] = []
    snapshots: list[ContractSnapshot] = []
    for path, value in bundle.objects.items():
        if path == "report.json":
            report = cast(MachineReport, _decode_object(path, value, MachineReport))
        elif path == "timeline.json":
            if not isinstance(value, list):
                raise BundleError("bundle object 'timeline.json' must be an array")
            timeline = [
                cast(
                    EvidenceTimelineEvent,
                    _decode_object(path, item, EvidenceTimelineEvent),
                )
                for item in cast(list[JsonValue], value)
            ]
        elif path == "run-events.json":
            if not isinstance(value, list):
                raise BundleError("bundle object 'run-events.json' must be an array")
            for item in cast(list[JsonValue], value):
                _decode_object(path, item, RunEvent)
        elif path == "explanation.json":
            explanation = cast(ExplanationRecord, _decode_object(path, value, ExplanationRecord))
        elif _ARTIFACT_PATH.fullmatch(path):
            artifact = cast(EvidenceArtifact, _decode_object(path, value, EvidenceArtifact))
            decoded_id = _decode_artifact_path(path)
            if decoded_id != artifact.artifact_id:
                raise BundleError(f"bundle artifact path {path!r} does not match its content")
            if _artifact_path(decoded_id) != path:
                raise BundleError(f"bundle artifact path {path!r} is not canonical")
            artifacts.append(artifact)
        elif _SNAPSHOT_PATH.fullmatch(path):
            snapshot = cast(ContractSnapshot, _decode_object(path, value, ContractSnapshot))
            if path.removeprefix("snapshots/").removesuffix(".json") != (snapshot.snapshot_digest):
                raise BundleError(f"bundle snapshot path {path!r} does not match its digest")
            snapshots.append(snapshot)
        else:
            raise BundleError(f"bundle contains unexpected object {path!r}")
    missing = {
        "report.json",
        "timeline.json",
        "run-events.json",
    } - set(bundle.objects)
    if missing:
        raise BundleError(f"bundle is missing required object {sorted(missing)[0]!r}")
    if report is None:
        raise BundleError("bundle is missing report.json")

    _check_compatibility(bundle.compatibility, report)
    _check_report_consistency(bundle.run_id, report, explanation)
    if redact_report(report) != report:
        raise BundleError("bundle report is not fully redacted")
    if any(
        not artifact.redacted or redact_artifact(artifact) != artifact for artifact in artifacts
    ):
        raise BundleError("bundle contains an unredacted artifact")
    if any(redact_timeline_event(event) != event for event in timeline):
        raise BundleError("bundle contains an unredacted timeline event")
    if explanation is not None and redact_explanation_record(explanation) != explanation:
        raise BundleError("bundle explanation is not fully redacted")
    for snapshot in snapshots:
        if redact_contract(snapshot.contract) != snapshot.contract or (
            redact_value(snapshot.source_contract) != snapshot.source_contract
        ):
            raise BundleError("bundle contains an unredacted contract snapshot")
    artifact_ids = [artifact.artifact_id for artifact in artifacts]
    if len(set(artifact_ids)) != len(artifact_ids):
        raise BundleError("bundle contains duplicate artifact IDs")
    snapshot_digests = [snapshot.snapshot_digest for snapshot in snapshots]
    if len(set(snapshot_digests)) != len(snapshot_digests):
        raise BundleError("bundle contains duplicate snapshot digests")

    if snapshots:
        contract = report.contract
        if contract is None or report.adapter_id not in _SUPPORTED_SNAPSHOT_RAILS:
            raise BundleError("bundle snapshots require contract and adapter provenance")
        target = contract.vendor_slug if report.adapter_id == "perflo" else contract.url
        for snapshot in snapshots:
            if snapshot.target != target or snapshot.rail != report.adapter_id:
                raise BundleError("bundle snapshot does not match report contract provenance")

    for position, event in enumerate(timeline):
        if event.sequence != position:
            raise BundleError("bundle timeline sequence does not match event position")

    citations = set(artifact_ids)
    citations.update(
        alias
        for artifact_type, alias in _ARTIFACT_CITATION_ALIASES.items()
        if any(artifact.artifact_type is artifact_type for artifact in artifacts)
    )
    citations.update(
        (
            f"{report.run_id}:intent",
            f"{report.run_id}:report",
            f"{report.run_id}:run_state",
        )
    )
    finding_ids = {finding.finding_id for finding in report.findings}

    def require_citations(kind: str, ids: tuple[str, ...]) -> None:
        unresolved = [evidence_id for evidence_id in ids if evidence_id not in citations]
        if unresolved:
            raise BundleError(f"{kind} cites unavailable evidence {unresolved[0]!r}")

    for finding in report.findings:
        require_citations("finding", finding.artifact_ids)
    if report.delivery is not None:
        require_citations("delivery", report.delivery.evidence_ids)
    if report.retry is not None:
        require_citations("retry", report.retry.evidence_ids)
    for event in timeline:
        require_citations("timeline", event.artifact_ids)
        unknown_findings = [
            finding_id for finding_id in event.finding_ids if finding_id not in finding_ids
        ]
        if unknown_findings:
            raise BundleError(f"timeline cites unknown finding {unknown_findings[0]!r}")
    if explanation is not None:
        require_citations("explanation", explanation.explanation.evidence_used)

    return report


def verify_bundle(bundle: Bundle) -> MachineReport:
    """Verify bundle integrity and persisted deterministic-report consistency."""
    if isinstance(bundle, EvidenceBundleV3):
        return _verify_v3(bundle)
    return _verify_v2(bundle)


def serialize_bundle(bundle: Bundle) -> bytes:
    """Serialize a bundle as compact, sorted UTF-8 JSON."""
    if isinstance(bundle, EvidenceBundleV3):
        return canonical_json_bytes(bundle.model_dump(mode="json"))
    return canonical_json_bytes(_bundle_payload(bundle, include_integrity=True))


def _reject_duplicate_keys(pairs: list[tuple[str, JsonValue]]) -> dict[str, JsonValue]:
    keys = [key for key, _value in pairs]
    if len(set(keys)) != len(keys):
        raise BundleError("invalid evidence bundle: duplicate object key")
    return dict(pairs)


def _reject_json_constant(_value: str) -> NoReturn:
    raise BundleError("invalid evidence bundle: non-JSON constant")


def load_bundle(data: bytes) -> Bundle:
    """Load strict bundle JSON while presenting one typed boundary error."""
    if len(data) > _BUNDLE_MAX_BYTES:
        raise BundleError("invalid evidence bundle: exceeds maximum size")
    try:
        decoded = json.loads(
            data,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_json_constant,
        )
    except (json.JSONDecodeError, UnicodeDecodeError, BundleError) as error:
        raise BundleError("invalid evidence bundle") from error
    if not isinstance(decoded, dict):
        raise BundleError("invalid evidence bundle")
    payload = cast(dict[str, JsonValue], decoded)
    version = payload.get("schema_version")
    encoded = canonical_json_bytes(payload)
    try:
        if version == 2:
            return EvidenceBundle.model_validate_json(encoded, strict=True)
        if version == 3:
            return EvidenceBundleV3.model_validate_json(encoded, strict=True)
    except ValidationError as error:
        raise BundleError("invalid evidence bundle") from error
    raise BundleError("invalid evidence bundle: unsupported schema")
