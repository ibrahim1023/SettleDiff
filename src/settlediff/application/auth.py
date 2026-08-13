"""One-use authorization for one exact paid execution."""

from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, JsonValue

from settlediff.domain.money import Money


class AuthorizationError(ValueError):
    """A paid request is not covered by its capability."""


class PaidExecutionRequest(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    run_id: str
    target: str
    body: dict[str, JsonValue]
    budget: Money


class ConsumedPaidAuthorization:
    """Opaque proof that the exact capability was consumed before execution."""

    __slots__ = ("run_id", "target", "_body_digest", "_budget", "_proof")

    def __init__(
        self,
        request: PaidExecutionRequest,
        *,
        body_digest: str,
        proof: object,
    ) -> None:
        if proof is not _TOKEN_PROOF:
            raise TypeError("consumed authorization tokens cannot be constructed directly")
        self.run_id = request.run_id
        self.target = request.target
        self._body_digest = body_digest
        self._budget = request.budget
        self._proof = proof

    def require_exact_request(self, request: PaidExecutionRequest) -> None:
        """Reject a request that differs from the capability already consumed."""
        if self._proof is not _TOKEN_PROOF:
            raise AuthorizationError("authorization token is invalid")
        if request.run_id != self.run_id:
            raise AuthorizationError("authorization does not cover this run")
        if request.target != self.target:
            raise AuthorizationError("authorization does not cover this target")
        if PaidExecutionCapability.body_digest_for(request.body) != self._body_digest:
            raise AuthorizationError("authorization does not cover this request body")
        if request.budget != self._budget:
            raise AuthorizationError("authorization does not cover this exact budget")


_TOKEN_PROOF = object()


class PaidExecutionCapability:
    """Mutable one-shot state kept behind an async lock."""

    def __init__(
        self,
        request: PaidExecutionRequest,
        *,
        expires_at: datetime,
    ) -> None:
        expiry_offset = expires_at.utcoffset()
        if expiry_offset is None or expiry_offset.total_seconds() != 0:
            raise ValueError("capability expiry must be timezone-aware UTC")
        self._run_id = request.run_id
        self._target = request.target
        self._body_digest = self.body_digest_for(request.body)
        self._budget = request.budget
        self._expires_at = expires_at.astimezone(UTC)
        self._consumed = False
        self._lock = asyncio.Lock()

    @classmethod
    def issue(
        cls, request: PaidExecutionRequest, *, expires_at: datetime
    ) -> PaidExecutionCapability:
        return cls(request, expires_at=expires_at)

    @property
    def body_digest(self) -> str:
        return self._body_digest

    @staticmethod
    def body_digest_for(body: dict[str, JsonValue]) -> str:
        canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return hashlib.sha256(canonical.encode()).hexdigest()

    async def consume(
        self,
        request: PaidExecutionRequest,
        *,
        now: datetime | None = None,
    ) -> ConsumedPaidAuthorization:
        checked_at = now or datetime.now(UTC)
        checked_offset = checked_at.utcoffset()
        if checked_offset is None or checked_offset.total_seconds() != 0:
            raise AuthorizationError("authorization time must be timezone-aware UTC")

        async with self._lock:
            if self._consumed:
                raise AuthorizationError("authorization was already consumed")
            if checked_at > self._expires_at:
                raise AuthorizationError("authorization expired")
            if request.run_id != self._run_id:
                raise AuthorizationError("authorization does not cover this run")
            if request.target != self._target:
                raise AuthorizationError("authorization does not cover this target")
            if self.body_digest_for(request.body) != self._body_digest:
                raise AuthorizationError("authorization does not cover this request body")
            if request.budget != self._budget:
                raise AuthorizationError("authorization does not cover this exact budget")

            self._consumed = True
            return ConsumedPaidAuthorization(
                request,
                body_digest=self._body_digest,
                proof=_TOKEN_PROOF,
            )
