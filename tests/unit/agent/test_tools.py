from __future__ import annotations

import pytest

from settlediff.agent.tools import EvidenceSummary, InvestigationDependencies


async def evidence() -> EvidenceSummary:
    return EvidenceSummary(artifact_id="artifact:contract", summary="synthetic")


@pytest.mark.asyncio
async def test_dependencies_expose_only_evidence_summaries() -> None:
    deps = InvestigationDependencies(evidence, evidence, evidence)
    assert (await deps.get_activity()).artifact_id == "artifact:contract"
