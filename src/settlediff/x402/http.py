"""Bounded unsigned resource client for x402 challenge preflight."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Protocol, cast, runtime_checkable
from urllib.parse import urlsplit, urlunsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field, JsonValue

from settlediff.application.auth import PaidExecutionRequest
from settlediff.domain.models import UtcDatetime
from settlediff.x402.signed import (
    SellerIdentityEvidence,
    did_for_resource,
    parse_seller_json_object,
)
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


@runtime_checkable
class SellerIdentityPort(Protocol):
    async def seller_identity(self, resource_url: str, kid: str) -> SellerIdentityEvidence: ...


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

    async def seller_identity(self, resource_url: str, kid: str) -> SellerIdentityEvidence:
        try:
            did = did_for_resource(resource_url)
            if not kid.startswith(did + "#"):
                raise ValueError("seller key does not match the authorized resource origin")
            parts = urlsplit(resource_url)
            url = urlunsplit((parts.scheme, parts.netloc, "/.well-known/did.json", "", ""))
            async with self._client.stream(
                "GET",
                url,
                timeout=self._timeout_seconds,
                follow_redirects=False,
            ) as response:
                if response.status_code != 200:
                    raise ValueError("seller identity was not returned")
                content = bytearray()
                async for chunk in response.aiter_bytes():
                    content.extend(chunk)
                    if len(content) > 16384:
                        raise ValueError("seller identity exceeds its bound")
            document = parse_seller_json_object(content)
            return SellerIdentityEvidence.capture(did, kid, document, observed_at=datetime.now(UTC))
        except (httpx.HTTPError, ValueError, RecursionError) as error:
            raise X402ResourceError("seller identity could not be captured safely") from error

    async def challenge(self, request: PaidExecutionRequest) -> X402ResourceResponse:
        if not is_safe_x402_target(request.target):
            raise X402ResourceError(
                "x402 resource target requires HTTPS or loopback HTTP without "
                "credentials or fragment"
            )
        try:
            async with self._client.stream(
                request.method,
                request.target,
                json=request.body if request.method == "POST" else None,
                timeout=self._timeout_seconds,
                follow_redirects=False,
            ) as response:
                content = bytearray()
                async for chunk in response.aiter_bytes():
                    content.extend(chunk)
                    if len(content) > self._max_response_bytes:
                        raise X402ResourceError(
                            "x402 resource response exceeded its configured limit"
                        )
                status_code = response.status_code
                payment_required = response.headers.get("PAYMENT-REQUIRED")
        except httpx.HTTPError as error:
            raise X402ResourceError("x402 resource challenge request failed") from error
        body: JsonValue | None = None
        if content:
            try:
                loaded: object = json.loads(content)
            except (json.JSONDecodeError, UnicodeDecodeError):
                loaded = None
            if loaded is None or isinstance(loaded, (dict, list, str, int, float, bool)):
                body = cast(JsonValue | None, loaded)
        return X402ResourceResponse(
            status_code=status_code,
            payment_required=payment_required,
            body=body,
            observed_at=datetime.now(UTC),
        )
