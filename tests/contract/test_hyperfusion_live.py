"""Opt-in compatibility probe for Hyperfusion's Chat Completions endpoint."""

from __future__ import annotations

import os

import pytest
from pydantic_ai import models

from settlediff.config import Settings
from tests.contract.model_compatibility import check_chat_compatibility

pytestmark = pytest.mark.live_hyperfusion


@pytest.mark.asyncio
async def test_hyperfusion_supports_structured_output_and_tool_continuation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if os.getenv("SETTLEDIFF_LIVE_HYPERFUSION") != "1":
        pytest.skip("set SETTLEDIFF_LIVE_HYPERFUSION=1 to run the live compatibility probe")
    monkeypatch.setattr(models, "ALLOW_MODEL_REQUESTS", True)
    await check_chat_compatibility(Settings().require_hyperfusion())
