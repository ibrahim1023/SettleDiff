from __future__ import annotations

import base64
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from settlediff.application.assurance import (
    DOMAIN,
    issuer_fingerprint,
    sign_bundle,
    verify_assurance,
)
from settlediff.application.bundle import export_bundle, serialize_bundle
from settlediff.application.replay import replay_fixture
from settlediff.domain.integrity import canonical_json_bytes
from settlediff.domain.models import ArtifactType, EvidenceArtifact
from settlediff.storage.sqlite import SQLiteReportRepository


@pytest.fixture
def bundle_data(tmp_path: Path) -> bytes:
    report = replay_fixture(Path(__file__).parents[3] / "fixtures" / "clean-success")
    repository = SQLiteReportRepository(tmp_path / "issuer.sqlite")
    directory = Path(__file__).parents[3] / "fixtures" / "clean-success"
    kinds = {
        "contract.json": ArtifactType.SERVICE_CONTRACT,
        "execution.json": ArtifactType.EXECUTION,
        "receipt.json": ArtifactType.PAYMENT_RECEIPT,
        "activity.json": ArtifactType.ACTIVITY,
    }
    artifacts = tuple(
        EvidenceArtifact(
            artifact_id=f"{report.run_id}:{kind.value}",
            artifact_type=kind,
            source="fixture",
            collected_at=datetime(2026, 8, 12, tzinfo=UTC),
            redacted=False,
            data=json.loads((directory / name).read_text()),
        )
        for name, kind in kinds.items()
        if (directory / name).is_file()
    )
    repository.save(report, artifacts=artifacts)
    try:
        return serialize_bundle(export_bundle(repository, report.run_id))
    finally:
        repository.close()


def test_recipient_authenticates_without_issuer_database(bundle_data: bytes) -> None:
    key = Ed25519PrivateKey.generate()
    signature = sign_bundle(bundle_data, key)
    result = verify_assurance(bundle_data, signature, expected_public_key=key.public_key())
    assert result.status == "AUTHENTIC"
    assert result.machine_verdict is not None


def test_altered_bundle_cannot_be_authenticated(bundle_data: bytes) -> None:
    key = Ed25519PrivateKey.generate()
    signature = sign_bundle(bundle_data, key)
    result = verify_assurance(bundle_data + b" ", signature, expected_public_key=key.public_key())
    assert result.status == "CONTENT_MISMATCH"
    assert result.machine_verdict is None


@pytest.mark.parametrize("anchor", ["missing", "wrong-key", "wrong-fingerprint", "revoked"])
def test_untrusted_issuer(bundle_data: bytes, anchor: str) -> None:
    key = Ed25519PrivateKey.generate()
    signature = sign_bundle(bundle_data, key)
    if anchor == "missing":
        result = verify_assurance(bundle_data, signature)
    elif anchor == "wrong-key":
        result = verify_assurance(
            bundle_data, signature, expected_public_key=Ed25519PrivateKey.generate().public_key()
        )
    elif anchor == "wrong-fingerprint":
        result = verify_assurance(bundle_data, signature, expected_fingerprint="0" * 64)
    else:
        result = verify_assurance(
            bundle_data,
            signature,
            expected_public_key=key.public_key(),
            revoked_fingerprints=frozenset({issuer_fingerprint(key.public_key())}),
        )
    assert result.status == "UNTRUSTED_ISSUER"
    assert result.machine_verdict is None


def test_independently_pinned_fingerprint(bundle_data: bytes) -> None:
    key = Ed25519PrivateKey.generate()
    result = verify_assurance(
        bundle_data,
        sign_bundle(bundle_data, key),
        expected_fingerprint=issuer_fingerprint(key.public_key()),
    )
    assert result.status == "AUTHENTIC"


@pytest.mark.parametrize(
    "mutation",
    [
        "signature",
        "payload",
        "algorithm",
        "version",
        "key",
        "duplicate",
        "unsigned",
        "bad-base64",
        "unknown-field",
    ],
)
def test_envelope_attacks(bundle_data: bytes, mutation: str) -> None:
    key = Ed25519PrivateKey.generate()
    envelope: dict[str, Any] = json.loads(sign_bundle(bundle_data, key))
    expected = "UNSUPPORTED_FORMAT"
    if mutation == "signature":
        envelope["signature"] = base64.b64encode(bytes(64)).decode()
        expected = "INVALID_SIGNATURE"
    elif mutation == "payload":
        payload = json.loads(base64.b64decode(envelope["payload"]))
        payload["report_schema_version"] = 4
        envelope["payload"] = base64.b64encode(canonical_json_bytes(payload)).decode()
        expected = "INVALID_SIGNATURE"
    elif mutation == "algorithm":
        envelope["algorithm"] = "none"
    elif mutation == "version":
        envelope["schema_id"] = "assurance-signature/v2"
    elif mutation == "key":
        envelope["public_key"] = base64.b64encode(
            Ed25519PrivateKey.generate().public_key().public_bytes_raw()
        ).decode()
        expected = "UNTRUSTED_ISSUER"
    elif mutation == "bad-base64":
        envelope["signature"] = "!"
    elif mutation == "unknown-field":
        envelope["extra"] = "unbound"
    signature = canonical_json_bytes(envelope)
    if mutation == "duplicate":
        signature = signature[:-1] + b',"algorithm":"Ed25519"}'
    if mutation == "unsigned":
        signature = b"{}"
    result = verify_assurance(bundle_data, signature, expected_public_key=key.public_key())
    assert result.status == expected
    assert result.machine_verdict is None


@pytest.mark.parametrize(
    "mutation",
    [
        "path",
        "absolute",
        "unicode",
        "duplicate",
        "length",
        "digest",
        "role",
        "whitespace",
        "float",
        "nan",
        "unknown",
    ],
)
def test_signed_malformed_or_false_manifest(bundle_data: bytes, mutation: str) -> None:
    key = Ed25519PrivateKey.generate()
    envelope: dict[str, Any] = json.loads(sign_bundle(bundle_data, key))
    manifest: dict[str, Any] = json.loads(base64.b64decode(envelope["payload"]))
    expected = "UNSUPPORTED_FORMAT"
    entry = manifest["artifacts"][0]
    if mutation in {"path", "absolute", "unicode"}:
        entry["path"] = {
            "path": "../report.json",
            "absolute": "/report.json",
            "unicode": "rеport.json",
        }[mutation]
    elif mutation == "duplicate":
        manifest["artifacts"].append(entry)
    elif mutation == "length":
        entry["byte_length"] += 1
        expected = "CONTENT_MISMATCH"
    elif mutation == "digest":
        entry["sha256"] = "0" * 64
        expected = "CONTENT_MISMATCH"
    elif mutation == "role":
        entry["role"] = "machine_report"
        expected = "CONTENT_MISMATCH"
    elif mutation in {"float", "nan"}:
        entry["byte_length"] = 1.5 if mutation == "float" else float("nan")
    elif mutation == "unknown":
        manifest["extra"] = True
    payload = canonical_json_bytes(manifest)
    if mutation == "whitespace":
        payload += b" "
    envelope["payload"] = base64.b64encode(payload).decode()
    envelope["signature"] = base64.b64encode(key.sign(DOMAIN + payload)).decode()
    result = verify_assurance(
        bundle_data, canonical_json_bytes(envelope), expected_public_key=key.public_key()
    )
    assert result.status == expected


def test_changed_financial_verdict_fails_authentication(bundle_data: bytes) -> None:
    key = Ed25519PrivateKey.generate()
    assert b'"verdict":"VERIFIED"' in bundle_data
    changed = bundle_data.replace(b'"verdict":"VERIFIED"', b'"verdict":"PAID_FAILURE"')
    result = verify_assurance(
        changed, sign_bundle(bundle_data, key), expected_public_key=key.public_key()
    )
    assert result.status == "CONTENT_MISMATCH"
    assert result.machine_verdict is None
