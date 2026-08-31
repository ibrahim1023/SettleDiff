"""Strict external x402 v2 header models."""

from __future__ import annotations

from typing import Annotated, Literal, Self
from urllib.parse import urlsplit

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    StringConstraints,
    field_validator,
    model_validator,
)

from settlediff.domain.models import Caip2Network, NonEmptyStr

EvmAddress = Annotated[str, StringConstraints(pattern=r"^0x[0-9a-fA-F]{40}$")]
TransactionHash = Annotated[str, StringConstraints(pattern=r"^0x[0-9a-fA-F]{64}$")]
AtomicAmount = Annotated[str, StringConstraints(pattern=r"^[0-9]+$")]


class X402ExternalModel(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True, populate_by_name=True)


class ResourceInfo(X402ExternalModel):
    url: NonEmptyStr
    description: str | None = None
    mime_type: str | None = Field(default=None, alias="mimeType")
    service_name: str | None = Field(default=None, alias="serviceName")
    tags: tuple[str, ...] | None = None
    icon_url: str | None = Field(default=None, alias="iconUrl")

    @field_validator("url")
    @classmethod
    def require_absolute_http_url(cls, value: str) -> str:
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username:
            raise ValueError("resource url must be absolute HTTP(S) without user information")
        return value


class ExactEvmExtra(BaseModel):
    model_config = ConfigDict(strict=True, extra="allow", frozen=True, populate_by_name=True)

    asset_transfer_method: Literal["eip3009"] = Field(
        default="eip3009", alias="assetTransferMethod"
    )
    name: NonEmptyStr
    version: NonEmptyStr
    payment_flow: str | None = Field(default=None, alias="paymentFlow")


class PaymentRequirements(X402ExternalModel):
    scheme: Literal["exact"]
    network: Literal["eip155:84532"]
    amount: AtomicAmount
    asset: EvmAddress
    pay_to: EvmAddress = Field(alias="payTo")
    max_timeout_seconds: int = Field(alias="maxTimeoutSeconds", gt=0, le=86_400)
    extra: ExactEvmExtra

    @field_validator("amount")
    @classmethod
    def require_positive_amount(cls, value: str) -> str:
        if int(value) <= 0:
            raise ValueError("amount must be positive")
        return value


class PaymentRequired(X402ExternalModel):
    x402_version: Literal[2] = Field(alias="x402Version")
    error: str | None = None
    resource: ResourceInfo
    accepts: tuple[PaymentRequirements, ...] = Field(min_length=1)
    extensions: dict[str, JsonValue] = Field(default_factory=dict)


class SettlementResponse(X402ExternalModel):
    success: bool
    error_reason: str | None = Field(default=None, alias="errorReason")
    payer: EvmAddress | None = None
    transaction: str
    network: Caip2Network
    amount: AtomicAmount | None = None
    extensions: dict[str, JsonValue] = Field(default_factory=dict)

    @model_validator(mode="after")
    def require_coherent_outcome(self) -> Self:
        if self.network != "eip155:84532":
            raise ValueError("unsupported settlement network")
        if self.success:
            if self.error_reason is not None:
                raise ValueError("successful settlement cannot include errorReason")
            if not _is_transaction_hash(self.transaction):
                raise ValueError("successful settlement requires a transaction hash")
            return self
        if self.error_reason is None:
            raise ValueError("failed settlement requires errorReason")
        if self.error_reason == "settlement_pending":
            if not _is_transaction_hash(self.transaction):
                raise ValueError("pending settlement requires a transaction hash")
        elif self.transaction and not _is_transaction_hash(self.transaction):
            raise ValueError("settlement transaction must be empty or a transaction hash")
        return self


def _is_transaction_hash(value: str) -> bool:
    return (
        len(value) == 66
        and value.startswith("0x")
        and all(character in "0123456789abcdefABCDEF" for character in value[2:])
    )
