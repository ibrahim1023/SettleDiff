"""Deterministic verification of x402 exact-USDC settlement on Base Sepolia."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Protocol, cast

from pydantic import JsonValue

from settlediff.domain.models import AssetIdentity, LedgerRecord, LedgerStatus
from settlediff.domain.money import Money
from settlediff.x402.models import PaymentRequirements
from settlediff.x402.normalize import BASE_SEPOLIA, BASE_SEPOLIA_USDC, USDC_DECIMALS

TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
_BASE_SEPOLIA_CHAIN_ID = "0x14a34"


class X402SettlementError(ValueError):
    pass


class ReadOnlyRpcPort(Protocol):
    async def call(self, method: str, params: tuple[JsonValue, ...]) -> JsonValue: ...


async def verify_exact_usdc_settlement(
    rpc: ReadOnlyRpcPort,
    transaction_hash: str,
    requirement: PaymentRequirements,
    *,
    expected_payer: str | None,
    observed_at: datetime,
) -> LedgerRecord | None:
    if not _is_prefixed_hex(transaction_hash, 64):
        raise X402SettlementError("transaction hash is malformed")
    if (
        requirement.network != BASE_SEPOLIA
        or requirement.asset.casefold() != BASE_SEPOLIA_USDC.casefold()
    ):
        raise X402SettlementError("unsupported x402 settlement asset")
    if expected_payer is not None and not _is_prefixed_hex(expected_payer, 40):
        raise X402SettlementError("expected payer is malformed")
    chain_id = await rpc.call("eth_chainId", ())
    if chain_id != _BASE_SEPOLIA_CHAIN_ID:
        raise X402SettlementError("RPC chain does not match Base Sepolia")
    receipt_value = await rpc.call("eth_getTransactionReceipt", (transaction_hash,))
    if receipt_value is None:
        return None
    if not isinstance(receipt_value, dict):
        raise X402SettlementError("transaction receipt must be an object")
    receipt = cast(dict[str, JsonValue], receipt_value)
    receipt_hash = receipt.get("transactionHash")
    if not isinstance(receipt_hash, str) or receipt_hash.casefold() != transaction_hash.casefold():
        raise X402SettlementError("transaction receipt hash does not match the reference")
    status = receipt.get("status")
    if status == "0x0":
        return LedgerRecord(
            ledger_id=f"x402:{transaction_hash}",
            vendor_slug=None,
            amount=None,
            asset=None,
            protocol="x402",
            chain=None,
            recipient=None,
            scheme=requirement.scheme,
            network=requirement.network,
            asset_identity=None,
            status=LedgerStatus.FAILED,
            error_reason="transaction reverted",
            transaction_id=None,
            session_id=None,
            transaction_hash=transaction_hash,
            occurred_at=observed_at,
        )
    if status != "0x1":
        raise X402SettlementError("transaction receipt status is malformed")
    logs_value = receipt.get("logs")
    if not isinstance(logs_value, list):
        raise X402SettlementError("transaction receipt logs must be an array")
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
        log_address = log.get("address")
        if (
            not isinstance(log_address, str)
            or log_address.casefold() != requirement.asset.casefold()
        ):
            continue
        if len(topics) != 3:
            raise X402SettlementError("USDC Transfer topics are malformed")
        candidates.append(log)
    if len(candidates) != 1:
        raise X402SettlementError("expected exactly one matching USDC transfer")
    transfer = candidates[0]
    topics = cast(list[JsonValue], transfer["topics"])
    payer = _topic_address(topics[1], "payer")
    recipient = _topic_address(topics[2], "recipient")
    if expected_payer is not None and payer.casefold() != expected_payer.casefold():
        raise X402SettlementError("USDC transfer payer does not match provider evidence")
    if recipient.casefold() != requirement.pay_to.casefold():
        raise X402SettlementError("USDC transfer recipient does not match payment terms")
    amount = _uint256(transfer.get("data"))
    if amount != int(requirement.amount):
        raise X402SettlementError("USDC transfer amount does not match payment terms")
    identity = AssetIdentity(
        symbol="USDC",
        network=BASE_SEPOLIA,
        reference=BASE_SEPOLIA_USDC,
        decimals=USDC_DECIMALS,
    )
    return LedgerRecord(
        ledger_id=f"x402:{transaction_hash}",
        vendor_slug=None,
        amount=Money(amount=Decimal(amount), unit="USDC", minor_units=USDC_DECIMALS),
        asset="USDC",
        protocol="x402",
        chain=None,
        recipient=recipient,
        scheme=requirement.scheme,
        network=requirement.network,
        asset_identity=identity,
        status=LedgerStatus.CONFIRMED,
        error_reason=None,
        transaction_id=None,
        session_id=None,
        transaction_hash=transaction_hash,
        occurred_at=observed_at,
    )


def _is_prefixed_hex(value: str, digits: int) -> bool:
    return (
        len(value) == digits + 2
        and value.startswith("0x")
        and all(character in "0123456789abcdefABCDEF" for character in value[2:])
    )


def _topic_address(value: JsonValue, field: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 66
        or not value.startswith("0x")
        or any(character not in "0123456789abcdefABCDEF" for character in value[2:])
    ):
        raise X402SettlementError(f"USDC Transfer {field} topic is malformed")
    return "0x" + value[-40:]


def _uint256(value: JsonValue | None) -> int:
    if (
        not isinstance(value, str)
        or len(value) != 66
        or not value.startswith("0x")
        or any(character not in "0123456789abcdefABCDEF" for character in value[2:])
    ):
        raise X402SettlementError("USDC Transfer amount data is malformed")
    return int(value, 16)
