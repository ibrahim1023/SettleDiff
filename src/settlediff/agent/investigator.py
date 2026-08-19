"""Bounded evidence-selection agent with immutable deterministic authority."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from pydantic_ai import Agent, RunContext, UsageLimits
from pydantic_ai.models import Model

from settlediff.agent.grounding import (
    ExplanationGroundingError,
    fallback_explanation,
    validate_explanation,
)
from settlediff.agent.tools import EvidenceSummary, InvestigationDependencies
from settlediff.domain.models import InvestigationExplanation, MachineReport

INVESTIGATION_REQUEST_LIMIT = 4
INVESTIGATION_TOOL_CALL_LIMIT = 6
INVESTIGATION_INPUT_TOKEN_LIMIT = 8_000
INVESTIGATION_OUTPUT_TOKEN_LIMIT = 1_000


class InvestigationResult(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    explanation: InvestigationExplanation
    tool_calls: int = Field(ge=0, le=25)
    used_fallback: bool
    model_requests: int = Field(default=0, ge=0, le=10)
    input_tokens: int = Field(default=0, ge=0, le=100_000)
    output_tokens: int = Field(default=0, ge=0, le=10_000)
    model_cost: Decimal | None = Field(default=None, ge=Decimal("0"), le=Decimal("1000"))
    rejected_output: str | None = Field(default=None, min_length=1, max_length=2048)


class _ProviderExplanation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    summary: str
    evidence_used: tuple[str, ...]
    finding_ids: tuple[str, ...]
    deterministic_verdict: str
    recommended_next_step: str | None


class _ProviderInvestigationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    explanation: _ProviderExplanation
    tool_calls: int = Field(ge=0, le=25)
    used_fallback: bool


@dataclass(frozen=True)
class InvestigationState:
    report: MachineReport
    artifact_ids: frozenset[str]


def build_investigation_prompt(report: MachineReport) -> str:
    """Describe immutable findings without sending raw evidence or identifiers."""
    findings = "\n".join(
        f"- {finding.finding_id} [{finding.status.value}] {finding.message}"
        for finding in report.findings
    )
    return (
        f"Explain deterministic verdict={report.verdict.value} for run={report.run_id}.\n"
        f"Immutable findings:\n{findings}\n"
        "Use evidence tools only when needed and cite only returned artifact IDs."
    )


def build_investigator(
    model: Model,
) -> Agent[InvestigationDependencies, _ProviderInvestigationResult]:
    """Build a constant, evidence-only tool set; it has no payment or verdict tool."""
    agent = Agent(
        model,
        deps_type=InvestigationDependencies,
        output_type=_ProviderInvestigationResult,
        instructions=(
            "Select evidence needed to explain the already-computed report. "
            "You cannot alter findings or verdicts. Cite only tool artifact IDs."
        ),
        retries=0,
    )

    async def inspect_contract(ctx: RunContext[InvestigationDependencies]) -> EvidenceSummary:
        """Read the advertised contract summary."""
        return await ctx.deps.inspect_contract()

    async def get_schema(ctx: RunContext[InvestigationDependencies]) -> EvidenceSummary:
        """Read the advertised request-schema summary."""
        return await ctx.deps.get_schema()

    async def get_activity(ctx: RunContext[InvestigationDependencies]) -> EvidenceSummary:
        """Read the persisted Activity summary."""
        return await ctx.deps.get_activity()

    agent.tool(inspect_contract)
    agent.tool(get_schema)
    agent.tool(get_activity)

    return agent


def _rejected_output_diagnostic(
    output: InvestigationResult | _ProviderInvestigationResult,
) -> str:
    diagnostic = {
        "deterministic_verdict": str(output.explanation.deterministic_verdict),
        "evidence_count": len(output.explanation.evidence_used),
        "finding_count": len(output.explanation.finding_ids),
        "tool_calls": output.tool_calls,
    }
    return json.dumps(diagnostic, sort_keys=True, separators=(",", ":"))


async def investigate(
    state: InvestigationState,
    deps: InvestigationDependencies,
    model: Model,
    *,
    timeout_seconds: float = 20,
) -> InvestigationResult:
    """Run with fixed request/tool/token limits and a deterministic fallback."""
    agent = build_investigator(model)
    prompt = build_investigation_prompt(state.report)
    try:
        async with asyncio.timeout(timeout_seconds):
            response = await agent.run(
                prompt,
                deps=deps,
                usage_limits=UsageLimits(
                    request_limit=INVESTIGATION_REQUEST_LIMIT,
                    tool_calls_limit=INVESTIGATION_TOOL_CALL_LIMIT,
                    input_tokens_limit=INVESTIGATION_INPUT_TOKEN_LIMIT,
                    output_tokens_limit=INVESTIGATION_OUTPUT_TOKEN_LIMIT,
                ),
            )
    except (TimeoutError, RuntimeError):
        return InvestigationResult(
            explanation=fallback_explanation(state.report, set(state.artifact_ids)),
            tool_calls=0,
            used_fallback=True,
        )

    usage = response.usage
    try:
        output = InvestigationResult.model_validate_json(response.output.model_dump_json())
    except ValidationError:
        return InvestigationResult(
            explanation=fallback_explanation(state.report, set(state.artifact_ids)),
            tool_calls=usage.tool_calls,
            used_fallback=True,
            model_requests=usage.requests,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            model_cost=None,
            rejected_output=_rejected_output_diagnostic(response.output),
        )
    try:
        explanation = validate_explanation(
            output.explanation, state.report, set(state.artifact_ids)
        )
    except ExplanationGroundingError:
        return InvestigationResult(
            explanation=fallback_explanation(state.report, set(state.artifact_ids)),
            tool_calls=usage.tool_calls,
            used_fallback=True,
            model_requests=usage.requests,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            model_cost=None,
            rejected_output=_rejected_output_diagnostic(output),
        )
    return output.model_copy(
        update={
            "explanation": explanation,
            "used_fallback": False,
            "tool_calls": usage.tool_calls,
            "model_requests": usage.requests,
            "input_tokens": usage.input_tokens,
            "output_tokens": usage.output_tokens,
            "model_cost": None,
            "rejected_output": None,
        }
    )
