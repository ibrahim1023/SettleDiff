from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import cast

import httpx
import pytest
from pydantic import JsonValue, SecretStr

from settlediff.contextdev.client import (
    DEFAULT_MAX_BODY_BYTES,
    MAX_EXCERPT_CHARS,
    ContextDevClient,
    ContextDevProtocolError,
    ContextDevUnavailableError,
    ContextEvidenceRequest,
    eligible_evidence_url,
)
from settlediff.domain.models import ExecutionRecord, SettlementStatus
from settlediff.domain.money import Money

FIXTURES = Path(__file__).parent / "contextdev"
BASE_URL = "https://api.context.dev/v1"
ENDPOINT = f"{BASE_URL}/web/scrape/markdown"
REQUEST = ContextEvidenceRequest(
    url="https://status.example.invalid/incidents/syn_incident", claim="HTTP 503"
)
NOW = datetime(2026, 8, 13, tzinfo=UTC)


def fixture_bytes(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def envelope_transport(body: bytes, *, status: int = 200) -> httpx.MockTransport:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, content=body)

    return httpx.MockTransport(handler)


def client_for(transport: httpx.MockTransport) -> ContextDevClient:
    return ContextDevClient(
        BASE_URL,
        SecretStr("syn-contextdev-key"),
        transport=transport,
        clock=lambda: NOW,
    )


@pytest.mark.asyncio
async def test_documented_markdown_response_returns_exact_evidence() -> None:
    evidence = await client_for(envelope_transport(fixture_bytes("reachable.json"))).verify(REQUEST)

    assert evidence.url == REQUEST.url
    assert evidence.reachable is True
    assert evidence.evidence_present is True
    assert evidence.excerpt is not None
    assert "HTTP 503" in evidence.excerpt
    assert evidence.fetched_at == NOW
    assert evidence.note is None
    assert evidence.body_bytes == len(fixture_bytes("reachable.json"))


@pytest.mark.asyncio
async def test_request_uses_documented_endpoint_query_and_bearer_authorization() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["authorization"] = request.headers.get("authorization")
        captured["target"] = f"{request.url.scheme}://{request.url.host}{request.url.path}"
        captured["params"] = dict(request.url.params)
        return httpx.Response(200, content=fixture_bytes("reachable.json"))

    await client_for(httpx.MockTransport(handler)).verify(REQUEST)

    assert captured == {
        "authorization": "Bearer syn-contextdev-key",
        "target": ENDPOINT,
        "params": {
            "url": REQUEST.url,
            "includeLinks": "false",
            "useMainContentOnly": "true",
        },
    }


@pytest.mark.asyncio
async def test_source_scrape_failure_records_unreachable_evidence() -> None:
    evidence = await client_for(
        envelope_transport(fixture_bytes("unavailable.json"), status=400)
    ).verify(REQUEST)

    assert evidence.reachable is False
    assert evidence.evidence_present is None
    assert evidence.excerpt is None
    assert evidence.note == "synthetic source could not be scraped"
    assert evidence.body_bytes == len(fixture_bytes("unavailable.json"))


@pytest.mark.asyncio
async def test_absent_exact_claim_records_no_evidence() -> None:
    evidence = await client_for(envelope_transport(fixture_bytes("unsupported_claim.json"))).verify(
        REQUEST
    )

    assert evidence.reachable is True
    assert evidence.evidence_present is False
    assert evidence.excerpt is None
    assert evidence.note is None


@pytest.mark.asyncio
async def test_malformed_success_response_is_a_protocol_error() -> None:
    with pytest.raises(ContextDevProtocolError, match="valid JSON"):
        await client_for(envelope_transport(fixture_bytes("malformed.stdout"))).verify(REQUEST)


@pytest.mark.asyncio
async def test_malformed_source_error_is_a_protocol_error() -> None:
    with pytest.raises(ContextDevProtocolError, match="error response"):
        await client_for(envelope_transport(b"{}", status=404)).verify(REQUEST)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 403, 429, 500])
async def test_provider_http_failure_is_unavailable(status: int) -> None:
    with pytest.raises(ContextDevUnavailableError, match=f"HTTP {status}") as raised:
        await client_for(envelope_transport(b"{}", status=status)).verify(REQUEST)

    assert raised.value.body_bytes == 2


@pytest.mark.asyncio
async def test_timeout_is_unavailable() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("synthetic timeout")

    with pytest.raises(ContextDevUnavailableError, match="transport failure") as raised:
        await client_for(httpx.MockTransport(handler)).verify(REQUEST)

    assert raised.value.body_bytes is None


@pytest.mark.asyncio
async def test_oversized_response_is_a_protocol_error_with_a_byte_count() -> None:
    body = b"x" * (DEFAULT_MAX_BODY_BYTES + 1)

    with pytest.raises(ContextDevProtocolError, match="size limit") as raised:
        await client_for(envelope_transport(body)).verify(REQUEST)

    assert raised.value.body_bytes == len(body)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    ["http://status.example.invalid/x", "status.example.invalid", "https://"],
)
async def test_non_https_evidence_urls_are_rejected_before_request(url: str) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise AssertionError("no request may be sent for an ineligible URL")

    with pytest.raises(ValueError, match="HTTPS"):
        await client_for(httpx.MockTransport(handler)).verify(
            ContextEvidenceRequest(url=url, claim="HTTP 503")
        )


@pytest.mark.asyncio
async def test_exact_excerpt_is_bounded() -> None:
    markdown = "a" * MAX_EXCERPT_CHARS + " HTTP 503 " + "b" * MAX_EXCERPT_CHARS
    body = json.dumps(
        {
            "success": True,
            "markdown": markdown,
            "contentLength": len(markdown.encode()),
            "url": REQUEST.url,
            "metadata": {"sourceUrl": REQUEST.url, "finalUrl": REQUEST.url},
        }
    ).encode()
    evidence = await client_for(envelope_transport(body)).verify(REQUEST)
    assert evidence.excerpt is not None
    assert len(evidence.excerpt) == MAX_EXCERPT_CHARS
    assert "HTTP 503" in evidence.excerpt


def execution_record(*, status: int | None, response_body: object) -> ExecutionRecord:
    return ExecutionRecord(
        vendor_slug="synthetic-search",
        upstream_http_status=status,
        charge=Money(amount=Decimal("0.01"), unit="USDC"),
        asset="USDC",
        protocol="mpp",
        chain="tempo",
        recipient="syn_recipient",
        settlement_status=SettlementStatus.SETTLED,
        transaction_id="syn_tx_context",
        session_id=None,
        transaction_hash=None,
        response_body=cast(JsonValue, response_body),
        executed_at=datetime(2026, 8, 13, tzinfo=UTC),
    )


@pytest.mark.parametrize(
    ("status", "body", "expected"),
    [
        (
            503,
            {"status_url": "https://status.example.invalid/x"},
            "https://status.example.invalid/x",
        ),
        (503, {"status_url": "http://status.example.invalid/x"}, None),
        (503, {"error": "synthetic"}, None),
        (503, None, None),
        (200, {"status_url": "https://status.example.invalid/x"}, None),
        (None, {"status_url": "https://status.example.invalid/x"}, None),
    ],
)
def test_eligibility_requires_failed_service_and_https_status_url(
    status: int | None, body: object, expected: str | None
) -> None:
    assert eligible_evidence_url(execution_record(status=status, response_body=body)) == expected
