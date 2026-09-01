"""Explicit x402 v2 mappings into rail-neutral canonical evidence."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import JsonValue

from settlediff.domain.models import (
    AssetIdentity,
    ExpectedContract,
    PaymentReceipt,
    SettlementStatus,
)
from settlediff.domain.money import Money
from settlediff.x402.models import PaymentRequired, PaymentRequirements, SettlementResponse

BASE_SEPOLIA = "eip155:84532"
BASE_SEPOLIA_USDC = "0x036CbD53842c5426634e7929541eC2318f3dCF7e"
USDC_DECIMALS = 6


class X402NormalizationError(ValueError):
    pass


def normalize_payment_required(
    required: PaymentRequired,
    *,
    request_schema: dict[str, JsonValue],
    selected: int = 0,
) -> ExpectedContract:
    try:
        requirement = required.accepts[selected]
    except IndexError as error:
        raise X402NormalizationError("selected payment requirement is unavailable") from error
    identity = _asset_identity(requirement)
    notes = (
        ("x402 exact EVM defaulted assetTransferMethod to eip3009",)
        if "asset_transfer_method" not in requirement.extra.model_fields_set
        else ()
    )
    return ExpectedContract(
        vendor_slug=None,
        url=required.resource.url,
        price=_atomic_money(requirement.amount, identity),
        asset=identity.symbol,
        protocol="x402",
        chain=None,
        request_schema=request_schema,
        scheme=requirement.scheme,
        network=requirement.network,
        asset_identity=identity,
        recipient=requirement.pay_to,
        max_timeout_seconds=requirement.max_timeout_seconds,
        normalization_notes=notes,
    )


def normalize_payment_response(
    response: SettlementResponse,
    requirement: PaymentRequirements,
    *,
    issued_at: datetime,
) -> PaymentReceipt:
    identity = _asset_identity(requirement)
    amount = _atomic_money(response.amount, identity) if response.amount is not None else None
    if response.success:
        status = SettlementStatus.SETTLED
    elif response.error_reason == "settlement_pending":
        status = SettlementStatus.PENDING
    else:
        status = SettlementStatus.FAILED
    notes = (
        ("provider payer is present but unavailable in canonical receipt schema",)
        if response.payer is not None
        else ()
    )
    return PaymentReceipt(
        amount=amount,
        asset=identity.symbol if amount is not None else None,
        protocol="x402",
        chain=None,
        recipient=None,
        scheme=requirement.scheme,
        network=response.network,
        asset_identity=identity if amount is not None else None,
        settlement_status=status,
        transaction_id=None,
        session_id=None,
        transaction_hash=response.transaction or None,
        issued_at=issued_at,
        normalization_notes=notes,
    )


def _asset_identity(requirement: PaymentRequirements) -> AssetIdentity:
    if (
        requirement.network != BASE_SEPOLIA
        or requirement.asset.casefold() != BASE_SEPOLIA_USDC.casefold()
    ):
        raise X402NormalizationError("unsupported x402 asset identity")
    return AssetIdentity(
        symbol="USDC",
        network=BASE_SEPOLIA,
        reference=BASE_SEPOLIA_USDC,
        decimals=USDC_DECIMALS,
    )


def _atomic_money(amount: str, identity: AssetIdentity) -> Money:
    return Money(
        amount=Decimal(amount),
        unit=identity.symbol,
        minor_units=identity.decimals,
    )
