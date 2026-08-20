"""Bounded adapter for Context.dev's documented Markdown scrape API."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from enum import StrEnum
from ipaddress import ip_address
from typing import Literal, Protocol, Self, cast
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel, ConfigDict, Field, JsonValue, SecretStr, model_validator

from settlediff.domain.models import CanonicalModel, ExecutionRecord, NonEmptyStr, UtcDatetime
from settlediff.domain.redaction import redact_embedded_identifiers

CONTEXTDEV_API_PATH = "/web/scrape/markdown"
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

    def __init__(self, message: str, *, body_bytes: int | None = None) -> None:
        super().__init__(message)
        self.body_bytes = body_bytes


class ContextEvidenceState(StrEnum):
    NOT_APPLICABLE = "NOT_APPLICABLE"
    PRESENT = "PRESENT"
    ABSENT = "ABSENT"
    SOURCE_UNREACHABLE = "SOURCE_UNREACHABLE"
    PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"
    PROTOCOL_ERROR = "PROTOCOL_ERROR"
    BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"


class ContextEvidenceDiagnostic(StrEnum):
    SERVICE_DID_NOT_FAIL = "service_did_not_fail"
    MISSING_ELIGIBLE_HTTPS_STATUS_URL = "missing_eligible_https_status_url"
    EXACT_CLAIM_PRESENT = "exact_claim_present"
    EXACT_CLAIM_ABSENT = "exact_claim_absent"
    SOURCE_SCRAPE_FAILED = "source_scrape_failed"
    PROVIDER_REQUEST_FAILED = "provider_request_failed"
    PROVIDER_RESPONSE_INVALID = "provider_response_invalid"
    BUDGET_EXHAUSTED = "budget_exhausted"


class ContextEvidenceErrorClass(StrEnum):
    UNAVAILABLE = "ContextDevUnavailableError"
    PROTOCOL = "ContextDevProtocolError"


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
    body_bytes: int | None = Field(default=None, ge=0)


class ContextEvidenceRecord(CanonicalModel):
    state: ContextEvidenceState
    status_url: NonEmptyStr | None
    excerpt: NonEmptyStr | None
    observed_at: UtcDatetime
    diagnostic: ContextEvidenceDiagnostic
    error_class: ContextEvidenceErrorClass | None
    body_bytes: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def require_coherent_state(self) -> Self:
        diagnostics = {
            ContextEvidenceState.NOT_APPLICABLE: {
                ContextEvidenceDiagnostic.SERVICE_DID_NOT_FAIL,
                ContextEvidenceDiagnostic.MISSING_ELIGIBLE_HTTPS_STATUS_URL,
            },
            ContextEvidenceState.PRESENT: {ContextEvidenceDiagnostic.EXACT_CLAIM_PRESENT},
            ContextEvidenceState.ABSENT: {ContextEvidenceDiagnostic.EXACT_CLAIM_ABSENT},
            ContextEvidenceState.SOURCE_UNREACHABLE: {
                ContextEvidenceDiagnostic.SOURCE_SCRAPE_FAILED
            },
            ContextEvidenceState.PROVIDER_UNAVAILABLE: {
                ContextEvidenceDiagnostic.PROVIDER_REQUEST_FAILED
            },
            ContextEvidenceState.PROTOCOL_ERROR: {
                ContextEvidenceDiagnostic.PROVIDER_RESPONSE_INVALID
            },
            ContextEvidenceState.BUDGET_EXHAUSTED: {ContextEvidenceDiagnostic.BUDGET_EXHAUSTED},
        }
        if self.diagnostic not in diagnostics[self.state]:
            raise ValueError("diagnostic does not match Context evidence state")
        if self.state is ContextEvidenceState.NOT_APPLICABLE:
            if any(
                value is not None
                for value in (self.status_url, self.excerpt, self.error_class, self.body_bytes)
            ):
                raise ValueError("not-applicable Context evidence cannot contain provider data")
            return self
        if self.status_url is None or not _is_eligible_evidence_url(self.status_url):
            raise ValueError("attempted Context evidence requires an HTTPS status URL")
        if (self.state is ContextEvidenceState.PRESENT) is (self.excerpt is None):
            raise ValueError("only present Context evidence requires an excerpt")
        expected_error = {
            ContextEvidenceState.PROVIDER_UNAVAILABLE: ContextEvidenceErrorClass.UNAVAILABLE,
            ContextEvidenceState.PROTOCOL_ERROR: ContextEvidenceErrorClass.PROTOCOL,
        }.get(self.state)
        if self.error_class is not expected_error:
            raise ValueError("error class does not match Context evidence state")
        return self


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
    """Return a safe HTTPS status URL only when the purchased service failed.

    Public cross-domain status hosts remain eligible because status providers commonly differ
    from service hosts. Operator approval of cross-domain hosts is a separate product policy.
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
    return candidate if _is_eligible_evidence_url(candidate) else None


class ContextDevClient:
    """Fetch one status page and check exact claim presence deterministically."""

    def __init__(
        self,
        base_url: str,
        api_key: SecretStr,
        *,
        timeout_seconds: float = 60.0,
        transport: httpx.AsyncBaseTransport | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not _is_https_url(base_url):
            raise ValueError("Context.dev base URL must be an absolute HTTPS URL")
        self._endpoint = f"{base_url.rstrip('/')}{CONTEXTDEV_API_PATH}"
        self._clock = clock if clock is not None else lambda: datetime.now(UTC)
        self._client = httpx.AsyncClient(
            transport=transport,
            timeout=httpx.Timeout(timeout_seconds),
            headers={"Authorization": f"Bearer {api_key.get_secret_value()}"},
        )

    async def verify(self, request: ContextEvidenceRequest) -> ContextEvidence:
        if not _is_eligible_evidence_url(request.url):
            raise ValueError("Context.dev evidence URL must be an eligible HTTPS URL")
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
                body_bytes=len(response.content),
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
                body_bytes=len(response.content),
            )
        raise ContextDevUnavailableError(
            f"Context.dev unavailable after HTTP {response.status_code}",
            body_bytes=len(response.content),
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


def _is_eligible_evidence_url(value: str) -> bool:
    try:
        parsed = urlparse(value)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError:
        return False
    if (
        parsed.scheme != "https"
        or hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or "#" in value
        or parsed.netloc.endswith(":")
        or port not in (None, 443)
    ):
        return False
    return _is_public_host(hostname)


def _is_public_host(hostname: str) -> bool:
    if hostname.endswith(".."):
        return False
    host = hostname.rstrip(".").casefold()
    if not host or host == "localhost" or host.endswith(".localhost") or "%" in host:
        return False
    try:
        address = ip_address(host)
    except ValueError:
        return _is_valid_domain_host(host)
    return not any(
        (
            address.is_loopback,
            address.is_private,
            address.is_link_local,
            address.is_multicast,
            address.is_reserved,
            address.is_unspecified,
        )
    )


def _is_valid_domain_host(host: str) -> bool:
    try:
        ascii_host = host.encode("idna").decode("ascii")
    except UnicodeError:
        return False
    if len(ascii_host) > 253 or ascii_host.replace(".", "").isdigit():
        return False
    labels = ascii_host.split(".")
    return all(
        label
        and len(label) <= 63
        and label[0].isalnum()
        and label[-1].isalnum()
        and all(character.isalnum() or character == "-" for character in label)
        for label in labels
    )


def _is_https_url(value: str) -> bool:
    try:
        parsed = urlparse(value)
    except ValueError:
        return False
    return parsed.scheme == "https" and bool(parsed.netloc)
