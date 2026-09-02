"""One-use authorization for one exact paid execution."""

from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import UTC, datetime
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, JsonValue, StringConstraints, model_validator

from settlediff.domain.models import AssetIdentity, Caip2Network, NonEmptyStr
from settlediff.domain.money import Money


class AuthorizationError(ValueError):
    """A paid request is not covered by its capability."""


Sha256Digest = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]


class PaymentTerms(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    adapter_id: NonEmptyStr
    protocol_version: NonEmptyStr | None
    scheme: NonEmptyStr | None
    network: Caip2Network | None
    chain: NonEmptyStr | None
    asset: AssetIdentity | None
    asset_symbol: NonEmptyStr | None
    recipient: NonEmptyStr | None
    quoted_price: Money
    max_timeout_seconds: int | None = Field(default=None, gt=0, le=86_400)
    resource_url: NonEmptyStr
    method: Literal["GET", "POST"]
    body_digest: Sha256Digest

    @property
    def digest(self) -> str:
        canonical = json.dumps(
            self.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        return hashlib.sha256(canonical.encode()).hexdigest()

    @model_validator(mode="after")
    def require_consistent_asset(self) -> Self:
        if self.quoted_price.amount <= 0:
            raise ValueError("payment terms quote must be positive")
        if self.asset is not None:
            if self.network != self.asset.network:
                raise ValueError("payment terms asset network must match the selected network")
            if self.asset_symbol != self.asset.symbol:
                raise ValueError("payment terms asset symbol must match the selected asset")
        if self.asset_symbol is not None and self.quoted_price.unit != self.asset_symbol:
            raise ValueError("payment terms quote unit must match the selected asset")
        return self


class PaidExecutionRequest(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    run_id: str
    target: str
    body: JsonValue | None
    budget: Money
    method: Literal["GET", "POST"] = "POST"


class ConsumedPaidAuthorization:
    """Opaque proof that the exact capability was consumed before execution."""

    __slots__ = (
        "run_id",
        "target",
        "_method",
        "_body_digest",
        "_budget",
        "_payment_terms_digest",
        "_proof",
    )

    def __init__(
        self,
        request: PaidExecutionRequest,
        *,
        body_digest: str,
        payment_terms_digest: str | None,
        proof: object,
    ) -> None:
        if proof is not _TOKEN_PROOF:
            raise TypeError("consumed authorization tokens cannot be constructed directly")
        self.run_id = request.run_id
        self.target = request.target
        self._method = request.method
        self._body_digest = body_digest
        self._budget = request.budget
        self._payment_terms_digest = payment_terms_digest
        self._proof = proof

    def require_exact_request(self, request: PaidExecutionRequest) -> None:
        """Reject a request that differs from the capability already consumed."""
        if self._proof is not _TOKEN_PROOF:
            raise AuthorizationError("authorization token is invalid")
        if request.run_id != self.run_id:
            raise AuthorizationError("authorization does not cover this run")
        if request.target != self.target:
            raise AuthorizationError("authorization does not cover this target")
        if request.method != self._method:
            raise AuthorizationError("authorization does not cover this request method")
        if PaidExecutionCapability.body_digest_for(request.body) != self._body_digest:
            raise AuthorizationError("authorization does not cover this request body")
        if request.budget != self._budget:
            raise AuthorizationError("authorization does not cover this exact budget")

    def require_exact_payment_terms(self, payment_terms: PaymentTerms) -> None:
        if self._proof is not _TOKEN_PROOF:
            raise AuthorizationError("authorization token is invalid")
        if payment_terms.digest != self._payment_terms_digest:
            raise AuthorizationError("authorization does not cover these exact payment terms")


_TOKEN_PROOF = object()


class PaidExecutionCapability:
    """Mutable one-shot state kept behind an async lock."""

    def __init__(
        self,
        request: PaidExecutionRequest,
        *,
        payment_terms: PaymentTerms | None,
        expires_at: datetime,
    ) -> None:
        expiry_offset = expires_at.utcoffset()
        if expiry_offset is None or expiry_offset.total_seconds() != 0:
            raise ValueError("capability expiry must be timezone-aware UTC")
        self._run_id = request.run_id
        self._target = request.target
        self._method = request.method
        self._body_digest = self.body_digest_for(request.body)
        self._budget = request.budget
        if payment_terms is not None and (
            payment_terms.resource_url != request.target
            or payment_terms.method != request.method
            or payment_terms.body_digest != self._body_digest
            or payment_terms.quoted_price.unit != request.budget.unit
            or not payment_terms.quoted_price.is_within(request.budget)
        ):
            raise AuthorizationError("payment terms do not match the request being authorized")
        self._payment_terms_digest = payment_terms.digest if payment_terms is not None else None
        self._expires_at = expires_at.astimezone(UTC)
        self._consumed = False
        self._lock = asyncio.Lock()

    @classmethod
    def issue(
        cls,
        request: PaidExecutionRequest,
        *,
        expires_at: datetime,
        payment_terms: PaymentTerms | None = None,
    ) -> PaidExecutionCapability:
        return cls(request, payment_terms=payment_terms, expires_at=expires_at)

    @property
    def body_digest(self) -> str:
        return self._body_digest

    @property
    def payment_terms_digest(self) -> str | None:
        return self._payment_terms_digest

    @staticmethod
    def body_digest_for(body: JsonValue | None) -> str:
        canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return hashlib.sha256(canonical.encode()).hexdigest()

    async def consume(
        self,
        request: PaidExecutionRequest,
        *,
        payment_terms: PaymentTerms | None = None,
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
            if request.method != self._method:
                raise AuthorizationError("authorization does not cover this request method")
            if self.body_digest_for(request.body) != self._body_digest:
                raise AuthorizationError("authorization does not cover this request body")
            if request.budget != self._budget:
                raise AuthorizationError("authorization does not cover this exact budget")
            payment_terms_digest = payment_terms.digest if payment_terms is not None else None
            if payment_terms_digest != self._payment_terms_digest:
                raise AuthorizationError("authorization does not cover these exact payment terms")

            self._consumed = True
            return ConsumedPaidAuthorization(
                request,
                body_digest=self._body_digest,
                payment_terms_digest=self._payment_terms_digest,
                proof=_TOKEN_PROOF,
            )
