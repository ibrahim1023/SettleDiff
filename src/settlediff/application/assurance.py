"""Detached issuer authentication of private bundle bytes (ADR 0015)."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
from enum import StrEnum
from typing import Literal, Self

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from settlediff.application.bundle import (
    BundleError,
    EvidenceBundleV3,
    LogicalPath,
    load_bundle,
    serialize_bundle,
    verify_bundle,
)
from settlediff.domain.integrity import Sha256Digest, canonical_json_bytes
from settlediff.domain.models import Verdict

DOMAIN = b"SettleDiff assurance manifest v1\x00"
MAX_SIGNATURE_BYTES = 2 * 1024 * 1024


class AuthenticationStatus(StrEnum):
    AUTHENTIC = "AUTHENTIC"
    INVALID_SIGNATURE = "INVALID_SIGNATURE"
    CONTENT_MISMATCH = "CONTENT_MISMATCH"
    UNTRUSTED_ISSUER = "UNTRUSTED_ISSUER"
    UNSUPPORTED_FORMAT = "UNSUPPORTED_FORMAT"
    MISSING_ARTIFACT = "MISSING_ARTIFACT"


class AssuranceError(ValueError):
    """Signing input or issuer key is unsuitable; contains no private material."""


class AssuranceResult(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    status: AuthenticationStatus
    issuer_fingerprint: Sha256Digest | None = None
    machine_verdict: Verdict | None = None


class SignedArtifact(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    path: LogicalPath
    role: Literal["private_bundle", "machine_report", "logical_object"]
    byte_length: int = Field(ge=0, le=16 * 1024 * 1024)
    sha256: Sha256Digest


class AssuranceManifest(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    schema_id: Literal["assurance-signature/v1"] = "assurance-signature/v1"
    domain: Literal["SettleDiff assurance manifest v1"] = "SettleDiff assurance manifest v1"
    algorithm: Literal["Ed25519"] = "Ed25519"
    digest_algorithm: Literal["SHA-256"] = "SHA-256"
    issuer_fingerprint: Sha256Digest
    bundle_schema_version: Literal[3] = 3
    report_schema_version: int = Field(ge=1, le=4)
    artifacts: tuple[SignedArtifact, ...] = Field(min_length=1, max_length=4097)

    @model_validator(mode="after")
    def require_unique_sorted_paths(self) -> Self:
        paths = [entry.path for entry in self.artifacts]
        if paths != sorted(set(paths)):
            raise ValueError("assurance paths must be unique and sorted")
        return self


class AssuranceEnvelope(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    schema_id: Literal["assurance-signature/v1"] = "assurance-signature/v1"
    algorithm: Literal["Ed25519"] = "Ed25519"
    public_key: str = Field(max_length=44)
    payload: str = Field(max_length=MAX_SIGNATURE_BYTES)
    signature: str = Field(max_length=88)


def issuer_fingerprint(key: Ed25519PublicKey) -> str:
    return hashlib.sha256(key.public_bytes_raw()).hexdigest()


def _entry(
    path: str, role: Literal["private_bundle", "machine_report", "logical_object"], data: bytes
) -> SignedArtifact:
    return SignedArtifact(
        path=path, role=role, byte_length=len(data), sha256=hashlib.sha256(data).hexdigest()
    )


def _manifest(data: bytes, fingerprint: str) -> tuple[AssuranceManifest, Verdict]:
    bundle = load_bundle(data)
    if not isinstance(bundle, EvidenceBundleV3) or serialize_bundle(bundle) != data:
        raise AssuranceError("issuer signing requires canonical schema-3 bundle bytes")
    report = verify_bundle(bundle)
    entries = [_entry("bundle.json", "private_bundle", data)]
    entries.extend(
        _entry(
            path,
            "machine_report" if path == "report.json" else "logical_object",
            canonical_json_bytes(value),
        )
        for path, value in bundle.objects.items()
    )
    return AssuranceManifest(
        issuer_fingerprint=fingerprint,
        report_schema_version=report.schema_version,
        artifacts=tuple(sorted(entries, key=lambda entry: entry.path)),
    ), report.verdict


def sign_bundle(data: bytes, key: Ed25519PrivateKey) -> bytes:
    """Authenticate already-exported bytes; never generate a key or change a report."""
    try:
        manifest, _verdict = _manifest(data, issuer_fingerprint(key.public_key()))
    except (BundleError, ValidationError, RecursionError) as error:
        raise AssuranceError("bundle cannot be signed") from error
    payload = canonical_json_bytes(manifest.model_dump(mode="json"))
    encoded_payload = base64.b64encode(payload).decode("ascii")
    if len(encoded_payload) > MAX_SIGNATURE_BYTES:
        raise AssuranceError("assurance envelope exceeds size limit")
    envelope = AssuranceEnvelope(
        public_key=base64.b64encode(key.public_key().public_bytes_raw()).decode("ascii"),
        payload=encoded_payload,
        signature=base64.b64encode(key.sign(DOMAIN + payload)).decode("ascii"),
    )
    encoded = canonical_json_bytes(envelope.model_dump(mode="json"))
    if len(encoded) > MAX_SIGNATURE_BYTES:
        raise AssuranceError("assurance envelope exceeds size limit")
    return encoded


def _pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    if len({key for key, _value in pairs}) != len(pairs):
        raise ValueError("duplicate assurance field")
    return dict(pairs)


def _no_number(_value: str) -> None:
    raise ValueError("assurance format admits only integer numbers")


def _strict_json(data: bytes) -> bytes:
    if len(data) > MAX_SIGNATURE_BYTES:
        raise ValueError("assurance format exceeds size limit")
    value: object = json.loads(
        data.decode("utf-8"),
        object_pairs_hook=_pairs,
        parse_float=_no_number,
        parse_constant=_no_number,
    )
    return canonical_json_bytes(value)


def _decode(value: str, length: int | None = None) -> bytes:
    raw = base64.b64decode(value, validate=True)
    if base64.b64encode(raw).decode("ascii") != value or (
        length is not None and len(raw) != length
    ):
        raise ValueError("invalid assurance base64")
    return raw


def verify_assurance(
    data: bytes,
    signature: bytes,
    *,
    expected_public_key: Ed25519PublicKey | None = None,
    expected_fingerprint: str | None = None,
    revoked_fingerprints: frozenset[str] = frozenset(),
) -> AssuranceResult:
    """Offline issuer authentication; never falls back to integrity-only verification."""
    if expected_public_key is None and expected_fingerprint is None:
        return AssuranceResult(status=AuthenticationStatus.UNTRUSTED_ISSUER)
    try:
        envelope = AssuranceEnvelope.model_validate_json(_strict_json(signature), strict=True)
        public_key = Ed25519PublicKey.from_public_bytes(_decode(envelope.public_key, 32))
        payload = _decode(envelope.payload)
        manifest = AssuranceManifest.model_validate_json(_strict_json(payload), strict=True)
        if canonical_json_bytes(manifest.model_dump(mode="json")) != payload:
            raise ValueError("noncanonical assurance manifest")
        raw_signature = _decode(envelope.signature, 64)
    except (ValueError, UnicodeError, binascii.Error, RecursionError):
        return AssuranceResult(status=AuthenticationStatus.UNSUPPORTED_FORMAT)
    fingerprint = issuer_fingerprint(public_key)
    if (
        fingerprint != manifest.issuer_fingerprint
        or fingerprint in revoked_fingerprints
        or (
            expected_public_key is not None
            and fingerprint != issuer_fingerprint(expected_public_key)
        )
        or (expected_fingerprint is not None and fingerprint != expected_fingerprint)
    ):
        return AssuranceResult(status=AuthenticationStatus.UNTRUSTED_ISSUER)
    try:
        public_key.verify(raw_signature, DOMAIN + payload)
    except InvalidSignature:
        return AssuranceResult(status=AuthenticationStatus.INVALID_SIGNATURE)
    try:
        expected, verdict = _manifest(data, fingerprint)
    except (AssuranceError, BundleError, ValidationError, ValueError, RecursionError):
        return AssuranceResult(status=AuthenticationStatus.CONTENT_MISMATCH)
    if expected != manifest:
        return AssuranceResult(status=AuthenticationStatus.CONTENT_MISMATCH)
    return AssuranceResult(
        status=AuthenticationStatus.AUTHENTIC,
        issuer_fingerprint=fingerprint,
        machine_verdict=verdict,
    )
