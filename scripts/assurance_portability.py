#!/usr/bin/env python3
"""Synthetic issuer/recipient check for the installed SettleDiff distribution."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from settlediff.application.assurance import issuer_fingerprint, sign_bundle
from settlediff.application.bundle import (
    export_bundle,
    load_bundle,
    serialize_bundle,
    verify_bundle,
)
from settlediff.application.replay import replay_fixture
from settlediff.domain.integrity import canonical_json_bytes, sha256_digest
from settlediff.domain.models import ArtifactType, EvidenceArtifact
from settlediff.storage.sqlite import SQLiteReportRepository

_FILES = {"bundle.json", "bundle.assurance.json", "legacy.bundle.json"}
_OFFLINE_CLI = """
import sys
def offline(event, args):
    if event in {'socket.connect', 'socket.getaddrinfo'}:
        raise RuntimeError('recipient verification attempted network access')
sys.addaudithook(offline)
from settlediff.cli import app
app(args=sys.argv[1:])
"""


def issue_fixture(output: Path) -> str:
    """Generate only synthetic evidence and an ephemeral, never serialized issuer key."""
    fixture = Path(__file__).resolve().parents[1] / "fixtures" / "x402-independent-confirmed"
    report = replay_fixture(fixture)
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
            data=json.loads((fixture / name).read_text()),
        )
        for name, kind in kinds.items()
        if (fixture / name).is_file()
    )
    with TemporaryDirectory(prefix="settlediff-synthetic-issuer-") as temporary:
        repository = SQLiteReportRepository(Path(temporary) / "issuer.sqlite")
        try:
            repository.save(report, artifacts=artifacts)
            bundle = export_bundle(repository, report.run_id)
        finally:
            repository.close()
    data = serialize_bundle(bundle)
    legacy: dict[str, object] = {
        "schema_version": 2,
        "run_id": bundle.run_id,
        "report": bundle.objects["report.json"],
        "explanation": None,
        "events": bundle.objects["run-events.json"],
        "artifacts": [
            value for path, value in bundle.objects.items() if path.startswith("artifacts/")
        ],
        "compatibility": bundle.compatibility.model_dump(mode="json"),
    }
    legacy["integrity"] = sha256_digest(legacy)
    legacy_data = canonical_json_bytes(legacy)
    verify_bundle(load_bundle(legacy_data))

    key = Ed25519PrivateKey.generate()
    signature = sign_bundle(data, key)
    output.mkdir(mode=0o700)
    (output / "bundle.json").write_bytes(data)
    (output / "bundle.assurance.json").write_bytes(signature)
    (output / "legacy.bundle.json").write_bytes(legacy_data)
    return issuer_fingerprint(key.public_key())


def _recipient_cli(directory: Path, arguments: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-I", "-c", _OFFLINE_CLI, *arguments],
        cwd=directory,
        env={"PATH": os.defpath, "NO_COLOR": "1"},
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def verify_recipient(delivery: Path, fingerprint: str) -> dict[str, str]:
    """Exercise the real offline CLI using only delivered synthetic files and a pin."""
    if {path.name for path in delivery.iterdir()} != _FILES:
        raise ValueError("synthetic delivery must contain exactly the three expected files")
    delivery = delivery.resolve()
    signature = delivery / "bundle.assurance.json"
    checks: dict[str, str] = {}

    def authenticate(
        name: str,
        bundle: Path,
        expected: str,
        *,
        pin: str = fingerprint,
        envelope: Path = signature,
        extra: tuple[str, ...] = (),
    ) -> None:
        result = _recipient_cli(
            working,
            [
                "verify-assurance",
                str(bundle),
                "--signature",
                str(envelope),
                "--issuer-fingerprint",
                pin,
                *extra,
            ],
        )
        payload = json.loads(result.stdout)
        verdict = "VERIFIED" if expected == "AUTHENTIC" else None
        if (
            result.returncode != (0 if expected == "AUTHENTIC" else 2)
            or payload["status"] != expected
            or payload["machine_verdict"] != verdict
        ):
            raise RuntimeError(f"recipient check failed: {name}")
        checks[name] = expected

    with TemporaryDirectory(prefix="settlediff-synthetic-recipient-") as temporary:
        working = Path(temporary).resolve()
        bundle = delivery / "bundle.json"
        authenticate("authentic", bundle, "AUTHENTIC")
        changed = working / "tampered.json"
        changed.write_bytes(bundle.read_bytes() + b" ")
        authenticate("tampered", changed, "CONTENT_MISMATCH")
        authenticate("wrong_issuer", bundle, "UNTRUSTED_ISSUER", pin="0" * 64)
        authenticate(
            "revoked_issuer", bundle, "UNTRUSTED_ISSUER", extra=("--revoked-issuer", fingerprint)
        )
        authenticate(
            "missing_signature",
            bundle,
            "MISSING_ARTIFACT",
            envelope=working / "missing.assurance.json",
        )
        legacy = delivery / "legacy.bundle.json"
        result = _recipient_cli(working, ["verify-bundle", str(legacy)])
        if result.returncode != 0 or "authenticity is not established" not in result.stdout:
            raise RuntimeError("recipient check failed: legacy integrity-only verification")
        checks["legacy_integrity"] = "INTEGRITY_ONLY"
        authenticate("legacy_authentication", legacy, "CONTENT_MISMATCH")
    return checks


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    issuer = commands.add_parser("issue", help="Create a new synthetic delivery directory")
    issuer.add_argument("directory", type=Path)
    recipient = commands.add_parser("verify", help="Check a delivered synthetic fixture offline")
    recipient.add_argument("directory", type=Path)
    recipient.add_argument("--issuer-fingerprint", required=True)
    args = parser.parse_args()
    if args.command == "issue":
        print(issue_fixture(args.directory))
    else:
        print(json.dumps(verify_recipient(args.directory, args.issuer_fingerprint), sort_keys=True))


if __name__ == "__main__":
    main()
