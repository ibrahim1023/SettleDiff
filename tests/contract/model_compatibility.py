"""Bounded, synthetic chat compatibility checks shared by the live provider probes."""

from __future__ import annotations

from uuid import uuid4

from pydantic import BaseModel
from pydantic_ai import Agent
from pydantic_ai.usage import RunUsage, UsageLimits

from settlediff.agent.model import build_chat_model
from settlediff.config import ChatCompletionsConfig


class ContractOutput(BaseModel):
    answer: str


async def check_chat_compatibility(config: ChatCompletionsConfig) -> RunUsage:
    model = build_chat_model(config)
    usage = RunUsage()
    try:
        structured = Agent(
            model,
            output_type=ContractOutput,
            instructions="Return the exact word 'structured-ok' in the answer field.",
            retries=0,
        )
        first = await structured.run(
            "Perform the structured-output check.",
            usage=usage,
            usage_limits=UsageLimits(
                request_limit=1, input_tokens_limit=4000, output_tokens_limit=256
            ),
            model_settings={"max_tokens": 256},
        )
        assert first.output.answer == "structured-ok"
        token = uuid4().hex
        calls: list[str] = []
        tools = Agent(
            model,
            output_type=str,
            instructions=(
                "Call echo_evidence exactly once with the word 'ping'. After the tool result, "
                "reply with its complete returned value, including its 'evidence:' prefix "
                "and opaque trailing token."
            ),
            retries=0,
        )

        async def echo_evidence(value: str) -> str:
            calls.append(value)
            return f"evidence:{value}:{token}"

        tools.tool_plain(echo_evidence)
        second = await tools.run(
            "Perform the compatibility check.",
            usage=usage,
            usage_limits=UsageLimits(
                request_limit=3,
                tool_calls_limit=1,
                input_tokens_limit=8000,
                output_tokens_limit=768,
            ),
            model_settings={"max_tokens": 256},
        )
        assert calls == ["ping"]
        assert second.output == f"evidence:ping:{token}"
        assert usage.requests == 3
        assert usage.tool_calls == 1
        return usage
    finally:
        assert model.provider is not None
        await model.provider.client.close()
