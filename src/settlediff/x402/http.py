"""Bounded unsigned resource client for x402 challenge preflight."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Protocol, cast

import httpx
from pydantic import BaseModel, ConfigDict, Field, JsonValue

from settlediff.application.auth import PaidExecutionRequest
from settlediff.domain.models import UtcDatetime
from settlediff.x402.urls import is_safe_x402_target


class X402ResourceError(ValueError):
    pass


class X402ResourceResponse(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    status_code: int = Field(ge=100, le=599)
    payment_required: str | None
    body: JsonValue | None
    observed_at: UtcDatetime


class X402ResourcePort(Protocol):
    async def challenge(self, request: PaidExecutionRequest) -> X402ResourceResponse: ...


class X402ResourceClient:
    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        timeout_seconds: float = 10,
        max_response_bytes: int = 1_048_576,
    ) -> None:
        if not 0 < timeout_seconds <= 60 or max_response_bytes < 1:
            raise ValueError("invalid x402 resource client limits")
        self._client = client
        self._timeout_seconds = timeout_seconds
        self._max_response_bytes = max_response_bytes

    async def challenge(self, request: PaidExecutionRequest) -> X402ResourceResponse:
        if not is_safe_x402_target(request.target):
            raise X402ResourceError(
                "x402 resource target requires HTTPS or loopback HTTP without "
                "credentials or fragment"
            )
        try:
            response = await self._client.request(
                request.method,
                request.target,
                json=request.body if request.method == "POST" else None,
                timeout=self._timeout_seconds,
            )
        except httpx.HTTPError as error:
            raise X402ResourceError("x402 resource challenge request failed") from error
        if len(response.content) > self._max_response_bytes:
            raise X402ResourceError("x402 resource response exceeded its configured limit")
        body: JsonValue | None = None
        if response.content:
            try:
                loaded: object = json.loads(response.content)
            except (json.JSONDecodeError, UnicodeDecodeError):
                loaded = None
            if loaded is None or isinstance(loaded, (dict, list, str, int, float, bool)):
                body = cast(JsonValue | None, loaded)
        return X402ResourceResponse(
            status_code=response.status_code,
            payment_required=response.headers.get("PAYMENT-REQUIRED"),
            body=body,
            observed_at=datetime.now(UTC),
        )
