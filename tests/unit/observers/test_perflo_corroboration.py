from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import JsonValue

from settlediff.observers.evm_rpc import EvmRpcError, EvmRpcProtocolError
from settlediff.observers.perflo_chain import corroborate_perflo_transaction

TX = "0x" + "a" * 64
NOW = datetime(2026, 9, 28, tzinfo=UTC)


class Rpc:
    def __init__(
        self, chain: JsonValue = "0x2105", receipt: JsonValue = None, error: Exception | None = None
    ):
        self.chain = chain
        self.receipt = receipt
        self.error = error
        self.calls: list[tuple[str, tuple[JsonValue, ...]]] = []

    async def call(self, method: str, params: tuple[JsonValue, ...]) -> JsonValue:
        self.calls.append((method, params))
        if self.error is not None:
            raise self.error
        return self.chain if method == "eth_chainId" else self.receipt


def receipt(status: str) -> JsonValue:
    return {"transactionHash": TX.upper().replace("0X", "0x"), "status": status, "logs": []}


@pytest.mark.asyncio
async def test_successful_receipt_never_establishes_provider_match_or_customer_settlement() -> None:
    rpc = Rpc(receipt=receipt("0x1"))
    result = await corroborate_perflo_transaction(rpc, TX, "base", "finalized", observed_at=NOW)
    assert (result.status, result.comparison, result.diagnostic) == (
        "RECEIPT_SUCCEEDED",
        "NOT_COMPARABLE",
        "RECEIPT_SUCCEEDED_TRANSFER_NOT_CHECKED",
    )
    assert result.transaction_reference == "0xaaaa…aaaa"
    assert result.model_dump_json().find(TX) == -1
    assert [method for method, _ in rpc.calls] == ["eth_chainId", "eth_getTransactionReceipt"]


@pytest.mark.asyncio
async def test_reverted_receipt_contradicts_only_finalized_provider_claim() -> None:
    for claim, comparison in [("finalized", "CONTRADICTED"), ("failed", "NOT_COMPARABLE")]:
        result = await corroborate_perflo_transaction(
            Rpc(receipt=receipt("0x0")), TX, "base", claim, observed_at=NOW
        )
        assert (result.status, result.comparison) == ("RECEIPT_REVERTED", comparison)


@pytest.mark.asyncio
@pytest.mark.parametrize("tx,chain", [(None, "base"), ("0xdead", "base"), (TX, "tempo")])
async def test_ineligible_reference_or_chain_makes_no_rpc_call(tx: str | None, chain: str) -> None:
    rpc = Rpc()
    result = await corroborate_perflo_transaction(rpc, tx, chain, "finalized", observed_at=NOW)
    assert result.status == "UNAVAILABLE"
    assert not rpc.calls


@pytest.mark.asyncio
async def test_chain_mismatch_and_pending_receipt_are_not_contradictions() -> None:
    wrong = await corroborate_perflo_transaction(
        Rpc(chain="0x14a34"), TX, "base", "finalized", observed_at=NOW
    )
    pending = await corroborate_perflo_transaction(Rpc(), TX, "base", "finalized", observed_at=NOW)
    assert (wrong.status, wrong.comparison) == ("INDETERMINATE", "NOT_COMPARABLE")
    assert (pending.status, pending.comparison) == ("INDETERMINATE", "NOT_COMPARABLE")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error,status",
    [(EvmRpcError("down"), "UNAVAILABLE"), (EvmRpcProtocolError("bad"), "INDETERMINATE")],
)
async def test_rpc_failures_are_safe(error: Exception, status: str) -> None:
    result = await corroborate_perflo_transaction(
        Rpc(error=error), TX, "base", "finalized", observed_at=NOW
    )
    assert result.status == status
    assert result.comparison == "NOT_COMPARABLE"
    assert "down" not in result.model_dump_json()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        [],
        {},
        {"transactionHash": TX, "status": "0x2"},
        {"transactionHash": "0x" + "b" * 64, "status": "0x1"},
    ],
)
async def test_malformed_receipt_is_indeterminate(payload: JsonValue) -> None:
    result = await corroborate_perflo_transaction(
        Rpc(receipt=payload), TX, "base", "finalized", observed_at=NOW
    )
    assert result.status == "INDETERMINATE"
    assert result.comparison == "NOT_COMPARABLE"
