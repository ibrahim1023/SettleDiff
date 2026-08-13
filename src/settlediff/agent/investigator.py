"""Bounded evidence-selection agent with immutable deterministic authority."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field
from pydantic_ai import Agent, RunContext, UsageLimits
from pydantic_ai.models import Model

from settlediff.agent.grounding import (
    ExplanationGroundingError,
    fallback_explanation,
    validate_explanation,
)
from settlediff.agent.tools import EvidenceSummary, InvestigationDependencies
from settlediff.domain.models import InvestigationExplanation, MachineReport


class InvestigationResult(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    explanation: InvestigationExplanation
    tool_calls: int = Field(ge=0)
    used_fallback: bool


@dataclass(frozen=True)
class InvestigationState:
    report: MachineReport
    artifact_ids: frozenset[str]


def build_investigator(model: Model) -> Agent[InvestigationDependencies, InvestigationResult]:
    """Build a constant, evidence-only tool set; it has no payment or verdict tool."""
    agent = Agent(
        model,
        deps_type=InvestigationDependencies,
        output_type=InvestigationResult,
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


async def investigate(
    state: InvestigationState,
    deps: InvestigationDependencies,
    model: Model,
    *,
    timeout_seconds: float = 20,
) -> InvestigationResult:
    """Run with fixed request/tool/token limits and a deterministic fallback."""
    agent = build_investigator(model)
    prompt = (
        f"Explain deterministic verdict {state.report.verdict.value} for run {state.report.run_id}."
    )
    try:
        async with asyncio.timeout(timeout_seconds):
            response = await agent.run(
                prompt,
                deps=deps,
                usage_limits=UsageLimits(
                    request_limit=4,
                    tool_calls_limit=6,
                    input_tokens_limit=8_000,
                    output_tokens_limit=1_000,
                ),
            )
        explanation = validate_explanation(
            response.output.explanation, state.report, set(state.artifact_ids)
        )
        return response.output.model_copy(
            update={"explanation": explanation, "used_fallback": False}
        )
    except (ExplanationGroundingError, TimeoutError, RuntimeError):
        return InvestigationResult(
            explanation=fallback_explanation(state.report, set(state.artifact_ids)),
            tool_calls=0,
            used_fallback=True,
        )
