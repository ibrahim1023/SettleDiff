"""Bounded read-only JSON-RPC client for x402 settlement evidence."""

from __future__ import annotations

import asyncio
import json
from typing import cast

import httpx
from pydantic import JsonValue

_ALLOWED_METHODS = frozenset({"eth_chainId", "eth_getTransactionReceipt"})


class X402RpcError(RuntimeError):
    pass


class X402RpcClient:
    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        max_requests: int = 2,
        max_response_bytes: int = 1_048_576,
        timeout_seconds: float = 10.0,
    ) -> None:
        if max_requests < 1 or max_response_bytes < 1 or not 0 < timeout_seconds <= 60:
            raise ValueError("invalid x402 RPC limits")
        self._client = client
        self._max_requests = max_requests
        self._max_response_bytes = max_response_bytes
        self._timeout_seconds = timeout_seconds
        self._requests = 0
        self._lock = asyncio.Lock()

    async def call(self, method: str, params: tuple[JsonValue, ...]) -> JsonValue:
        if method not in _ALLOWED_METHODS:
            raise X402RpcError("x402 RPC method is not read-only allowlisted")
        async with self._lock:
            if self._requests >= self._max_requests:
                raise X402RpcError("x402 RPC request limit exhausted")
            self._requests += 1
            request_id = self._requests
        try:
            response = await self._client.post(
                "/",
                json={"jsonrpc": "2.0", "id": request_id, "method": method, "params": params},
                timeout=self._timeout_seconds,
            )
        except httpx.HTTPError as error:
            raise X402RpcError("x402 RPC request failed") from error
        if response.status_code != 200:
            raise X402RpcError("x402 RPC returned a non-success HTTP status")
        if len(response.content) > self._max_response_bytes:
            raise X402RpcError("x402 RPC response exceeded its configured limit")
        try:
            loaded: object = json.loads(response.content)
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            raise X402RpcError("x402 RPC returned invalid JSON") from error
        if not isinstance(loaded, dict):
            raise X402RpcError("x402 RPC response must be an object")
        payload = cast(dict[str, JsonValue], loaded)
        if (
            payload.get("jsonrpc") != "2.0"
            or payload.get("id") != request_id
            or "error" in payload
            or "result" not in payload
        ):
            raise X402RpcError("x402 RPC response envelope is invalid")
        return payload["result"]
