from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from decimal import Decimal
from typing import cast

import pytest
from pydantic import JsonValue

from settlediff.domain.models import (
    AssetIdentity,
    IndependentSettlementStatus,
    LedgerStatus,
    SettlementProfile,
)
from settlediff.domain.money import Money
from settlediff.observers.evm_rpc import EvmRpcError, EvmRpcProtocolError
from settlediff.observers.evm_transfer import (
    TRANSFER_TOPIC,
    observe_exact_erc20_transfer,
    unavailable_settlement,
)

NOW = datetime(2026, 9, 24, tzinfo=UTC)
TX_HASH = "0x" + "2" * 64
ASSET = "0x036CbD53842c5426634e7929541eC2318f3dCF7e"
PAYER = "0x3333333333333333333333333333333333333333"
RECIPIENT = "0x1111111111111111111111111111111111111111"
SOURCE = "evm_rpc:eip155:84532"


def profile(**updates: object) -> SettlementProfile:
    values: dict[str, object] = {
        "network": "eip155:84532",
        "chain_id": 84532,
        "asset_identity": AssetIdentity(
            symbol="USDC",
            network="eip155:84532",
            reference=ASSET,
            decimals=6,
        ),
        "atomic_amount": 1000,
        "recipient": RECIPIENT,
        "payer": PAYER,
    }
    return SettlementProfile.model_validate(values | updates)


def address_topic(address: str) -> str:
    return "0x" + "0" * 24 + address[2:].lower()


def receipt() -> dict[str, JsonValue]:
    return {
        "transactionHash": TX_HASH,
        "status": "0x1",
        "from": "0x5555555555555555555555555555555555555555",
        "logs": [
            {
                "address": ASSET,
                "topics": [
                    TRANSFER_TOPIC,
                    address_topic(PAYER),
                    address_topic(RECIPIENT),
                ],
                "data": "0x" + (1000).to_bytes(32, "big").hex(),
            }
        ],
    }


class FakeRpc:
    def __init__(
        self,
        transaction_receipt: JsonValue,
        *,
        chain_id: JsonValue = "0x14a34",
        error_on: str | None = None,
        error: EvmRpcError | None = None,
    ) -> None:
        self.transaction_receipt = transaction_receipt
        self.chain_id = chain_id
        self.error_on = error_on
        self.error = error
        self.calls: list[tuple[str, tuple[JsonValue, ...]]] = []

    async def call(self, method: str, params: tuple[JsonValue, ...]) -> JsonValue:
        self.calls.append((method, params))
        if method == self.error_on and self.error is not None:
            raise self.error
        if method == "eth_chainId":
            return self.chain_id
        if method == "eth_getTransactionReceipt":
            return self.transaction_receipt
        raise AssertionError(method)


async def observe(rpc: FakeRpc, value: SettlementProfile | None = None, tx: str = TX_HASH):
    return await observe_exact_erc20_transfer(
        rpc,
        value or profile(),
        tx,
        source=SOURCE,
        protocol="x402",
        scheme="exact",
        observed_at=NOW,
    )


@pytest.mark.asyncio
async def test_exact_transfer_is_confirmed_with_coherent_ledger() -> None:
    rpc = FakeRpc(receipt())

    observation = await observe(rpc)

    assert observation.status is IndependentSettlementStatus.CONFIRMED
    assert observation.diagnostic == "EXACT_TRANSFER_CONFIRMED"
    assert observation.source == SOURCE
    assert observation.observed_at == NOW
    assert observation.transaction_reference == TX_HASH
    assert observation.profile == profile()
    assert observation.dimensions.chain_verified is True
    assert observation.dimensions.asset_verified is True
    assert observation.dimensions.amount_verified is True
    assert observation.dimensions.recipient_verified is True
    assert observation.dimensions.payer_verified is True
    ledger = observation.ledger
    assert ledger is not None
    assert ledger.ledger_id == f"x402:{TX_HASH}"
    assert ledger.amount == Money(amount=Decimal(1000), unit="USDC", minor_units=6)
    assert ledger.asset == "USDC"
    assert ledger.protocol == "x402"
    assert ledger.scheme == "exact"
    assert ledger.network == "eip155:84532"
    assert ledger.asset_identity == profile().asset_identity
    assert ledger.recipient == RECIPIENT
    assert ledger.status is LedgerStatus.CONFIRMED
    assert ledger.error_reason is None
    assert ledger.transaction_hash == TX_HASH
    assert ledger.occurred_at == NOW
    assert rpc.calls == [
        ("eth_chainId", ()),
        ("eth_getTransactionReceipt", (TX_HASH,)),
    ]


@pytest.mark.asyncio
async def test_reverted_receipt_is_failed() -> None:
    value = receipt()
    value["status"] = "0x0"
    value["logs"] = []

    observation = await observe(FakeRpc(value))

    assert observation.status is IndependentSettlementStatus.FAILED
    assert observation.diagnostic == "RECEIPT_REVERTED"
    assert observation.dimensions.chain_verified is True
    assert observation.dimensions.asset_verified is False
    assert observation.dimensions.amount_verified is False
    assert observation.dimensions.recipient_verified is False
    assert observation.dimensions.payer_verified is False
    ledger = observation.ledger
    assert ledger is not None
    assert ledger.status is LedgerStatus.FAILED
    assert ledger.ledger_id == f"x402:{TX_HASH}"
    assert ledger.amount is None
    assert ledger.asset is None
    assert ledger.recipient is None
    assert ledger.asset_identity is None
    assert ledger.error_reason == "transaction reverted"


@pytest.mark.asyncio
async def test_null_receipt_is_pending() -> None:
    observation = await observe(FakeRpc(None))

    assert observation.status is IndependentSettlementStatus.INDETERMINATE
    assert observation.diagnostic == "RECEIPT_PENDING"
    assert observation.dimensions.chain_verified is True
    assert observation.dimensions.asset_verified is False
    assert observation.ledger is None


@pytest.mark.asyncio
async def test_chain_mismatch_stops_before_receipt() -> None:
    rpc = FakeRpc(receipt(), chain_id="0x1")

    observation = await observe(rpc)

    assert observation.status is IndependentSettlementStatus.INDETERMINATE
    assert observation.diagnostic == "CHAIN_MISMATCH"
    assert observation.ledger is None
    assert rpc.calls == [("eth_chainId", ())]


@pytest.mark.asyncio
async def test_invalid_transaction_reference_makes_no_rpc_call() -> None:
    rpc = FakeRpc(receipt())

    observation = await observe(rpc, tx="not-a-hash")

    assert observation.status is IndependentSettlementStatus.INDETERMINATE
    assert observation.diagnostic == "INVALID_TRANSACTION_REFERENCE"
    assert observation.transaction_reference == "not-a-hash"
    assert observation.ledger is None
    assert rpc.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["payer", "recipient", "asset"])
async def test_unsupported_profile_makes_no_rpc_call(field: str) -> None:
    value = profile()
    if field == "asset":
        value = value.model_copy(
            update={
                "asset_identity": value.asset_identity.model_copy(
                    update={"reference": "not-an-address"}
                )
            }
        )
    else:
        value = value.model_copy(update={field: "not-an-address"})
    rpc = FakeRpc(receipt())

    observation = await observe(rpc, value)

    assert observation.status is IndependentSettlementStatus.INDETERMINATE
    assert observation.diagnostic == "UNSUPPORTED_SETTLEMENT_PROFILE"
    assert observation.profile == value
    assert observation.ledger is None
    assert rpc.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mutation",
    [
        "non_object",
        "missing_hash",
        "wrong_hash",
        "missing_status",
        "invalid_status",
        "missing_logs",
        "non_list_logs",
        "matching_topic_count",
        "malformed_payer_topic",
        "malformed_recipient_topic",
        "malformed_data",
    ],
)
async def test_malformed_receipt_variants_are_indeterminate(mutation: str) -> None:
    value: JsonValue = deepcopy(receipt())
    if mutation == "non_object":
        value = []
    else:
        assert isinstance(value, dict)
        log = cast(dict[str, JsonValue], cast(list[JsonValue], value["logs"])[0])
        topics = cast(list[JsonValue], log["topics"])
        if mutation == "missing_hash":
            value.pop("transactionHash")
        elif mutation == "wrong_hash":
            value["transactionHash"] = "0x" + "9" * 64
        elif mutation == "missing_status":
            value.pop("status")
        elif mutation == "invalid_status":
            value["status"] = "confirmed"
        elif mutation == "missing_logs":
            value.pop("logs")
        elif mutation == "non_list_logs":
            value["logs"] = {}
        elif mutation == "matching_topic_count":
            log["topics"] = [TRANSFER_TOPIC]
        elif mutation == "malformed_payer_topic":
            topics[1] = "not-a-topic"
        elif mutation == "malformed_recipient_topic":
            topics[2] = "not-a-topic"
        else:
            log["data"] = "0x1"

    observation = await observe(FakeRpc(value))

    assert observation.status is IndependentSettlementStatus.INDETERMINATE
    assert observation.diagnostic == "MALFORMED_RECEIPT"
    assert observation.ledger is None


@pytest.mark.asyncio
@pytest.mark.parametrize("candidate_count", [0, 2])
async def test_transfer_candidate_count_mismatch(candidate_count: int) -> None:
    value = receipt()
    log = cast(dict[str, JsonValue], cast(list[JsonValue], value["logs"])[0])
    value["logs"] = [] if candidate_count == 0 else [log, deepcopy(log)]

    observation = await observe(FakeRpc(value))

    assert observation.status is IndependentSettlementStatus.INDETERMINATE
    assert observation.diagnostic == "TRANSFER_MISMATCH"
    assert observation.dimensions.chain_verified is True
    assert observation.dimensions.asset_verified is False
    assert observation.dimensions.amount_verified is False
    assert observation.dimensions.recipient_verified is False
    assert observation.dimensions.payer_verified is False
    assert observation.ledger is None


@pytest.mark.asyncio
async def test_unrelated_logs_are_ignored() -> None:
    value = receipt()
    logs = cast(list[JsonValue], value["logs"])
    logs.insert(0, "not-an-object")
    logs.insert(1, {"address": ASSET, "topics": ["0xother"], "data": "0x1"})
    logs.insert(
        2,
        {
            "address": "0x4444444444444444444444444444444444444444",
            "topics": [TRANSFER_TOPIC],
            "data": "0x1",
        },
    )

    observation = await observe(FakeRpc(value))

    assert observation.status is IndependentSettlementStatus.CONFIRMED


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "verified"),
    [
        ("payer", "payer_verified"),
        ("recipient", "recipient_verified"),
        ("amount", "amount_verified"),
    ],
)
async def test_transfer_mismatch_preserves_computed_dimensions(field: str, verified: str) -> None:
    value = receipt()
    log = cast(dict[str, JsonValue], cast(list[JsonValue], value["logs"])[0])
    topics = cast(list[JsonValue], log["topics"])
    if field == "payer":
        topics[1] = address_topic("0x4444444444444444444444444444444444444444")
    elif field == "recipient":
        topics[2] = address_topic("0x4444444444444444444444444444444444444444")
    else:
        log["data"] = "0x" + (999).to_bytes(32, "big").hex()

    observation = await observe(FakeRpc(value))

    assert observation.status is IndependentSettlementStatus.INDETERMINATE
    assert observation.diagnostic == "TRANSFER_MISMATCH"
    assert observation.dimensions.chain_verified is True
    assert observation.dimensions.asset_verified is True
    assert getattr(observation.dimensions, verified) is False
    for name in ("amount_verified", "recipient_verified", "payer_verified"):
        if name != verified:
            assert getattr(observation.dimensions, name) is True
    assert observation.ledger is None


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["eth_chainId", "eth_getTransactionReceipt"])
async def test_rpc_unavailable_is_unavailable(method: str) -> None:
    rpc = FakeRpc(receipt(), error_on=method, error=EvmRpcError("synthetic unavailable"))

    observation = await observe(rpc)

    assert observation.status is IndependentSettlementStatus.UNAVAILABLE
    assert observation.diagnostic == "OBSERVER_UNAVAILABLE"
    assert observation.ledger is None


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["eth_chainId", "eth_getTransactionReceipt"])
async def test_rpc_protocol_error_is_indeterminate(method: str) -> None:
    rpc = FakeRpc(receipt(), error_on=method, error=EvmRpcProtocolError("synthetic malformed"))

    observation = await observe(rpc)

    assert observation.status is IndependentSettlementStatus.INDETERMINATE
    assert observation.diagnostic == "MALFORMED_RPC_RESPONSE"
    assert observation.ledger is None


@pytest.mark.asyncio
async def test_hash_and_addresses_compare_case_insensitively() -> None:
    value = receipt()
    value["transactionHash"] = TX_HASH.upper().replace("0X", "0x")
    log = cast(dict[str, JsonValue], cast(list[JsonValue], value["logs"])[0])
    log["address"] = ASSET.upper().replace("0X", "0x")
    log["topics"] = [
        TRANSFER_TOPIC.upper().replace("0X", "0x"),
        address_topic(PAYER).upper().replace("0X", "0x"),
        address_topic(RECIPIENT).upper().replace("0X", "0x"),
    ]

    observation = await observe(FakeRpc(value))

    assert observation.status is IndependentSettlementStatus.CONFIRMED


def test_unavailable_settlement_has_all_dimensions_false() -> None:
    observation = unavailable_settlement(
        "SETTLEMENT_PROFILE_UNAVAILABLE",
        source=SOURCE,
        observed_at=NOW,
    )

    assert observation.status is IndependentSettlementStatus.UNAVAILABLE
    assert observation.diagnostic == "SETTLEMENT_PROFILE_UNAVAILABLE"
    assert observation.profile is None
    assert observation.transaction_reference is None
    assert observation.ledger is None
    assert observation.dimensions.chain_verified is False
    assert observation.dimensions.asset_verified is False
    assert observation.dimensions.amount_verified is False
    assert observation.dimensions.recipient_verified is False
    assert observation.dimensions.payer_verified is False
