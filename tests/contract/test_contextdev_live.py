"""Opt-in live Context.dev contract; one invocation consumes one Context.dev credit."""

from __future__ import annotations

import os

import pytest
from pydantic import SecretStr

from settlediff.contextdev.client import ContextDevClient, ContextEvidence, ContextEvidenceRequest

pytestmark = [pytest.mark.live_contextdev, pytest.mark.asyncio]


async def test_live_contextdev_returns_coherent_typed_evidence() -> None:
    if os.getenv("SETTLEDIFF_LIVE_CONTEXTDEV") != "1":
        pytest.skip("set SETTLEDIFF_LIVE_CONTEXTDEV=1 to spend one Context.dev credit")

    api_key = os.getenv("SETTLEDIFF_CONTEXTDEV_API_KEY")
    url = os.getenv("SETTLEDIFF_LIVE_CONTEXTDEV_URL")
    claim = os.getenv("SETTLEDIFF_LIVE_CONTEXTDEV_CLAIM")
    assert api_key, "SETTLEDIFF_CONTEXTDEV_API_KEY is required for the live contract"
    assert url, "SETTLEDIFF_LIVE_CONTEXTDEV_URL must be owner-supplied"
    assert claim, "SETTLEDIFF_LIVE_CONTEXTDEV_CLAIM must be owner-supplied"

    client = ContextDevClient("https://api.context.dev/v1", SecretStr(api_key))
    try:
        evidence = await client.verify(ContextEvidenceRequest(url=url, claim=claim))
    finally:
        await client.aclose()

    assert isinstance(evidence, ContextEvidence)
    assert evidence.url == url
    assert evidence.fetched_at.utcoffset() is not None
    assert evidence.body_bytes is not None and evidence.body_bytes > 0
    if evidence.reachable:
        assert isinstance(evidence.evidence_present, bool)
        assert evidence.note is None
        assert (evidence.excerpt is not None) is evidence.evidence_present
    else:
        assert evidence.evidence_present is None
        assert evidence.excerpt is None
        assert evidence.note is not None
