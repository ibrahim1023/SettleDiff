from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from settlediff.agent.tools import (
    EvidenceSummary,
    InvestigationDependencies,
    build_investigation_dependencies,
)
from settlediff.application.replay import replay_fixture
from settlediff.domain.models import ArtifactType, EvidenceArtifact


async def evidence() -> EvidenceSummary:
    return EvidenceSummary(artifact_id="artifact:contract", summary="synthetic")


@pytest.mark.asyncio
async def test_dependencies_expose_only_evidence_summaries() -> None:
    deps = InvestigationDependencies(evidence, evidence, evidence)
    assert (await deps.get_activity()).artifact_id == "artifact:contract"


@pytest.mark.asyncio
async def test_live_dependencies_use_canonical_summaries_and_artifact_handles() -> None:
    report = replay_fixture(Path("fixtures/clean-success"))
    artifacts = tuple(
        EvidenceArtifact(
            artifact_id=f"artifact:{source}",
            artifact_type=artifact_type,
            source=source,
            collected_at=datetime(2026, 8, 18, tzinfo=UTC),
            redacted=True,
            data={},
        )
        for source, artifact_type in (
            ("perflo.vendor", ArtifactType.SERVICE_CONTRACT),
            ("perflo.schema", ArtifactType.CONTEXT_EVIDENCE),
            ("perflo.activity.agent", ArtifactType.ACTIVITY),
        )
    )

    deps = build_investigation_dependencies(report, artifacts)
    contract = await deps.inspect_contract()
    schema = await deps.get_schema()
    activity = await deps.get_activity()

    assert contract.artifact_id == "artifact:perflo.vendor"
    assert "synthetic-search" in contract.summary
    assert "example.invalid" not in contract.summary
    assert schema.artifact_id == "artifact:perflo.schema"
    assert "request schema fields" in schema.summary
    assert activity.artifact_id == "artifact:perflo.activity.agent"
    assert "confirmed" in activity.summary
    assert "syn_recipient" not in activity.summary


@pytest.mark.asyncio
async def test_schema4_activity_summary_labels_provider_assertion() -> None:
    report = replay_fixture(Path("fixtures/perflo-v8-provider-only-success"))

    activity = await build_investigation_dependencies(report, ()).get_activity()

    assert activity.summary.startswith("provider assertion: status=confirmed")
    assert (
        "; independent settlement=UNAVAILABLE (SETTLEMENT_PROFILE_UNAVAILABLE); "
        "comparison=NOT_COMPARABLE" in activity.summary
    )


@pytest.mark.asyncio
async def test_schema4_activity_summary_without_provider_activity() -> None:
    report = replay_fixture(Path("fixtures/x402-independent-confirmed"))

    activity = await build_investigation_dependencies(report, ()).get_activity()

    assert activity.summary == (
        "No provider Activity record is available; independent settlement=CONFIRMED "
        "(EXACT_TRANSFER_CONFIRMED); comparison=MATCH"
    )


@pytest.mark.asyncio
async def test_legacy_activity_summary_is_unchanged() -> None:
    report = replay_fixture(Path("fixtures/clean-success"))

    activity = await build_investigation_dependencies(report, ()).get_activity()

    assert activity.summary.startswith("status=confirmed")
    assert "provider assertion" not in activity.summary
    assert "independent settlement" not in activity.summary
