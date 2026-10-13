"""Explicitly gated Groq compatibility probe using only synthetic prompts and tools."""

from __future__ import annotations

import os

import pytest
from pydantic_ai import models

from settlediff.config import Settings
from settlediff.telemetry.setup import configure_telemetry
from tests.contract.model_compatibility import check_chat_compatibility

pytestmark = [pytest.mark.live, pytest.mark.live_groq]


@pytest.mark.asyncio
async def test_groq_supports_structured_output_and_tool_continuation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if os.getenv("SETTLEDIFF_LIVE_GROQ") != "1":
        pytest.skip("set SETTLEDIFF_LIVE_GROQ=1 to run the live compatibility probe")
    monkeypatch.setattr(models, "ALLOW_MODEL_REQUESTS", True)
    settings = Settings()
    settings.require_observability()
    telemetry = configure_telemetry(settings)
    try:
        with telemetry.span(
            "settlediff.model.compatibility", {"component": "agent", "provider": "groq"}
        ):
            usage = await check_chat_compatibility(settings.require_groq())
            telemetry.model_requests(usage.requests, "groq")
            telemetry.tool_calls(usage.tool_calls, "groq")
            telemetry.token_usage(usage.input_tokens, usage.output_tokens, "groq")
    finally:
        telemetry.shutdown()
