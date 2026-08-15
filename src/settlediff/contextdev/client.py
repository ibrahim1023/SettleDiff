"""Bounded Context.dev evidence adapter.

Context.dev is the only web-evidence adapter in the MVP. It records source
reachability and exact evidence presence for one eligible URL; it performs no
semantic fact checking and its results never change deterministic findings.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Literal, Protocol, cast
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel, ConfigDict, JsonValue, SecretStr

from settlediff.domain.models import (
    CanonicalModel,
    ExecutionRecord,
    NonEmptyStr,
    UtcDatetime,
)
from settlediff.domain.redaction import redact_embedded_identifiers

DEFAULT_MAX_BODY_BYTES = 1_048_576
MAX_EXCERPT_CHARS = 2_000
KNOWN_ERROR_CODES = frozenset({"SOURCE_UNAVAILABLE", "CLAIM_UNSUPPORTED"})


class ContextDevProtocolError(ValueError):
    """Context.dev output did not satisfy the JSON envelope contract."""

    def __init__(self, message: str, *, body_bytes: int) -> None:
        super().__init__(message)
        self.body_bytes = body_bytes


class ContextDevUnavailableError(RuntimeError):
    """Context.dev itself could not be reached; source reachability is unknown."""


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
    """The single narrow evidence call exposed to a live investigation."""

    async def verify(self, request: ContextEvidenceRequest) -> ContextEvidence: ...


class ContextDevParserModel(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)


class ContextDevError(ContextDevParserModel):
    code: str
    message: str
    recoverable: bool


class ContextDevSuccessEnvelope(ContextDevParserModel):
    ok: Literal[True]
    payload: dict[str, JsonValue]


class ContextDevErrorEnvelope(ContextDevParserModel):
    ok: Literal[False]
    error: ContextDevError
    payload: dict[str, JsonValue]


ContextDevEnvelope = ContextDevSuccessEnvelope | ContextDevErrorEnvelope


def parse_contextdev_envelope(
    body: bytes, *, max_body_bytes: int = DEFAULT_MAX_BODY_BYTES
) -> ContextDevEnvelope:
    """Parse exactly one bounded JSON envelope without inspecting prose."""
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
            "Context.dev envelope must be a JSON object", body_bytes=len(body)
        )
    payload = cast(dict[str, JsonValue], loaded)
    ok = payload.get("ok")
    if type(ok) is not bool:
        raise ContextDevProtocolError(
            "Context.dev envelope requires a boolean 'ok' field", body_bytes=len(body)
        )
    if ok:
        return ContextDevSuccessEnvelope(ok=True, payload=payload)
    raw_error = payload.get("error")
    if not isinstance(raw_error, dict):
        raise ContextDevProtocolError(
            "Context.dev error must be a JSON object", body_bytes=len(body)
        )
    error_payload = cast(dict[str, JsonValue], raw_error)
    code = error_payload.get("code")
    message = error_payload.get("message")
    recoverable = error_payload.get("recoverable")
    if not isinstance(code, str) or not code:
        raise ContextDevProtocolError(
            "Context.dev error.code must be a non-empty string", body_bytes=len(body)
        )
    if not isinstance(message, str) or not message:
        raise ContextDevProtocolError(
            "Context.dev error.message must be a non-empty string", body_bytes=len(body)
        )
    if type(recoverable) is not bool:
        raise ContextDevProtocolError(
            "Context.dev error.recoverable must be a boolean", body_bytes=len(body)
        )
    return ContextDevErrorEnvelope(
        ok=False,
        error=ContextDevError(code=code, message=message, recoverable=recoverable),
        payload=payload,
    )


def eligible_evidence_url(execution: ExecutionRecord) -> str | None:
    """Return the service-provided status URL when independent evidence is warranted.

    Context.dev is exposed only when the purchased service failed (the
    service_execution check cannot pass) and the service response carries an
    HTTPS status URL to check.
    """
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
    """HTTP adapter that validates URLs and bounds every response."""

    def __init__(
        self,
        base_url: str,
        api_key: SecretStr,
        *,
        timeout_seconds: float = 10.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not _is_https_url(base_url):
            raise ValueError("Context.dev base URL must be an absolute HTTPS URL")
        self._endpoint = base_url
        self._client = httpx.AsyncClient(
            transport=transport,
            timeout=httpx.Timeout(timeout_seconds),
            headers={"Authorization": f"Bearer {api_key.get_secret_value()}"},
        )

    async def verify(self, request: ContextEvidenceRequest) -> ContextEvidence:
        if not _is_https_url(request.url):
            raise ValueError("Context.dev evidence URL must be an absolute HTTPS URL")
        try:
            response = await self._client.post(
                self._endpoint,
                content=json.dumps(
                    {"url": request.url, "claim": request.claim}, separators=(",", ":")
                ),
                headers={"Content-Type": "application/json"},
            )
        except httpx.HTTPError as error:
            raise ContextDevUnavailableError(
                "Context.dev reachability unknown after a transport failure"
            ) from error
        if response.status_code != 200:
            raise ContextDevUnavailableError(
                f"Context.dev reachability unknown after HTTP {response.status_code}"
            )
        envelope = parse_contextdev_envelope(response.content)
        if isinstance(envelope, ContextDevErrorEnvelope):
            error = envelope.error
            if error.code not in KNOWN_ERROR_CODES:
                raise ContextDevProtocolError(
                    f"Context.dev returned an unknown error code {error.code!r}",
                    body_bytes=len(response.content),
                )
            return ContextEvidence(
                url=request.url,
                reachable=error.code == "CLAIM_UNSUPPORTED",
                evidence_present=None,
                excerpt=None,
                fetched_at=datetime.now(UTC),
                note=redact_embedded_identifiers(error.message),
            )
        return _evidence_from_result(request.url, envelope.payload, len(response.content))

    async def aclose(self) -> None:
        await self._client.aclose()


def _evidence_from_result(
    url: str, payload: dict[str, JsonValue], body_bytes: int
) -> ContextEvidence:
    result = payload.get("result")
    if not isinstance(result, dict):
        raise ContextDevProtocolError(
            "Context.dev success envelope did not include a result object", body_bytes=body_bytes
        )
    fields = cast(dict[str, JsonValue], result)
    reachable = fields.get("reachable")
    evidence_present = fields.get("evidence_present")
    excerpt = fields.get("excerpt")
    fetched_at = fields.get("fetched_at")
    if type(reachable) is not bool:
        raise ContextDevProtocolError(
            "Context.dev result.reachable must be a boolean", body_bytes=body_bytes
        )
    if evidence_present is not None and type(evidence_present) is not bool:
        raise ContextDevProtocolError(
            "Context.dev result.evidence_present must be a boolean or null", body_bytes=body_bytes
        )
    if excerpt is not None and not isinstance(excerpt, str):
        raise ContextDevProtocolError(
            "Context.dev result.excerpt must be a string or null", body_bytes=body_bytes
        )
    if not isinstance(fetched_at, str):
        raise ContextDevProtocolError(
            "Context.dev result.fetched_at must be an ISO timestamp", body_bytes=body_bytes
        )
    try:
        fetched = datetime.fromisoformat(fetched_at)
    except ValueError as error:
        raise ContextDevProtocolError(
            "Context.dev result.fetched_at must be an ISO timestamp", body_bytes=body_bytes
        ) from error
    bounded = excerpt[:MAX_EXCERPT_CHARS] if excerpt is not None else None
    return ContextEvidence(
        url=url,
        reachable=reachable,
        evidence_present=evidence_present,
        excerpt=redact_embedded_identifiers(bounded) if bounded is not None else None,
        fetched_at=fetched,
        note=None,
    )


def _is_https_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme == "https" and bool(parsed.netloc)
