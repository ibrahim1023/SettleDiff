from __future__ import annotations

import json

import httpx
import pytest

from settlediff.x402.rpc import X402RpcClient, X402RpcError


@pytest.mark.asyncio
async def test_rpc_client_uses_bounded_read_only_json_rpc_calls() -> None:
    requests: list[dict[str, object]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert request.extensions["timeout"]["read"] == 0.25
        requests.append(payload)
        result = "0x14a34" if payload["method"] == "eth_chainId" else None
        return httpx.Response(
            200,
            json={"jsonrpc": "2.0", "id": payload["id"], "result": result},
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="https://rpc.example.invalid",
    ) as http:
        client = X402RpcClient(http, max_requests=2, timeout_seconds=0.25)

        assert await client.call("eth_chainId", ()) == "0x14a34"
        assert await client.call("eth_getTransactionReceipt", ("syn_hash",)) is None

    assert [request["method"] for request in requests] == [
        "eth_chainId",
        "eth_getTransactionReceipt",
    ]
    assert all("private" not in json.dumps(request) for request in requests)


@pytest.mark.asyncio
async def test_rpc_client_rejects_calls_after_request_budget() -> None:
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": calls, "result": None})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="https://rpc.example.invalid",
    ) as http:
        client = X402RpcClient(http, max_requests=1)
        await client.call("eth_chainId", ())
        with pytest.raises(X402RpcError, match="request limit"):
            await client.call("eth_chainId", ())

    assert calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["oversized", "timeout", "status", "malformed", "rpc_error"])
async def test_rpc_client_fails_safely_without_retry(mode: str) -> None:
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if mode == "timeout":
            raise httpx.ReadTimeout("synthetic secret timeout", request=request)
        if mode == "status":
            return httpx.Response(503, text="synthetic secret failure")
        if mode == "malformed":
            return httpx.Response(200, content=b"not-json")
        if mode == "rpc_error":
            return httpx.Response(
                200,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "error": {"code": -1, "message": "synthetic secret"},
                },
            )
        return httpx.Response(200, content=b"{" + b" " * 2_000 + b"}")

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="https://rpc.example.invalid",
    ) as http:
        client = X402RpcClient(http, max_response_bytes=512)
        with pytest.raises(X402RpcError) as error:
            await client.call("eth_chainId", ())

    assert calls == 1
    assert "synthetic secret" not in str(error.value)


@pytest.mark.asyncio
async def test_rpc_client_rejects_mutating_or_unknown_methods_before_request() -> None:
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": None})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="https://rpc.example.invalid",
    ) as http:
        client = X402RpcClient(http)
        with pytest.raises(X402RpcError, match="method"):
            await client.call("eth_sendRawTransaction", ("syn_payload",))

    assert calls == 0
