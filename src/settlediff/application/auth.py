"""One-use authorization for one exact paid execution."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from settlediff.domain.integrity import Sha256Digest, sha256_digest
from settlediff.domain.models import AssetIdentity, Caip2Network, NonEmptyStr
from settlediff.domain.money import Money


class AuthorizationError(ValueError):
    """A paid request is not covered by its capability."""


class PaymentTerms(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    schema_version: Literal[1, 2, 3] = 1
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
    resource_url: NonEmptyStr | None = None
    method: Literal["GET", "POST"] | None = None
    body_digest: Sha256Digest | None = None
    response_contract_digest: Sha256Digest | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    resource_digest: Sha256Digest | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    contract_digest: Sha256Digest | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    maximum_charge: Money | None = Field(default=None, exclude_if=lambda value: value is None)
    required_max_charge: Money | None = Field(default=None, exclude_if=lambda value: value is None)

    @property
    def digest(self) -> Sha256Digest:
        return sha256_digest(self.model_dump(mode="json"))

    @model_validator(mode="after")
    def require_consistent_asset(self) -> Self:
        catalog_fields = {
            "resource_digest",
            "contract_digest",
            "maximum_charge",
            "required_max_charge",
        }
        http_fields = {"resource_url", "method", "body_digest"}
        if self.schema_version <= 2:
            if catalog_fields & self.model_fields_set:
                raise ValueError("catalog payment terms fields require schema version 3")
            if self.resource_url is None or self.method is None or self.body_digest is None:
                raise ValueError("HTTP payment terms require resource_url, method, and body_digest")
        else:
            if http_fields & self.model_fields_set:
                raise ValueError("catalog payment terms cannot contain HTTP resource fields")
            if "response_contract_digest" in self.model_fields_set:
                raise ValueError("catalog payment terms cannot contain response_contract_digest")
            if (
                self.resource_digest is None
                or self.contract_digest is None
                or self.maximum_charge is None
                or self.required_max_charge is None
            ):
                raise ValueError(
                    "catalog payment terms require resource_digest, "
                    "contract_digest, maximum_charge, and required_max_charge"
                )
            assert self.maximum_charge is not None
            assert self.required_max_charge is not None
            if self.maximum_charge.amount <= 0:
                raise ValueError("payment terms maximum charge must be positive")
            if self.required_max_charge.amount <= 0:
                raise ValueError("payment terms required max charge must be positive")
            if (
                self.required_max_charge.unit != self.quoted_price.unit
                or self.maximum_charge.unit != self.quoted_price.unit
            ):
                raise ValueError("payment terms charge units must match the quote unit")
            if not self.quoted_price.is_within(self.required_max_charge):
                raise ValueError("payment terms quote exceeds the required max charge")
            if not self.required_max_charge.is_within(self.maximum_charge):
                raise ValueError("payment terms required max charge exceeds the maximum charge")
        if self.schema_version == 1 and "response_contract_digest" in self.model_fields_set:
            raise ValueError("schema version 1 cannot contain response_contract_digest")
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


class HttpResourceReference(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    kind: Literal["http"] = "http"
    url: NonEmptyStr
    method: Literal["GET", "POST"]
    body: JsonValue | None

    @model_validator(mode="after")
    def require_method_body(self) -> Self:
        if self.method == "GET" and self.body is not None:
            raise ValueError("GET resources cannot contain a request body")
        return self


class CatalogResourceReference(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    kind: Literal["catalog"] = "catalog"
    slug: NonEmptyStr
    input: dict[str, JsonValue]
    query: dict[str, JsonValue]
    sub_account: NonEmptyStr | None = None


ResourceReference = Annotated[
    HttpResourceReference | CatalogResourceReference,
    Field(discriminator="kind"),
]


class PaidExecutionRequest(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    run_id: NonEmptyStr
    resource: ResourceReference
    budget: Money

    @property
    def target(self) -> str:
        return (
            self.resource.url
            if isinstance(self.resource, HttpResourceReference)
            else self.resource.slug
        )

    @property
    def method(self) -> Literal["GET", "POST"]:
        return self.resource.method if isinstance(self.resource, HttpResourceReference) else "POST"

    @property
    def body(self) -> JsonValue | None:
        return (
            self.resource.body
            if isinstance(self.resource, HttpResourceReference)
            else self.resource.input
        )

    @property
    def resource_digest(self) -> Sha256Digest:
        return sha256_digest(self.resource.model_dump(mode="json"))


class ConsumedPaidAuthorization:
    """Opaque proof that the exact capability was consumed before execution."""

    __slots__ = (
        "run_id",
        "target",
        "_resource_digest",
        "_budget",
        "_payment_terms_digest",
        "_proof",
    )

    def __init__(
        self,
        request: PaidExecutionRequest,
        *,
        payment_terms_digest: str | None,
        proof: object,
    ) -> None:
        if proof is not _TOKEN_PROOF:
            raise TypeError("consumed authorization tokens cannot be constructed directly")
        self.run_id = request.run_id
        self.target = request.target
        self._resource_digest = request.resource_digest
        self._budget = request.budget
        self._payment_terms_digest = payment_terms_digest
        self._proof = proof

    def require_exact_request(self, request: PaidExecutionRequest) -> None:
        """Reject a request that differs from the capability already consumed."""
        if self._proof is not _TOKEN_PROOF:
            raise AuthorizationError("authorization token is invalid")
        if request.run_id != self.run_id:
            raise AuthorizationError("authorization does not cover this run")
        if request.resource_digest != self._resource_digest:
            raise AuthorizationError("authorization does not cover this exact resource")
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
        self._resource_digest = request.resource_digest
        self._body_digest = self.body_digest_for(request.body)
        self._budget = request.budget
        if payment_terms is not None:
            if payment_terms.schema_version <= 2:
                terms_match = (
                    payment_terms.resource_url == request.target
                    and payment_terms.method == request.method
                    and payment_terms.body_digest == self._body_digest
                    and payment_terms.quoted_price.unit == request.budget.unit
                    and payment_terms.quoted_price.is_within(request.budget)
                )
            else:
                terms_match = (
                    isinstance(request.resource, CatalogResourceReference)
                    and payment_terms.resource_digest == request.resource_digest
                    and payment_terms.maximum_charge == request.budget
                    and payment_terms.quoted_price.unit == request.budget.unit
                    and payment_terms.quoted_price.is_within(request.budget)
                )
            if not terms_match:
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
    def body_digest_for(body: JsonValue | None) -> Sha256Digest:
        return sha256_digest(body)

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
            if checked_at >= self._expires_at:
                raise AuthorizationError("authorization expired")
            if request.run_id != self._run_id:
                raise AuthorizationError("authorization does not cover this run")
            if request.resource_digest != self._resource_digest:
                raise AuthorizationError("authorization does not cover this exact resource")
            if request.budget != self._budget:
                raise AuthorizationError("authorization does not cover this exact budget")
            payment_terms_digest = payment_terms.digest if payment_terms is not None else None
            if payment_terms_digest != self._payment_terms_digest:
                raise AuthorizationError("authorization does not cover these exact payment terms")

            self._consumed = True
            return ConsumedPaidAuthorization(
                request,
                payment_terms_digest=self._payment_terms_digest,
                proof=_TOKEN_PROOF,
            )
