"""Offline replay of versioned, sanitized evidence fixtures."""

from __future__ import annotations

import json
import math
import re
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from settlediff.domain.checks import run_checks
from settlediff.domain.matching import match_activity
from settlediff.domain.models import ArtifactType, EvidenceArtifact, MachineReport, PurchaseIntent
from settlediff.domain.normalize import (
    normalize_activity,
    normalize_contract,
    normalize_execution,
    normalize_receipt,
)
from settlediff.domain.verdict import derive_verdict

_EMAIL = re.compile(r"\b[^\s@]+@[^\s@]+\.[^\s@]+\b")
_HEX_IDENTIFIER = re.compile(r"\b0x[a-fA-F0-9]{40,64}\b")
_SECRET = re.compile(r"(?i)\b(?:bearer\s+|api[_-]?key\s*[=:]|sk-[a-z0-9_-]{16,})")
_SYNTHETIC = re.compile(r"\bsyn_[a-z0-9_]+\b")


class FixtureManifest(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    schema_version: int = Field(ge=1)
    scenario: str
    synthetic: bool
    expected_verdict: str
    artifacts: tuple[str, ...]


def replay_fixture(path: Path) -> MachineReport:
    """Replay one fixture without importing agent or adapter modules."""
    manifest = FixtureManifest.model_validate_json((path / "manifest.json").read_text())
    if not manifest.synthetic:
        raise ValueError("fixture manifest must explicitly declare synthetic evidence")
    for name in manifest.artifacts:
        if not (path / name).is_file():
            raise ValueError(f"fixture declares missing artifact {name}")
        assert_sanitized_fixture((path / name).read_text())

    intent_text = (path / "intent.json").read_text()
    assert_sanitized_fixture(intent_text)
    intent = PurchaseIntent.model_validate_json(intent_text)
    contract = normalize_contract(_artifact(path, "contract.json", ArtifactType.SERVICE_CONTRACT))
    execution = normalize_execution(_artifact(path, "execution.json", ArtifactType.EXECUTION))
    receipt = (
        normalize_receipt(_artifact(path, "receipt.json", ArtifactType.PAYMENT_RECEIPT))
        if "receipt.json" in manifest.artifacts
        else None
    )
    activity = normalize_activity(_artifact(path, "activity.json", ArtifactType.ACTIVITY))
    match = match_activity(execution, activity)
    findings = run_checks(intent, contract, execution, match, receipt=receipt)
    report = MachineReport(
        run_id=intent.run_id,
        intent=intent,
        contract=contract,
        execution=execution,
        ledger=match.matched,
        findings=findings,
        verdict=derive_verdict(findings),
        receipt=receipt,
    )
    if report.verdict.value != manifest.expected_verdict:
        raise ValueError(
            f"fixture expected {manifest.expected_verdict}, got {report.verdict.value}"
        )
    return report


def assert_sanitized_fixture(text: str) -> None:
    """Reject likely credentials, personal data, real wallet identifiers, and random payloads."""
    for pattern, label in (
        (_EMAIL, "email"),
        (_HEX_IDENTIFIER, "hex identifier"),
        (_SECRET, "secret"),
    ):
        if pattern.search(text):
            raise ValueError(f"fixture contains prohibited {label}")
    for token in re.findall(r"[A-Za-z0-9_-]{33,}", text):
        if _SYNTHETIC.fullmatch(token) is None and _entropy(token) > 4.3:
            raise ValueError("fixture contains unexplained high-entropy content")


def _artifact(path: Path, filename: str, artifact_type: ArtifactType) -> EvidenceArtifact:
    return EvidenceArtifact(
        artifact_id=f"{path.name}:{filename}",
        artifact_type=artifact_type,
        source="fixture",
        collected_at=datetime(2026, 8, 12, tzinfo=UTC),
        redacted=True,
        data=json.loads((path / filename).read_text()),
    )


def _entropy(value: str) -> float:
    frequencies = (value.count(char) / len(value) for char in set(value))
    return -sum(probability * math.log2(probability) for probability in frequencies)
