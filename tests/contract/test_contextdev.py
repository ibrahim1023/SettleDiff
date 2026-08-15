from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr

from settlediff.contextdev.client import (
    MAX_EXCERPT_CHARS,
    ContextDevClient,
    ContextDevProtocolError,
    ContextDevUnavailableError,
    ContextEvidence,
    ContextEvidenceRequest,
    eligible_evidence_url,
)
from settlediff.domain.models import ExecutionRecord, SettlementStatus
from settlediff.domain.money import Money

FIXTURES = Path(__file__).parent / "contextdev"
ENDPOINT = "https://contextdev.example.invalid/v1/evidence"
REQUEST = ContextEvidenceRequest(
    url="https://status.example.invalid/incidents/syn_incident", claim="HTTP 503"
)


def fixture_bytes(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def envelope_transport(body: bytes, *, status: int = 200) -> httpx.MockTransport:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, content=body)

    return httpx.MockTransport(handler)


def client_for(transport: httpx.MockTransport) -> ContextDevClient:
    return ContextDevClient(ENDPOINT, SecretStr("syn-contextdev-key"), transport=transport)


@pytest.mark.asyncio
async def test_reachable_source_returns_bounded_evidence() -> None:
    evidence = await client_for(envelope_transport(fixture_bytes("reachable.json"))).verify(REQUEST)

    assert evidence == ContextEvidence(
        url="https://status.example.invalid/incidents/syn_incident",
        reachable=True,
        evidence_present=True,
        excerpt=(
            "Synthetic status excerpt: syn_service returned HTTP 503 "
            "during the syn_window maintenance window."
        ),
        fetched_at=datetime(2026, 8, 13, tzinfo=UTC),
        note=None,
    )


@pytest.mark.asyncio
async def test_request_sends_url_claim_and_bearer_authorization() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["authorization"] = request.headers.get("authorization")
        captured["body"] = request.content.decode()
        captured["target"] = str(request.url)
        return httpx.Response(200, content=fixture_bytes("reachable.json"))

    await client_for(httpx.MockTransport(handler)).verify(REQUEST)

    assert captured["authorization"] == "Bearer syn-contextdev-key"
    assert captured["target"] == ENDPOINT
    assert captured["body"] == (
        '{"url":"https://status.example.invalid/incidents/syn_incident","claim":"HTTP 503"}'
    )


@pytest.mark.asyncio
async def test_unavailable_source_is_reachability_evidence_not_a_crash() -> None:
    evidence = await client_for(envelope_transport(fixture_bytes("unavailable.json"))).verify(
        REQUEST
    )

    assert evidence.reachable is False
    assert evidence.evidence_present is None
    assert evidence.excerpt is None
    assert evidence.note == "synthetic source did not respond"


@pytest.mark.asyncio
async def test_unsupported_claim_records_absence_of_evidence() -> None:
    evidence = await client_for(envelope_transport(fixture_bytes("unsupported_claim.json"))).verify(
        REQUEST
    )

    assert evidence.reachable is True
    assert evidence.evidence_present is None
    assert evidence.note == "synthetic claim cannot be answered from the source"


@pytest.mark.asyncio
async def test_malformed_response_is_a_protocol_error() -> None:
    with pytest.raises(ContextDevProtocolError, match="valid JSON"):
        await client_for(envelope_transport(fixture_bytes("malformed.stdout"))).verify(REQUEST)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [500, 503])
async def test_contextdev_http_failure_means_reachability_unknown(status: int) -> None:
    with pytest.raises(ContextDevUnavailableError, match="HTTP"):
        await client_for(envelope_transport(b"{}", status=status)).verify(REQUEST)


@pytest.mark.asyncio
async def test_timeout_means_reachability_unknown() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("synthetic timeout")

    with pytest.raises(ContextDevUnavailableError, match="reachability unknown"):
        await client_for(httpx.MockTransport(handler)).verify(REQUEST)


@pytest.mark.asyncio
async def test_unknown_error_code_is_a_protocol_error() -> None:
    body = b'{"ok":false,"error":{"code":"FUTURE_CODE","message":"synthetic","recoverable":false}}'
    with pytest.raises(ContextDevProtocolError, match="FUTURE_CODE"):
        await client_for(envelope_transport(body)).verify(REQUEST)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    ["http://status.example.invalid/x", "status.example.invalid", "https://"],
)
async def test_non_https_urls_are_rejected_before_any_request(url: str) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise AssertionError("no request may be sent for an ineligible URL")

    with pytest.raises(ValueError, match="HTTPS"):
        await client_for(httpx.MockTransport(handler)).verify(
            ContextEvidenceRequest(url=url, claim="HTTP 503")
        )


@pytest.mark.asyncio
async def test_oversized_excerpt_is_truncated_to_the_bound() -> None:
    body = (
        b'{"ok":true,"result":{"url":"https://status.example.invalid/x","reachable":true,'
        b'"evidence_present":true,"excerpt":"'
        + b"a" * (MAX_EXCERPT_CHARS + 10)
        + b'","fetched_at":"2026-08-13T00:00:00Z"}}'
    )
    evidence = await client_for(envelope_transport(body)).verify(REQUEST)
    assert evidence.excerpt is not None
    assert len(evidence.excerpt) == MAX_EXCERPT_CHARS


def execution_record(*, status: int | None, response_body: object) -> ExecutionRecord:
    from typing import cast

    from pydantic import JsonValue

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
