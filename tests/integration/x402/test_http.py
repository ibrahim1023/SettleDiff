from __future__ import annotations

import json
from decimal import Decimal
from typing import Literal, cast

import httpx
import pytest
from pydantic import JsonValue

from settlediff.application.auth import PaidExecutionRequest
from settlediff.domain.money import Money
from settlediff.x402.http import X402ResourceClient, X402ResourceError


def request(
    *, method: Literal["GET", "POST"] = "POST", body: JsonValue | None = None
) -> PaidExecutionRequest:
    return PaidExecutionRequest(
        run_id="syn_x402_http",
        target="https://example.invalid/paid",
        method=method,
        body={} if body is None and method == "POST" else body,
        budget=Money(amount=Decimal("0.01"), unit="USDC"),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(("method", "body"), [("POST", {"query": "synthetic"}), ("GET", None)])
async def test_resource_client_preserves_exact_method_body_and_timeout(
    method: str, body: JsonValue | None
) -> None:
    requests: list[httpx.Request] = []

    async def handler(value: httpx.Request) -> httpx.Response:
        requests.append(value)
        assert value.extensions["timeout"]["read"] == 0.25
        return httpx.Response(
            402,
            headers={"PAYMENT-REQUIRED": "syn_header"},
            json={"error": "payment required"},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = X402ResourceClient(http, timeout_seconds=0.25)
        response = await client.challenge(
            request(method=cast(Literal["GET", "POST"], method), body=body)
        )

    assert response.status_code == 402
    assert response.payment_required == "syn_header"
    assert response.body == {"error": "payment required"}
    assert requests[0].method == method
    if method == "POST":
        assert json.loads(requests[0].content) == body
    else:
        assert requests[0].content == b""


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "target",
    [
        "http://example.invalid/paid",
        "https://user:secret@example.invalid/paid",
        "https://example.invalid/paid#fragment",
    ],
)
async def test_resource_client_rejects_unsafe_target_before_request(target: str) -> None:
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(402)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = X402ResourceClient(http)
        with pytest.raises(X402ResourceError, match="target"):
            await client.challenge(request().model_copy(update={"target": target}))

    assert calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["oversized", "timeout"])
async def test_resource_client_fails_bounded_without_leaking_response(mode: str) -> None:
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if mode == "timeout":
            raise httpx.ReadTimeout("synthetic secret", request=request)
        return httpx.Response(402, content=b"x" * 2_000)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = X402ResourceClient(http, max_response_bytes=512)
        with pytest.raises(X402ResourceError) as error:
            await client.challenge(request())

    assert calls == 1
    assert "synthetic secret" not in str(error.value)
