"""Portable, integrity-checked exports of persisted investigation evidence."""

from __future__ import annotations

import json
from collections.abc import Mapping
from hashlib import sha256
from typing import Annotated, Literal, Protocol, cast

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError

from settlediff import __version__
from settlediff.application.run import RunEvent
from settlediff.domain.models import EvidenceArtifact, ExplanationRecord, MachineReport, NonEmptyStr
from settlediff.domain.redaction import redact_artifact
from settlediff.domain.verdict import derive_verdict

Sha256Digest = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
DATABASE_SCHEMA_VERSION = 3
X402_PROTOCOL_VERSION = "2"
X402_SIGNER_SCHEMA_VERSION = 1


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
    x402_signer_schema_version: Literal[1] | None = None


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


class BundleRepository(Protocol):
    def get(self, run_id: str) -> MachineReport | None: ...

    def events(self, run_id: str) -> tuple[RunEvent, ...]: ...

    def artifacts(self, run_id: str) -> tuple[EvidenceArtifact, ...]: ...

    def explanation(self, run_id: str) -> ExplanationRecord | None: ...


def _canonical_json(value: Mapping[str, object]) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


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
    return sha256(_canonical_json(_payload(bundle))).hexdigest()


def export_bundle(repository: BundleRepository, run_id: str) -> EvidenceBundle:
    """Export one persisted run as a canonical, redacted evidence bundle."""
    report = repository.get(run_id)
    if report is None:
        raise BundleError(f"run {run_id!r} not found")

    artifacts = tuple(redact_artifact(artifact) for artifact in repository.artifacts(run_id))
    if any(not artifact.redacted for artifact in artifacts):
        raise BundleError("bundle contains an unredacted artifact")

    bundle = EvidenceBundle(
        run_id=run_id,
        report=report,
        explanation=repository.explanation(run_id),
        events=repository.events(run_id),
        artifacts=artifacts,
        compatibility=CompatibilityMetadata(
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
        ),
        integrity="0" * 64,
    )
    return bundle.model_copy(update={"integrity": _digest(bundle)})


def verify_bundle(bundle: EvidenceBundle) -> MachineReport:
    """Verify bundle integrity and persisted deterministic-report consistency."""
    if _digest(bundle) != bundle.integrity:
        raise BundleError("bundle integrity digest does not match its payload")

    report = bundle.report
    compatibility = bundle.compatibility
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
    if bundle.run_id != report.run_id or report.run_id != report.intent.run_id:
        raise BundleError("bundle, report, and intent run IDs do not match")
    if any(not artifact.redacted for artifact in bundle.artifacts):
        raise BundleError("bundle contains an unredacted artifact")
    if derive_verdict(report.findings) is not report.verdict:
        raise BundleError("report verdict does not match its persisted findings")

    finding_ids = tuple(finding.finding_id for finding in report.findings)
    if len(set(finding_ids)) != len(finding_ids):
        raise BundleError("report contains duplicate finding IDs")

    if bundle.explanation is not None:
        explanation = bundle.explanation.explanation
        known_finding_ids = set(finding_ids)
        if (
            explanation.run_id != report.run_id
            or explanation.deterministic_verdict is not report.verdict
            or any(finding_id not in known_finding_ids for finding_id in explanation.finding_ids)
        ):
            raise BundleError("explanation does not match the deterministic report")

    return report


def serialize_bundle(bundle: EvidenceBundle) -> bytes:
    """Serialize a bundle as compact, sorted UTF-8 JSON."""
    return _canonical_json(_bundle_payload(bundle, include_integrity=True))


def load_bundle(data: bytes) -> EvidenceBundle:
    """Load strict bundle JSON while presenting one typed boundary error."""
    try:
        return EvidenceBundle.model_validate_json(data, strict=True)
    except (ValidationError, ValueError, UnicodeDecodeError) as error:
        raise BundleError("invalid evidence bundle") from error
