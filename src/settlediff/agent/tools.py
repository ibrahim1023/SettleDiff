"""Small evidence-only tool surface for the investigation model."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict

from settlediff.domain.models import EvidenceArtifact, MachineReport
from settlediff.domain.redaction import redact_embedded_identifiers


class EvidenceSummary(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    artifact_id: str
    summary: str


@dataclass(frozen=True)
class InvestigationDependencies:
    inspect_contract: Callable[[], Awaitable[EvidenceSummary]]
    get_schema: Callable[[], Awaitable[EvidenceSummary]]
    get_activity: Callable[[], Awaitable[EvidenceSummary]]


def build_investigation_dependencies(
    report: MachineReport, artifacts: tuple[EvidenceArtifact, ...]
) -> InvestigationDependencies:
    """Build bounded canonical summaries backed by stable artifact handles."""
    by_source = {artifact.source: artifact for artifact in artifacts}

    async def inspect_contract() -> EvidenceSummary:
        artifact = by_source.get("perflo.check")
        contract = report.contract
        if contract is None:
            summary = "No normalized service contract is available."
        else:
            price = (
                f"{contract.price.amount} {contract.price.unit}"
                if contract.price is not None
                else "unknown"
            )
            summary = (
                f"vendor={contract.vendor_slug or 'unknown'}; price={price}; "
                f"asset={contract.asset or 'unknown'}; "
                f"protocol={contract.protocol or 'unknown'}; chain={contract.chain or 'unknown'}"
            )
        return EvidenceSummary(
            artifact_id=artifact.artifact_id if artifact is not None else "report:contract",
            summary=summary,
        )

    async def get_schema() -> EvidenceSummary:
        artifact = by_source.get("perflo.schema")
        schema = report.contract.request_schema if report.contract is not None else None
        schema = schema or {}
        fields = tuple(sorted(redact_embedded_identifiers(key) for key in schema))[:20]
        summary = f"request schema fields: {', '.join(fields) if fields else 'none'}"
        return EvidenceSummary(
            artifact_id=artifact.artifact_id if artifact is not None else "report:schema",
            summary=summary,
        )

    async def get_activity() -> EvidenceSummary:
        artifact = by_source.get("perflo.activity")
        ledger = report.ledger
        if ledger is None:
            summary = "No deterministically matched Activity record is available."
        else:
            amount = (
                f"{ledger.amount.amount} {ledger.amount.unit}"
                if ledger.amount is not None
                else "unknown"
            )
            summary = (
                f"status={ledger.status.value}; amount={amount}; "
                f"asset={ledger.asset or 'unknown'}; "
                f"protocol={ledger.protocol or 'unknown'}; chain={ledger.chain or 'unknown'}"
            )
        return EvidenceSummary(
            artifact_id=artifact.artifact_id if artifact is not None else "report:activity",
            summary=summary,
        )

    return InvestigationDependencies(inspect_contract, get_schema, get_activity)
