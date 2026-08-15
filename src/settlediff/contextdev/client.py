"""Bounded adapter for Context.dev's documented Markdown scrape API."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Literal, Protocol, cast
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel, ConfigDict, JsonValue, SecretStr

from settlediff.domain.models import CanonicalModel, ExecutionRecord, NonEmptyStr, UtcDatetime
from settlediff.domain.redaction import redact_embedded_identifiers

DEFAULT_MAX_BODY_BYTES = 1_048_576
MAX_EXCERPT_CHARS = 2_000
_SOURCE_FAILURE_STATUSES = frozenset({400, 404, 408, 413, 415})


class ContextDevProtocolError(ValueError):
    """Context.dev output did not satisfy its documented response contract."""

    def __init__(self, message: str, *, body_bytes: int) -> None:
        super().__init__(message)
        self.body_bytes = body_bytes


class ContextDevUnavailableError(RuntimeError):
    """Context.dev itself could not be reached, authenticated, or used."""


class ContextEvidenceRequest(CanonicalModel):
    url: NonEmptyStr
    claim: NonEmptyStr


class ContextEvidence(CanonicalModel):
    url: NonEmptyStr
    reachable: bool
    evidence_present: bool | None
    excerpt: NonEmptyStr | None
    fetched_at: UtcDatetime
    note: NonEmptyStr | None


class ContextEvidencePort(Protocol):
    async def verify(self, request: ContextEvidenceRequest) -> ContextEvidence: ...


class ContextDevResponseModel(BaseModel):
    model_config = ConfigDict(strict=True, extra="allow", frozen=True)


class ContextDevSuccessResponse(ContextDevResponseModel):
    success: Literal[True]
    markdown: str
    contentLength: int
    url: str
    metadata: dict[str, JsonValue]


class ContextDevErrorResponse(ContextDevResponseModel):
    message: str
    error_code: str


def parse_contextdev_success(
    body: bytes, *, max_body_bytes: int = DEFAULT_MAX_BODY_BYTES
) -> ContextDevSuccessResponse:
    payload = _json_object(body, max_body_bytes=max_body_bytes)
    try:
        return ContextDevSuccessResponse.model_validate(payload)
    except ValueError as error:
        raise ContextDevProtocolError(
            "Context.dev success response did not match the Markdown scrape contract",
            body_bytes=len(body),
        ) from error


def parse_contextdev_error(
    body: bytes, *, max_body_bytes: int = DEFAULT_MAX_BODY_BYTES
) -> ContextDevErrorResponse:
    payload = _json_object(body, max_body_bytes=max_body_bytes)
    try:
        return ContextDevErrorResponse.model_validate(payload)
    except ValueError as error:
        raise ContextDevProtocolError(
            "Context.dev error response did not match its documented contract",
            body_bytes=len(body),
        ) from error


def eligible_evidence_url(execution: ExecutionRecord) -> str | None:
    """Return an HTTPS status URL only when the purchased service failed."""
    status = execution.upstream_http_status
    if status is None or 200 <= status < 300:
        return None
    body = execution.response_body
    if not isinstance(body, dict):
        return None
    candidate = cast(dict[str, JsonValue], body).get("status_url")
    if not isinstance(candidate, str):
        return None
    return candidate if _is_https_url(candidate) else None


class ContextDevClient:
    """Fetch one status page and check exact claim presence deterministically."""

    def __init__(
        self,
        base_url: str,
        api_key: SecretStr,
        *,
        timeout_seconds: float = 10.0,
        transport: httpx.AsyncBaseTransport | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not _is_https_url(base_url):
            raise ValueError("Context.dev base URL must be an absolute HTTPS URL")
        self._endpoint = f"{base_url.rstrip('/')}/web/scrape/markdown"
        self._clock = clock if clock is not None else lambda: datetime.now(UTC)
        self._client = httpx.AsyncClient(
            transport=transport,
            timeout=httpx.Timeout(timeout_seconds),
            headers={"Authorization": f"Bearer {api_key.get_secret_value()}"},
        )

    async def verify(self, request: ContextEvidenceRequest) -> ContextEvidence:
        if not _is_https_url(request.url):
            raise ValueError("Context.dev evidence URL must be an absolute HTTPS URL")
        try:
            response = await self._client.get(
                self._endpoint,
                params={
                    "url": request.url,
                    "includeLinks": "false",
                    "useMainContentOnly": "true",
                },
            )
        except httpx.HTTPError as error:
            raise ContextDevUnavailableError(
                "Context.dev unavailable after a transport failure"
            ) from error

        if response.status_code == 200:
            parsed = parse_contextdev_success(response.content)
            excerpt = _exact_excerpt(parsed.markdown, request.claim)
            return ContextEvidence(
                url=request.url,
                reachable=True,
                evidence_present=excerpt is not None,
                excerpt=excerpt,
                fetched_at=self._clock(),
                note=None,
            )
        if response.status_code in _SOURCE_FAILURE_STATUSES:
            error = parse_contextdev_error(response.content)
            return ContextEvidence(
                url=request.url,
                reachable=False,
                evidence_present=None,
                excerpt=None,
                fetched_at=self._clock(),
                note=redact_embedded_identifiers(error.message),
            )
        raise ContextDevUnavailableError(
            f"Context.dev unavailable after HTTP {response.status_code}"
        )

    async def aclose(self) -> None:
        await self._client.aclose()


def _json_object(body: bytes, *, max_body_bytes: int) -> dict[str, JsonValue]:
    if max_body_bytes < 1 or len(body) > max_body_bytes:
        raise ContextDevProtocolError(
            "Context.dev response exceeded the configured size limit", body_bytes=len(body)
        )
    try:
        decoded = body.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise ContextDevProtocolError(
            "Context.dev response must be UTF-8", body_bytes=len(body)
        ) from error
    try:
        loaded: object = json.loads(decoded)
    except json.JSONDecodeError as error:
        raise ContextDevProtocolError(
            "Context.dev response must contain one valid JSON value", body_bytes=len(body)
        ) from error
    if not isinstance(loaded, dict):
        raise ContextDevProtocolError(
            "Context.dev response must be a JSON object", body_bytes=len(body)
        )
    return cast(dict[str, JsonValue], loaded)


def _exact_excerpt(markdown: str, claim: str) -> str | None:
    start = markdown.casefold().find(claim.casefold())
    if start < 0:
        return None
    margin = max((MAX_EXCERPT_CHARS - len(claim)) // 2, 0)
    excerpt_start = max(start - margin, 0)
    excerpt = markdown[excerpt_start : excerpt_start + MAX_EXCERPT_CHARS].strip()
    return redact_embedded_identifiers(excerpt) or None


def _is_https_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme == "https" and bool(parsed.netloc)
