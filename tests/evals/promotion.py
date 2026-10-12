"""Promote a reviewed synthetic reconstruction without copying the live payload."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from pydantic import JsonValue

from settlediff.application.bundle import load_bundle, verify_bundle
from settlediff.application.replay import FixtureManifest, assert_sanitized_fixture, replay_fixture
from settlediff.domain.redaction import IDENTIFIER_KEYS, normalize_key, redact_value
from tests.evals.dataset import EvalCase

_ARTIFACT_FILES = frozenset(
    {
        "contract.json",
        "execution.json",
        "activity.json",
        "receipt.json",
        "independent_settlement.json",
        "context-evidence.json",
    }
)
_SYNTHETIC_IDENTIFIER_KEYS = IDENTIFIER_KEYS | {
    "ledgerid",
    "payer",
    "transactionreference",
    "runid",
}


def _require_synthetic_identifiers(value: JsonValue) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if (
                normalize_key(key) in _SYNTHETIC_IDENTIFIER_KEYS
                and child is not None
                and (not isinstance(child, str) or re.fullmatch(r"syn_[a-z0-9_]+", child) is None)
            ):
                raise ValueError("fixture identifier fields must use synthetic identifiers")
            _require_synthetic_identifiers(child)
    elif isinstance(value, list):
        for child in value:
            _require_synthetic_identifiers(child)


def promote_case(
    source_bundle: Path,
    fixture: Path,
    case: EvalCase,
    output: Path,
    *,
    reviewed: bool,
) -> Path:
    if not reviewed:
        raise ValueError("promotion requires reviewed evidence and explicit expected behavior")
    destination = output / case.case_id
    if destination.exists() or destination.is_symlink():
        raise ValueError("promotion destination already exists")
    bundle_data = source_bundle.read_bytes()
    verify_bundle(load_bundle(bundle_data))
    manifest = FixtureManifest.model_validate_json((fixture / "manifest.json").read_text())
    if any(name not in _ARTIFACT_FILES for name in manifest.artifacts):
        raise ValueError("fixture declares an unsupported artifact filename")
    filenames = {"manifest.json", "intent.json", *manifest.artifacts}
    payloads: dict[str, str] = {}
    for name in sorted(filenames):
        path = fixture / name
        if path.is_symlink():
            raise ValueError("fixture files must not be symlinks")
        text = path.read_text()
        assert_sanitized_fixture(text)
        value = cast(JsonValue, json.loads(text))
        _require_synthetic_identifiers(value)
        if redact_value(value, mask_keyed_identifiers=False) != value:
            raise ValueError("fixture contains secret fields or unredacted identifiers")
        payloads[name] = text
    report = replay_fixture(fixture)
    if report.verdict is not case.expected_verdict:
        raise ValueError("reviewed fixture does not establish the expected verdict")
    checks = {finding.check_id: finding.status for finding in report.findings}
    if any(checks.get(key) is not value for key, value in case.expected_checks.items()):
        raise ValueError("reviewed fixture does not establish the expected findings")
    case_text = case.model_dump_json(indent=2)
    assert_sanitized_fixture(case_text)
    output.mkdir(parents=True, exist_ok=True)
    destination.mkdir()
    copied_fixture = destination / case.fixture
    copied_fixture.mkdir()
    for name, text in payloads.items():
        (copied_fixture / name).write_text(text)
    (destination / "case.json").write_text(case_text + "\n")
    provenance = {
        "schema_version": 1,
        "source_bundle_digest": hashlib.sha256(bundle_data).hexdigest(),
        "synthetic_reconstruction": True,
        "reviewed": True,
        "created_at": datetime.now(UTC).isoformat(),
    }
    (destination / "provenance.json").write_text(json.dumps(provenance, indent=2) + "\n")
    return destination
