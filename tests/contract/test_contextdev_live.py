"""Opt-in live Context.dev contract; one invocation consumes one Context.dev credit."""

from __future__ import annotations

import os

import pytest

from settlediff.config import Settings
from settlediff.contextdev.client import ContextDevClient, ContextEvidence, ContextEvidenceRequest

pytestmark = [pytest.mark.live_contextdev, pytest.mark.asyncio]


async def test_live_contextdev_returns_coherent_typed_evidence() -> None:
    if os.getenv("SETTLEDIFF_LIVE_CONTEXTDEV") != "1":
        pytest.skip("set SETTLEDIFF_LIVE_CONTEXTDEV=1 to spend one Context.dev credit")

    url = os.getenv("SETTLEDIFF_LIVE_CONTEXTDEV_URL")
    claim = os.getenv("SETTLEDIFF_LIVE_CONTEXTDEV_CLAIM")
    assert url, "SETTLEDIFF_LIVE_CONTEXTDEV_URL must be owner-supplied"
    assert claim, "SETTLEDIFF_LIVE_CONTEXTDEV_CLAIM must be owner-supplied"
    config = Settings().require_contextdev()

    client = ContextDevClient(
        config.base_url,
        config.api_key,
        timeout_seconds=config.timeout_seconds,
    )
    try:
        evidence = await client.verify(ContextEvidenceRequest(url=url, claim=claim))
    finally:
        await client.aclose()

    assert isinstance(evidence, ContextEvidence)
    assert evidence.url == url
    assert evidence.fetched_at.utcoffset() is not None
    assert evidence.body_bytes is not None and evidence.body_bytes > 0
    assert evidence.reachable is True
    assert evidence.evidence_present is True
    assert evidence.excerpt is not None and claim in evidence.excerpt
    assert evidence.note is None
