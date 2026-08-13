"""Validation and safe fallback for model-written investigation explanations."""

from __future__ import annotations

from settlediff.domain.models import InvestigationExplanation, MachineReport
from settlediff.domain.redaction import redact_embedded_identifiers


class ExplanationGroundingError(ValueError):
    """An explanation is inconsistent with immutable machine evidence."""


def validate_explanation(
    explanation: InvestigationExplanation,
    report: MachineReport,
    artifact_ids: set[str],
) -> InvestigationExplanation:
    """Accept only citations and verdicts already established by deterministic code."""
    if explanation.run_id != report.run_id:
        raise ExplanationGroundingError("explanation belongs to a different run")
    if explanation.deterministic_verdict is not report.verdict:
        raise ExplanationGroundingError("explanation contradicts the deterministic verdict")
    finding_ids = {finding.finding_id for finding in report.findings}
    if not set(explanation.finding_ids).issubset(finding_ids):
        raise ExplanationGroundingError("explanation cites an unknown finding")
    if not set(explanation.evidence_used).issubset(artifact_ids):
        raise ExplanationGroundingError("explanation cites an unknown artifact")
    if redact_embedded_identifiers(explanation.summary) != explanation.summary:
        raise ExplanationGroundingError("explanation contains an unredacted identifier")
    return explanation


def fallback_explanation(report: MachineReport, artifact_ids: set[str]) -> InvestigationExplanation:
    """Produce a factual explanation when a provider response is rejected or unavailable."""
    cited_findings = tuple(finding.finding_id for finding in report.findings)
    summary = f"Deterministic verification produced verdict {report.verdict.value}."
    return InvestigationExplanation(
        run_id=report.run_id,
        summary=summary,
        evidence_used=tuple(sorted(artifact_ids)),
        finding_ids=cited_findings,
        deterministic_verdict=report.verdict,
        recommended_next_step="Review the cited deterministic findings and evidence.",
    )
