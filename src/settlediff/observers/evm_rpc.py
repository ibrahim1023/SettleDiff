"""Bounded read-only JSON-RPC client for EVM settlement evidence."""

from __future__ import annotations

import asyncio
import json
from typing import cast

import httpx
from pydantic import JsonValue

_ALLOWED_METHODS = frozenset({"eth_chainId", "eth_getTransactionReceipt"})


class EvmRpcError(RuntimeError):
    pass


class EvmRpcProtocolError(EvmRpcError):
    pass


class EvmRpcClient:
    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        max_requests: int = 2,
        max_response_bytes: int = 1_048_576,
        timeout_seconds: float = 10.0,
    ) -> None:
        if max_requests < 1 or max_response_bytes < 1 or not 0 < timeout_seconds <= 60:
            raise ValueError("invalid EVM RPC limits")
        self._client = client
        self._max_requests = max_requests
        self._max_response_bytes = max_response_bytes
        self._timeout_seconds = timeout_seconds
        self._requests = 0
        self._lock = asyncio.Lock()

    async def call(self, method: str, params: tuple[JsonValue, ...]) -> JsonValue:
        if method not in _ALLOWED_METHODS:
            raise EvmRpcError("EVM RPC method is not read-only allowlisted")
        async with self._lock:
            if self._requests >= self._max_requests:
                raise EvmRpcError("EVM RPC request limit exhausted")
            self._requests += 1
            request_id = self._requests
        try:
            async with self._client.stream(
                "POST",
                "",
                json={"jsonrpc": "2.0", "id": request_id, "method": method, "params": params},
                timeout=self._timeout_seconds,
            ) as response:
                if response.status_code != 200:
                    raise EvmRpcError("EVM RPC returned a non-success HTTP status")
                content = bytearray()
                async for chunk in response.aiter_bytes():
                    content.extend(chunk)
                    if len(content) > self._max_response_bytes:
                        raise EvmRpcProtocolError("EVM RPC response exceeded its configured limit")
        except httpx.HTTPError as error:
            raise EvmRpcError("EVM RPC request failed") from error
        try:
            loaded: object = json.loads(content)
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            raise EvmRpcProtocolError("EVM RPC returned invalid JSON") from error
        if not isinstance(loaded, dict):
            raise EvmRpcProtocolError("EVM RPC response must be an object")
        payload = cast(dict[str, JsonValue], loaded)
        if (
            payload.get("jsonrpc") != "2.0"
            or payload.get("id") != request_id
            or "error" in payload
            or "result" not in payload
        ):
            raise EvmRpcProtocolError("EVM RPC response envelope is invalid")
        return payload["result"]
