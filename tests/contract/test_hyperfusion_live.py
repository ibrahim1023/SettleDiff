"""Opt-in compatibility probe for Hyperfusion's Chat Completions endpoint."""

from __future__ import annotations

import os

import pytest
from pydantic import BaseModel
from pydantic_ai import Agent

from settlediff.agent.model import build_hyperfusion_model
from settlediff.config import Settings

pytestmark = pytest.mark.live_hyperfusion


class ContractOutput(BaseModel):
    answer: str


@pytest.mark.asyncio
async def test_hyperfusion_supports_structured_output_and_tool_continuation() -> None:
    if os.getenv("SETTLEDIFF_LIVE_HYPERFUSION") != "1":
        pytest.skip("set SETTLEDIFF_LIVE_HYPERFUSION=1 to run the live compatibility probe")

    tool_calls: list[str] = []
    agent = Agent(
        build_hyperfusion_model(Settings().require_hyperfusion()),
        output_type=ContractOutput,
        instructions=(
            "Call the echo_evidence tool exactly once with the word 'ping'. "
            "After the tool result, return its value unchanged in the answer field."
        ),
        retries=0,
    )

    async def echo_evidence(value: str) -> str:
        tool_calls.append(value)
        return f"evidence:{value}"

    agent.tool_plain(echo_evidence)

    result = await agent.run("Perform the compatibility check.")

    assert tool_calls == ["ping"]
    assert result.output.answer == "evidence:ping"
    usage = result.usage()
    assert usage.requests >= 2
    assert usage.tool_calls == 1
