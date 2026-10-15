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


@pytest.fixture(params=["clean-success", "x402-independent-confirmed"])
def bundle_data(tmp_path: Path, request: pytest.FixtureRequest) -> bytes:
    scenario = str(request.param)
    report = replay_fixture(Path(__file__).parents[3] / "fixtures" / scenario)
    repository = SQLiteReportRepository(tmp_path / "issuer.sqlite")
    directory = Path(__file__).parents[3] / "fixtures" / scenario
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
        payload["report_schema_version"] = 1 if payload["report_schema_version"] == 4 else 4
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


@pytest.mark.parametrize("mutation", ["change", "remove", "add"])
def test_recomputed_unsigned_checksums_do_not_restore_authentication(
    bundle_data: bytes, mutation: str
) -> None:
    from hashlib import sha256

    key = Ed25519PrivateKey.generate()
    payload: dict[str, Any] = json.loads(bundle_data)
    if mutation == "change":
        payload["compatibility"]["settlediff_version"] = "0.1.1"
    elif mutation == "remove":
        payload["objects"].pop("timeline.json")
    else:
        payload["objects"]["injected.json"] = {"claim": "synthetic"}
    payload["manifest"] = [
        {"path": path, "sha256": sha256(canonical_json_bytes(value)).hexdigest()}
        for path, value in sorted(payload["objects"].items())
    ]
    del payload["bundle_sha256"]
    payload["bundle_sha256"] = sha256(canonical_json_bytes(payload)).hexdigest()
    changed = canonical_json_bytes(payload)
    if mutation == "change":
        from settlediff.application.bundle import load_bundle, verify_bundle

        verify_bundle(load_bundle(changed))  # Checksum-only verification can be recomputed.
    assert (
        verify_assurance(
            changed, sign_bundle(bundle_data, key), expected_public_key=key.public_key()
        ).status
        == "CONTENT_MISMATCH"
    )


def test_legacy_bundle_is_integrity_only(bundle_data: bytes) -> None:
    from hashlib import sha256

    from settlediff.application.assurance import AssuranceError
    from settlediff.application.bundle import load_bundle, verify_bundle

    modern: dict[str, Any] = json.loads(bundle_data)
    legacy = {
        "schema_version": 2,
        "run_id": modern["run_id"],
        "report": modern["objects"]["report.json"],
        "explanation": None,
        "events": modern["objects"]["run-events.json"],
        "artifacts": [
            value for path, value in modern["objects"].items() if path.startswith("artifacts/")
        ],
        "compatibility": modern["compatibility"],
    }
    legacy["integrity"] = sha256(canonical_json_bytes(legacy)).hexdigest()
    data = canonical_json_bytes(legacy)
    verify_bundle(load_bundle(data))
    with pytest.raises(AssuranceError):
        sign_bundle(data, Ed25519PrivateKey.generate())


def test_cli_signing_and_fresh_process_verification(bundle_data: bytes, tmp_path: Path) -> None:
    import os
    import subprocess
    import sys

    from typer.testing import CliRunner

    from settlediff.cli import app

    runner = CliRunner()
    private, public = tmp_path / "private.pem", tmp_path / "public.pem"
    bundle, signature = tmp_path / "bundle.json", tmp_path / "assurance.json"
    bundle.write_bytes(bundle_data)
    created = runner.invoke(
        app,
        ["issuer-keygen", "--private-key", str(private), "--public-key", str(public)],
        input="synthetic-only\nsynthetic-only\n",
    )
    assert created.exit_code == 0, created.output
    signed = runner.invoke(
        app,
        ["sign-bundle", str(bundle), "--issuer-key", str(private), "--output", str(signature)],
        input="synthetic-only\n",
    )
    assert signed.exit_code == 0, signed.output
    assert "synthetic-only" not in created.output + signed.output
    assert private.read_bytes() not in signature.read_bytes()
    assert "issuer.sqlite" not in signature.read_text()
    private.unlink()
    (tmp_path / "issuer.sqlite").unlink()
    # A new interpreter, different cwd, no database/key, and network disabled.
    code = """import socket, sys
socket.socket.connect = lambda *a, **kw: (_ for _ in ()).throw(AssertionError("network"))
socket.socket.connect_ex = socket.socket.connect
from settlediff.cli import app
app(args=sys.argv[1:])
"""
    environment = {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONPATH": str(Path(__file__).parents[3] / "src"),
    }
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            code,
            "verify-assurance",
            str(bundle),
            "--signature",
            str(signature),
            "--issuer-public-key",
            str(public),
        ],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["status"] == "AUTHENTIC"


def test_independent_node_verifies_wire_format(bundle_data: bytes, tmp_path: Path) -> None:
    import shutil
    import subprocess

    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

    node = shutil.which("node")
    if node is None:
        pytest.skip("Node standard crypto is unavailable")
    key = Ed25519PrivateKey.generate()
    (tmp_path / "bundle.json").write_bytes(bundle_data)
    (tmp_path / "assurance.json").write_bytes(sign_bundle(bundle_data, key))
    (tmp_path / "trusted.pem").write_bytes(
        key.public_key().public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo)
    )
    program = r"""
const fs = require('node:fs'), crypto = require('node:crypto');
const assert = require('node:assert/strict');
const envelope = JSON.parse(fs.readFileSync('assurance.json'));
const key = crypto.createPublicKey(fs.readFileSync('trusted.pem'));
const payload = Buffer.from(envelope.payload, 'base64');
const message = Buffer.concat([Buffer.from('SettleDiff assurance manifest v1\0'), payload]);
assert(crypto.verify(null, message,
                     key, Buffer.from(envelope.signature, 'base64')));
const hash = x => crypto.createHash('sha256').update(x).digest('hex');
const manifest = JSON.parse(payload);
const canonical = x => Array.isArray(x) ? x.map(canonical) : x && typeof x === 'object'
    ? Object.fromEntries(Object.keys(x).sort().map(k => [k, canonical(x[k])])) : x;
assert.equal(JSON.stringify(canonical(manifest)), payload.toString());
const raw = fs.readFileSync('bundle.json'), bundle = JSON.parse(raw);
for (const entry of manifest.artifacts) {
  const bytes = entry.role === 'private_bundle' ? raw
    : Buffer.from(JSON.stringify(canonical(bundle.objects[entry.path])));
  assert.equal(bytes.length, entry.byte_length);
  assert.equal(hash(bytes), entry.sha256);
}
const spki = key.export({type:'spki', format:'der'});
assert.equal(hash(spki.subarray(-32)), manifest.issuer_fingerprint);
console.log('AUTHENTIC');
"""
    result = subprocess.run(
        [node, "-e", program], cwd=tmp_path, capture_output=True, text=True, timeout=10, check=False
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "AUTHENTIC"


@pytest.mark.parametrize(
    "raw",
    [
        b'{"schema_id":NaN}',
        b'{"value":1.0}',
        b"\xff",
        b'{"value":1,"value":2}',
        b"[" * 2000,
        b" " * (2 * 1024 * 1024 + 1),
    ],
)
def test_hostile_envelope_is_bounded_and_rejected(bundle_data: bytes, raw: bytes) -> None:
    assert (
        verify_assurance(
            bundle_data, raw, expected_public_key=Ed25519PrivateKey.generate().public_key()
        ).status
        == "UNSUPPORTED_FORMAT"
    )


def test_signature_from_another_issuer_is_invalid(bundle_data: bytes) -> None:
    key = Ed25519PrivateKey.generate()
    envelope: dict[str, Any] = json.loads(sign_bundle(bundle_data, key))
    other: dict[str, Any] = json.loads(sign_bundle(bundle_data, Ed25519PrivateKey.generate()))
    envelope["signature"] = other["signature"]
    assert (
        verify_assurance(
            bundle_data, canonical_json_bytes(envelope), expected_public_key=key.public_key()
        ).status
        == "INVALID_SIGNATURE"
    )


def test_signing_never_emits_an_envelope_over_the_verifier_limit(
    bundle_data: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    from settlediff.application import assurance
    from settlediff.application.assurance import AssuranceError

    monkeypatch.setattr(assurance, "MAX_SIGNATURE_BYTES", 1024)
    with pytest.raises(AssuranceError):
        sign_bundle(bundle_data, Ed25519PrivateKey.generate())


@pytest.mark.parametrize("field", ["schema_id", "algorithm"])
def test_envelope_requires_explicit_format_discriminators(bundle_data: bytes, field: str) -> None:
    key = Ed25519PrivateKey.generate()
    envelope: dict[str, Any] = json.loads(sign_bundle(bundle_data, key))
    del envelope[field]
    result = verify_assurance(
        bundle_data, canonical_json_bytes(envelope), expected_public_key=key.public_key()
    )
    assert result.status == "UNSUPPORTED_FORMAT"
    assert result.machine_verdict is None


def test_signing_rejects_unpaired_unicode_with_boundary_error() -> None:
    from settlediff.application.assurance import AssuranceError

    malformed = b'{"schema_version":3,"run_id":"\\ud800"}'
    with pytest.raises(AssuranceError, match="bundle cannot be signed"):
        sign_bundle(malformed, Ed25519PrivateKey.generate())
