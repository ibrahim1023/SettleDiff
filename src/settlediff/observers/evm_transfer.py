"""Deterministic observation of exact ERC-20 settlement transfers."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Protocol, cast

from pydantic import JsonValue

from settlediff.domain.models import (
    IndependentSettlementObservation,
    IndependentSettlementStatus,
    LedgerRecord,
    LedgerStatus,
    SettlementProfile,
    SettlementVerificationDimensions,
)
from settlediff.domain.money import Money
from settlediff.observers.evm_rpc import EvmRpcError, EvmRpcProtocolError

TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"


class ReadOnlyRpcPort(Protocol):
    async def call(self, method: str, params: tuple[JsonValue, ...]) -> JsonValue: ...


def unavailable_settlement(
    diagnostic: str,
    *,
    source: str,
    observed_at: datetime,
    profile: SettlementProfile | None = None,
    transaction_reference: str | None = None,
) -> IndependentSettlementObservation:
    return IndependentSettlementObservation(
        status=IndependentSettlementStatus.UNAVAILABLE,
        diagnostic=diagnostic,
        source=source,
        observed_at=observed_at,
        transaction_reference=transaction_reference,
        profile=profile,
        dimensions=_dimensions(),
        ledger=None,
    )


async def observe_exact_erc20_transfer(
    rpc: ReadOnlyRpcPort,
    profile: SettlementProfile,
    transaction_reference: str,
    *,
    source: str,
    protocol: str,
    scheme: str | None,
    observed_at: datetime,
) -> IndependentSettlementObservation:
    if not _is_prefixed_hex(transaction_reference, 64):
        return _observation(
            IndependentSettlementStatus.INDETERMINATE,
            "INVALID_TRANSACTION_REFERENCE",
            source=source,
            observed_at=observed_at,
            profile=profile,
            transaction_reference=transaction_reference,
        )
    if not all(
        _is_prefixed_hex(value, 40)
        for value in (
            profile.payer,
            profile.recipient,
            profile.asset_identity.reference,
        )
    ):
        return _observation(
            IndependentSettlementStatus.INDETERMINATE,
            "UNSUPPORTED_SETTLEMENT_PROFILE",
            source=source,
            observed_at=observed_at,
            profile=profile,
            transaction_reference=transaction_reference,
        )
    try:
        chain_id = await rpc.call("eth_chainId", ())
    except EvmRpcProtocolError:
        return _observation(
            IndependentSettlementStatus.INDETERMINATE,
            "MALFORMED_RPC_RESPONSE",
            source=source,
            observed_at=observed_at,
            profile=profile,
            transaction_reference=transaction_reference,
        )
    except EvmRpcError:
        return unavailable_settlement(
            "OBSERVER_UNAVAILABLE",
            source=source,
            observed_at=observed_at,
            profile=profile,
            transaction_reference=transaction_reference,
        )
    if not isinstance(chain_id, str) or chain_id != hex(profile.chain_id):
        return _observation(
            IndependentSettlementStatus.INDETERMINATE,
            "CHAIN_MISMATCH",
            source=source,
            observed_at=observed_at,
            profile=profile,
            transaction_reference=transaction_reference,
        )
    try:
        receipt_value = await rpc.call("eth_getTransactionReceipt", (transaction_reference,))
    except EvmRpcProtocolError:
        return _observation(
            IndependentSettlementStatus.INDETERMINATE,
            "MALFORMED_RPC_RESPONSE",
            source=source,
            observed_at=observed_at,
            profile=profile,
            transaction_reference=transaction_reference,
        )
    except EvmRpcError:
        return unavailable_settlement(
            "OBSERVER_UNAVAILABLE",
            source=source,
            observed_at=observed_at,
            profile=profile,
            transaction_reference=transaction_reference,
        )
    if receipt_value is None:
        return _observation(
            IndependentSettlementStatus.INDETERMINATE,
            "RECEIPT_PENDING",
            source=source,
            observed_at=observed_at,
            profile=profile,
            transaction_reference=transaction_reference,
            dimensions=_dimensions(chain_verified=True),
        )
    if not isinstance(receipt_value, dict):
        return _malformed_receipt(source, observed_at, profile, transaction_reference)
    receipt = cast(dict[str, JsonValue], receipt_value)
    receipt_hash = receipt.get("transactionHash")
    if (
        not isinstance(receipt_hash, str)
        or receipt_hash.casefold() != transaction_reference.casefold()
    ):
        return _malformed_receipt(source, observed_at, profile, transaction_reference)
    status = receipt.get("status")
    if not isinstance(status, str) or status not in {"0x0", "0x1"}:
        return _malformed_receipt(source, observed_at, profile, transaction_reference)
    if status == "0x0":
        ledger = LedgerRecord(
            ledger_id=f"{protocol}:{transaction_reference}",
            vendor_slug=None,
            amount=None,
            asset=None,
            protocol=protocol,
            chain=None,
            recipient=None,
            scheme=scheme,
            network=profile.network,
            asset_identity=None,
            status=LedgerStatus.FAILED,
            error_reason="transaction reverted",
            transaction_id=None,
            session_id=None,
            transaction_hash=transaction_reference,
            occurred_at=observed_at,
        )
        return _observation(
            IndependentSettlementStatus.FAILED,
            "RECEIPT_REVERTED",
            source=source,
            observed_at=observed_at,
            profile=profile,
            transaction_reference=transaction_reference,
            dimensions=_dimensions(chain_verified=True),
            ledger=ledger,
        )
    logs_value = receipt.get("logs")
    if not isinstance(logs_value, list):
        return _malformed_receipt(source, observed_at, profile, transaction_reference)
    candidates: list[dict[str, JsonValue]] = []
    for log_value in cast(list[JsonValue], logs_value):
        if not isinstance(log_value, dict):
            continue
        log = cast(dict[str, JsonValue], log_value)
        topics_value = log.get("topics")
        if not isinstance(topics_value, list) or not topics_value:
            continue
        topics = cast(list[JsonValue], topics_value)
        topic = topics[0]
        if not isinstance(topic, str) or topic.casefold() != TRANSFER_TOPIC:
            continue
        address = log.get("address")
        if (
            not isinstance(address, str)
            or address.casefold() != profile.asset_identity.reference.casefold()
        ):
            continue
        if len(topics) != 3:
            return _malformed_receipt(source, observed_at, profile, transaction_reference)
        candidates.append(log)
    if len(candidates) != 1:
        return _observation(
            IndependentSettlementStatus.INDETERMINATE,
            "TRANSFER_MISMATCH",
            source=source,
            observed_at=observed_at,
            profile=profile,
            transaction_reference=transaction_reference,
            dimensions=_dimensions(chain_verified=True),
        )
    transfer = candidates[0]
    topics = cast(list[JsonValue], transfer["topics"])
    payer = _topic_address(topics[1])
    recipient = _topic_address(topics[2])
    amount = _uint256(transfer.get("data"))
    if payer is None or recipient is None or amount is None:
        return _malformed_receipt(source, observed_at, profile, transaction_reference)
    dimensions = _dimensions(
        chain_verified=True,
        asset_verified=True,
        amount_verified=amount == profile.atomic_amount,
        recipient_verified=recipient.casefold() == profile.recipient.casefold(),
        payer_verified=payer.casefold() == profile.payer.casefold(),
    )
    if not all(
        (
            dimensions.amount_verified,
            dimensions.recipient_verified,
            dimensions.payer_verified,
        )
    ):
        return _observation(
            IndependentSettlementStatus.INDETERMINATE,
            "TRANSFER_MISMATCH",
            source=source,
            observed_at=observed_at,
            profile=profile,
            transaction_reference=transaction_reference,
            dimensions=dimensions,
        )
    ledger = LedgerRecord(
        ledger_id=f"{protocol}:{transaction_reference}",
        vendor_slug=None,
        amount=Money(
            amount=Decimal(amount),
            unit=profile.asset_identity.symbol,
            minor_units=profile.asset_identity.decimals,
        ),
        asset=profile.asset_identity.symbol,
        protocol=protocol,
        chain=None,
        recipient=recipient,
        scheme=scheme,
        network=profile.network,
        asset_identity=profile.asset_identity,
        status=LedgerStatus.CONFIRMED,
        error_reason=None,
        transaction_id=None,
        session_id=None,
        transaction_hash=transaction_reference,
        occurred_at=observed_at,
    )
    return _observation(
        IndependentSettlementStatus.CONFIRMED,
        "EXACT_TRANSFER_CONFIRMED",
        source=source,
        observed_at=observed_at,
        profile=profile,
        transaction_reference=transaction_reference,
        dimensions=dimensions,
        ledger=ledger,
    )


def _observation(
    status: IndependentSettlementStatus,
    diagnostic: str,
    *,
    source: str,
    observed_at: datetime,
    profile: SettlementProfile,
    transaction_reference: str,
    dimensions: SettlementVerificationDimensions | None = None,
    ledger: LedgerRecord | None = None,
) -> IndependentSettlementObservation:
    return IndependentSettlementObservation(
        status=status,
        diagnostic=diagnostic,
        source=source,
        observed_at=observed_at,
        transaction_reference=transaction_reference,
        profile=profile,
        dimensions=dimensions or _dimensions(),
        ledger=ledger,
    )


def _malformed_receipt(
    source: str,
    observed_at: datetime,
    profile: SettlementProfile,
    transaction_reference: str,
) -> IndependentSettlementObservation:
    return _observation(
        IndependentSettlementStatus.INDETERMINATE,
        "MALFORMED_RECEIPT",
        source=source,
        observed_at=observed_at,
        profile=profile,
        transaction_reference=transaction_reference,
        dimensions=_dimensions(chain_verified=True),
    )


def _dimensions(
    *,
    chain_verified: bool = False,
    asset_verified: bool = False,
    amount_verified: bool = False,
    recipient_verified: bool = False,
    payer_verified: bool = False,
) -> SettlementVerificationDimensions:
    return SettlementVerificationDimensions(
        chain_verified=chain_verified,
        asset_verified=asset_verified,
        amount_verified=amount_verified,
        recipient_verified=recipient_verified,
        payer_verified=payer_verified,
    )


def _is_prefixed_hex(value: str, digits: int) -> bool:
    return (
        len(value) == digits + 2
        and value.startswith("0x")
        and all(character in "0123456789abcdefABCDEF" for character in value[2:])
    )


def _topic_address(value: JsonValue) -> str | None:
    if not isinstance(value, str) or not _is_prefixed_hex(value, 64):
        return None
    return "0x" + value[-40:]


def _uint256(value: JsonValue | None) -> int | None:
    if not isinstance(value, str) or not _is_prefixed_hex(value, 64):
        return None
    return int(value, 16)
