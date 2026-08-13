"""Opt-in compatibility probe for Hyperfusion's Chat Completions endpoint."""

from __future__ import annotations

import os
from uuid import uuid4

import pytest
from pydantic import BaseModel
from pydantic_ai import Agent, models

from settlediff.agent.model import build_hyperfusion_model
from settlediff.config import Settings

pytestmark = pytest.mark.live_hyperfusion


class ContractOutput(BaseModel):
    answer: str


@pytest.mark.asyncio
async def test_hyperfusion_supports_structured_output_and_tool_continuation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if os.getenv("SETTLEDIFF_LIVE_HYPERFUSION") != "1":
        pytest.skip("set SETTLEDIFF_LIVE_HYPERFUSION=1 to run the live compatibility probe")

    monkeypatch.setattr(models, "ALLOW_MODEL_REQUESTS", True)
    structured_agent = Agent(
        build_hyperfusion_model(Settings().require_hyperfusion()),
        output_type=ContractOutput,
        instructions="Return the exact word 'structured-ok' in the answer field.",
        retries=0,
    )
    structured_result = await structured_agent.run("Perform the structured-output check.")

    assert structured_result.output.answer == "structured-ok"
    assert structured_result.usage.requests >= 1

    tool_calls: list[str] = []
    evidence_token = uuid4().hex
    agent = Agent(
        build_hyperfusion_model(Settings().require_hyperfusion()),
        output_type=str,
        instructions=(
            "Call the echo_evidence tool exactly once with the word 'ping'. "
            "After the tool result, reply with its complete returned value, including its "
            "'evidence:' prefix and opaque trailing token. Do not reply with only the argument."
        ),
        retries=0,
    )

    async def echo_evidence(value: str) -> str:
        tool_calls.append(value)
        return f"evidence:{value}:{evidence_token}"

    agent.tool_plain(echo_evidence)

    result = await agent.run("Perform the compatibility check.")

    assert tool_calls == ["ping"]
    assert result.output == f"evidence:ping:{evidence_token}"
    usage = result.usage
    assert usage.requests >= 2
    assert usage.tool_calls >= 1
