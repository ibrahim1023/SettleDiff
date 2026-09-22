"""Deterministic allowlist public reports separate from local evidence bundles."""

from __future__ import annotations

import contextlib
import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Annotated, Literal, Protocol, cast

from jinja2 import Environment, FileSystemLoader, select_autoescape
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    StringConstraints,
    ValidationError,
    model_validator,
)

from settlediff.application.timeline import EvidenceTimelineEvent
from settlediff.domain.integrity import Sha256Digest, canonical_json_bytes
from settlediff.domain.models import (
    CheckStatus,
    DeliveryAssessment,
    DeliveryObservation,
    DeliveryStatus,
    MachineReport,
    NonEmptyStr,
    RetrySafety,
    Severity,
    UtcDatetime,
    Verdict,
)
from settlediff.domain.redaction import mask_identifier, normalize_key
from settlediff.domain.verdict import derive_verdict

PublicCode = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_.:-]{1,128}$")]


class PublicationError(ValueError):
    """A public report could not be projected, rendered, or written safely."""


class PublicationNotFoundError(PublicationError):
    """The requested run has no persisted report."""


class PublicFinding(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    finding_id: PublicCode
    check_id: PublicCode
    severity: Severity
    status: CheckStatus
    field_paths: tuple[PublicCode, ...] = Field(max_length=16)


class PublicDelivery(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    status: DeliveryStatus
    reason_code: PublicCode
    status_code: int | None = Field(default=None, ge=100, le=599)
    media_type: Annotated[str, StringConstraints(max_length=255)] | None = None
    received_bytes: int | None = Field(default=None, ge=0, le=100_000_000)
    truncated: bool | None = None


class PublicRetry(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    safety: RetrySafety
    reason_codes: tuple[PublicCode, ...] = Field(min_length=1, max_length=16)


class PublicTimelineEvent(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    sequence: int = Field(ge=0)
    source_time: UtcDatetime | None
    observed_at: UtcDatetime
    source: PublicCode
    attributes: dict[PublicCode, str | bool | int | None] = Field(max_length=12)


class PublicReport(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    source_report_schema_version: int = Field(ge=1)
    public_run_id: NonEmptyStr
    evidence_through: UtcDatetime
    verdict: Verdict
    findings: tuple[PublicFinding, ...]
    delivery: PublicDelivery | None
    retry: PublicRetry | None
    timeline: tuple[PublicTimelineEvent, ...]


class PublicManifestEntry(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    path: Literal["index.html", "report.json"]
    sha256: Sha256Digest


class PublicManifest(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    source_timestamp: UtcDatetime
    objects: tuple[PublicManifestEntry, ...] = Field(min_length=2, max_length=2)

    @model_validator(mode="after")
    def require_sorted_unique_objects(self) -> PublicManifest:
        paths = [entry.path for entry in self.objects]
        if paths != sorted(set(paths)):
            raise ValueError("public manifest objects must be unique and sorted by path")
        return self


@dataclass(frozen=True)
class PublicationFiles:
    report_json: bytes
    index_html: bytes
    manifest_json: bytes


class PublicationRepository(Protocol):
    def get(self, run_id: str) -> MachineReport | None: ...

    def timeline(self, run_id: str) -> tuple[EvidenceTimelineEvent, ...]: ...


_PUBLIC_CODE_PATTERN = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
_PUBLIC_SOURCE_PREFIXES = ("settlediff.", "perflo.", "x402.")
_PUBLIC_ATTRIBUTE_NAMES = frozenset(
    {
        "event",
        "state",
        "artifact_type",
        "redacted",
        "status_code",
        "received_bytes",
        "truncated",
        "delivery_status",
        "reason_code",
        "media_type",
    }
)
_PUBLIC_FILES = ("index.html", "report.json", "public-manifest.json")

_URL_PATTERN = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://|www\.")
_EMAIL_PATTERN = re.compile(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]{1,64}@[A-Za-z0-9.-]")
_PREFIXED_HEX_PATTERN = re.compile(r"0x[0-9a-fA-F]{16,}")
_BARE_HEX_PATTERN = re.compile(r"\b[0-9a-fA-F]{32,128}\b")
_LOCAL_PATH_PATTERN = re.compile(r"/Users/|/home/|[A-Za-z]:[\\/]")
_QUERY_CREDENTIAL_PATTERN = re.compile(
    r"(?i)(api[_-]?key|token|secret|password|credential|signature|sessionid)="
)
_SECRET_KEY_NAMES = frozenset(
    {
        "apikey",
        "authorization",
        "bearer",
        "clientsecret",
        "cookie",
        "credential",
        "idtoken",
        "password",
        "paymentpayload",
        "paymentsignature",
        "privatekey",
        "refreshtoken",
        "signature",
        "secret",
        "token",
        "accesstoken",
    }
)


def _public_code(value: str) -> str | None:
    return value if _PUBLIC_CODE_PATTERN.fullmatch(value) else None


def _guard_disclosure(value: JsonValue, *, key: str | None = None) -> None:
    if key is not None and normalize_key(key) in _SECRET_KEY_NAMES:
        raise PublicationError(f"public report field {key!r} names secret material")
    if isinstance(value, dict):
        for child_key, child in cast(dict[str, JsonValue], value).items():
            _guard_disclosure(child, key=child_key)
        return
    if isinstance(value, list):
        for item in cast(list[JsonValue], value):
            _guard_disclosure(item, key=key)
        return
    if not isinstance(value, str):
        return
    if (
        _URL_PATTERN.search(value)
        or _EMAIL_PATTERN.search(value)
        or _PREFIXED_HEX_PATTERN.search(value)
        or _BARE_HEX_PATTERN.search(value)
        or _LOCAL_PATH_PATTERN.search(value)
        or _QUERY_CREDENTIAL_PATTERN.search(value)
        or ".." in value.split("/")
    ):
        raise PublicationError(f"public report value under {key or 'root'!r} is disclosive")


def _public_source(source: str) -> str:
    if _PUBLIC_CODE_PATTERN.fullmatch(source) and (
        source == "fixture" or source.startswith(_PUBLIC_SOURCE_PREFIXES)
    ):
        return source
    return "other"


def _public_attributes(
    attributes: dict[str, str | bool | int | None],
) -> dict[str, str | bool | int | None]:
    public: dict[str, str | bool | int | None] = {}
    for name in sorted(attributes):
        if name not in _PUBLIC_ATTRIBUTE_NAMES or len(public) >= 12:
            continue
        value = attributes[name]
        if isinstance(value, str) and _public_code(value) is None:
            continue
        public[name] = value
    return public


def build_public_report(
    report: MachineReport, timeline: tuple[EvidenceTimelineEvent, ...]
) -> PublicReport:
    """Project a persisted report and timeline onto the dedicated public allowlist."""
    if report.run_id != report.intent.run_id:
        raise PublicationError("report and intent run IDs do not match")
    if derive_verdict(report.findings, delivery=report.delivery) is not report.verdict:
        raise PublicationError("report verdict does not match its findings and delivery")
    finding_ids = [finding.finding_id for finding in report.findings]
    if len(set(finding_ids)) != len(finding_ids):
        raise PublicationError("report contains duplicate finding IDs")
    events: list[PublicTimelineEvent] = []
    for position, event in enumerate(timeline):
        if event.sequence != position:
            raise PublicationError("timeline event sequence does not match its position")
        events.append(
            PublicTimelineEvent(
                sequence=event.sequence,
                source_time=event.source_time,
                observed_at=event.observed_at,
                source=_public_source(event.source),
                attributes=_public_attributes(dict(event.attributes)),
            )
        )
    delivery = report.delivery
    observation = delivery.observation if delivery is not None else None
    try:
        public = _build_report(
            report=report,
            events=events,
            delivery=delivery,
            observation=observation,
            evidence_through=(
                max(event.observed_at for event in timeline)
                if timeline
                else report.intent.created_at
            ),
        )
    except ValidationError as error:
        raise PublicationError("report content cannot satisfy the public schema") from error
    _guard_disclosure(cast(JsonValue, public.model_dump(mode="json")))
    return public


def _build_report(
    *,
    report: MachineReport,
    events: list[PublicTimelineEvent],
    delivery: DeliveryAssessment | None,
    observation: DeliveryObservation | None,
    evidence_through: UtcDatetime,
) -> PublicReport:
    return PublicReport(
        source_report_schema_version=report.schema_version,
        public_run_id=mask_identifier(report.run_id),
        evidence_through=evidence_through,
        verdict=report.verdict,
        findings=tuple(
            PublicFinding(
                finding_id=finding.finding_id,
                check_id=finding.check_id,
                severity=finding.severity,
                status=finding.status,
                field_paths=tuple(
                    path for path in finding.field_paths if _public_code(path) is not None
                ),
            )
            for finding in sorted(report.findings, key=lambda item: item.finding_id)
        ),
        delivery=(
            PublicDelivery(
                status=delivery.status,
                reason_code=delivery.reason_code,
                status_code=observation.status_code if observation is not None else None,
                media_type=observation.media_type if observation is not None else None,
                received_bytes=(observation.received_bytes if observation is not None else None),
                truncated=observation.truncated if observation is not None else None,
            )
            if delivery is not None
            else None
        ),
        retry=(
            PublicRetry(
                safety=report.retry.safety,
                reason_codes=report.retry.reason_codes,
            )
            if report.retry is not None
            else None
        ),
        timeline=tuple(events),
    )


def _templates() -> Environment:
    return Environment(
        loader=FileSystemLoader(Path(__file__).parents[1] / "ui" / "templates"),
        autoescape=select_autoescape(["html"]),
    )


def _check_html_static(encoded: bytes) -> None:
    lowered = encoded.lower()
    if (
        b"<script" in lowered
        or b"src=" in lowered
        or b"href=" in lowered
        or b"https://" in lowered
        or b"http://" in lowered
        or b"track" in lowered
    ):
        raise PublicationError("public report HTML contains disallowed constructs")


def render_public_report(public_report: PublicReport) -> bytes:
    """Render a deterministic standalone public HTML report."""
    rendered = _templates().get_template("public_report.html").render(report=public_report)
    encoded = rendered.encode("utf-8")
    _check_html_static(encoded)
    return encoded


def build_publication(repository: PublicationRepository, run_id: str) -> PublicationFiles:
    """Build the three public output bytes for one persisted run."""
    report = repository.get(run_id)
    if report is None:
        raise PublicationNotFoundError(f"run {run_id!r} not found")
    public = build_public_report(report, repository.timeline(run_id))
    report_json = canonical_json_bytes(public.model_dump(mode="json"))
    index_html = render_public_report(public)
    manifest = PublicManifest(
        source_timestamp=public.evidence_through,
        objects=(
            PublicManifestEntry(path="index.html", sha256=sha256(index_html).hexdigest()),
            PublicManifestEntry(path="report.json", sha256=sha256(report_json).hexdigest()),
        ),
    )
    return PublicationFiles(
        report_json=report_json,
        index_html=index_html,
        manifest_json=canonical_json_bytes(manifest.model_dump(mode="json")),
    )


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _contains_symlink(path: Path) -> bool:
    with os.scandir(path) as entries:
        for entry in entries:
            if entry.is_symlink():
                return True
            if entry.is_dir(follow_symlinks=False) and _contains_symlink(Path(entry.path)):
                return True
    return False


def _has_symlink_ancestor(path: Path) -> bool:
    current = path
    while True:
        if current.is_symlink():
            return True
        parent = current.parent
        if parent == current:
            return False
        if not parent.exists():
            return False
        current = parent


def _checked_output(output: Path) -> Path:
    if any(part == ".." for part in output.parts):
        raise PublicationError("publication output path cannot contain '..'")
    absolute = output.absolute()
    if absolute == Path(absolute.anchor) or absolute == Path.cwd():
        raise PublicationError("publication output cannot be a root or current directory")
    parent = output.parent
    if not parent.is_dir():
        raise PublicationError("publication output parent is not a directory")
    if _has_symlink_ancestor(output.parent):
        raise PublicationError("publication output path contains a symlink ancestor")
    if output.is_symlink():
        raise PublicationError("publication output cannot be a symlink")
    return output


def _write_files(stage: Path, files: PublicationFiles) -> None:
    payloads = {
        "index.html": files.index_html,
        "report.json": files.report_json,
        "public-manifest.json": files.manifest_json,
    }
    for name, payload in payloads.items():
        target = stage / name
        descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
        except BaseException:
            with contextlib.suppress(OSError):
                os.close(descriptor)
            raise
    _fsync_directory(stage)


def _verify_installed(output: Path, files: PublicationFiles) -> None:
    expected = {
        "index.html": files.index_html,
        "report.json": files.report_json,
        "public-manifest.json": files.manifest_json,
    }
    entries = sorted(entry.name for entry in output.iterdir())
    if entries != sorted(expected):
        raise PublicationError("publication output does not contain exactly the public files")
    for name, payload in expected.items():
        entry = output / name
        if entry.is_symlink() or not entry.is_file():
            raise PublicationError("publication output contains a non-regular file")
        if entry.read_bytes() != payload:
            raise PublicationError("publication output bytes do not match publication")


def _validate_publication_files(files: PublicationFiles) -> None:
    try:
        report = PublicReport.model_validate_json(files.report_json, strict=True)
        manifest = PublicManifest.model_validate_json(files.manifest_json, strict=True)
    except ValidationError as error:
        raise PublicationError("publication bytes are not valid public objects") from error
    if canonical_json_bytes(report.model_dump(mode="json")) != files.report_json:
        raise PublicationError("publication report JSON is not canonical")
    if canonical_json_bytes(manifest.model_dump(mode="json")) != files.manifest_json:
        raise PublicationError("publication manifest JSON is not canonical")
    if manifest.source_timestamp != report.evidence_through:
        raise PublicationError("publication manifest timestamp does not match report")
    digests = {entry.path: entry.sha256 for entry in manifest.objects}
    if digests.get("index.html") != sha256(files.index_html).hexdigest():
        raise PublicationError("publication manifest does not hash index.html")
    if digests.get("report.json") != sha256(files.report_json).hexdigest():
        raise PublicationError("publication manifest does not hash report.json")
    _guard_disclosure(cast(JsonValue, report.model_dump(mode="json")))
    if files.index_html != render_public_report(report):
        raise PublicationError("publication HTML does not match its public report")


def write_publication(files: PublicationFiles, output: Path, *, force: bool = False) -> None:
    """Atomically install the three public files under ``output``."""
    _validate_publication_files(files)
    output = _checked_output(output)
    exists = output.exists()
    if exists and not output.is_dir():
        raise PublicationError("publication output exists and is not a directory")
    if exists and not force:
        raise PublicationError("publication output exists; pass --force to replace it")

    parent = output.parent
    stage = Path(tempfile.mkdtemp(prefix=f".{output.name}.", dir=parent))
    backup: Path | None = None
    backup_contains_original = False
    installed = False
    try:
        _write_files(stage, files)
        if exists:
            if _contains_symlink(output):
                raise PublicationError(
                    "publication output contains a symlink; refusing to replace it"
                )
            backup = Path(tempfile.mkdtemp(prefix=f".{output.name}.backup.", dir=parent))
            os.rmdir(backup)
            os.replace(output, backup)
            backup_contains_original = True
        try:
            if backup_contains_original:
                _fsync_directory(parent)
            os.replace(stage, output)
            installed = True
            _fsync_directory(parent)
            _verify_installed(output, files)
            _fsync_directory(output)
        except BaseException as error:
            if installed:
                shutil.rmtree(output, ignore_errors=True)
                installed = False
            if backup_contains_original and backup is not None:
                try:
                    os.replace(backup, output)
                except OSError as restore_error:
                    raise PublicationError(
                        f"publication rollback failed; the original directory remains at {backup}"
                    ) from restore_error
                backup = None
                backup_contains_original = False
                try:
                    _fsync_directory(parent)
                except OSError as sync_error:
                    raise PublicationError(
                        "publication restored the original directory but the durability sync failed"
                    ) from sync_error
            else:
                _fsync_directory(parent)
            raise error
        if backup is not None:
            shutil.rmtree(backup)
            backup = None
            _fsync_directory(parent)
    finally:
        if stage.exists():
            shutil.rmtree(stage, ignore_errors=True)
        if backup is not None and not backup_contains_original and backup.exists():
            shutil.rmtree(backup, ignore_errors=True)
