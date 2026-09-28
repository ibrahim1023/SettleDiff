"""Read-only corroboration of a Perflo-referenced Base transaction, not customer settlement."""

from __future__ import annotations

import re
from datetime import datetime
from enum import StrEnum
from typing import Self, cast

from pydantic import BaseModel, ConfigDict, JsonValue, model_validator

from settlediff.domain.models import UtcDatetime
from settlediff.domain.redaction import mask_identifier
from settlediff.observers.evm_rpc import EvmRpcError, EvmRpcProtocolError
from settlediff.observers.evm_transfer import ReadOnlyRpcPort

_HASH = re.compile(r"^0x[0-9a-fA-F]{64}$")


class PerfloChainStatus(StrEnum):
    RECEIPT_SUCCEEDED = "RECEIPT_SUCCEEDED"
    RECEIPT_REVERTED = "RECEIPT_REVERTED"
    INDETERMINATE = "INDETERMINATE"
    UNAVAILABLE = "UNAVAILABLE"


class PerfloChainComparison(StrEnum):
    CONTRADICTED = "CONTRADICTED"
    NOT_COMPARABLE = "NOT_COMPARABLE"


class PerfloChainCorroboration(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    schema_version: int = 1
    status: PerfloChainStatus
    diagnostic: str
    comparison: PerfloChainComparison
    transaction_reference: str | None
    observed_at: UtcDatetime

    @model_validator(mode="after")
    def require_conservative_comparison(self) -> Self:
        if (
            self.comparison is PerfloChainComparison.CONTRADICTED
            and self.status is not PerfloChainStatus.RECEIPT_REVERTED
        ):
            raise ValueError("only an observed reverted receipt may contradict a provider claim")
        return self


def _result(
    status: PerfloChainStatus,
    diagnostic: str,
    transaction_reference: str | None,
    observed_at: datetime,
    *,
    contradicted: bool = False,
) -> PerfloChainCorroboration:
    return PerfloChainCorroboration(
        status=status,
        diagnostic=diagnostic,
        comparison=(
            PerfloChainComparison.CONTRADICTED
            if contradicted
            else PerfloChainComparison.NOT_COMPARABLE
        ),
        transaction_reference=(
            mask_identifier(transaction_reference) if transaction_reference is not None else None
        ),
        observed_at=observed_at,
    )


async def corroborate_perflo_transaction(
    rpc: ReadOnlyRpcPort | None,
    transaction_reference: str | None,
    chain: str | None,
    provider_status: str | None,
    *,
    observed_at: datetime,
) -> PerfloChainCorroboration:
    if rpc is None:
        return _result(
            PerfloChainStatus.UNAVAILABLE,
            "OBSERVER_NOT_CONFIGURED",
            transaction_reference,
            observed_at,
        )
    if transaction_reference is None:
        return _result(PerfloChainStatus.UNAVAILABLE, "NO_TRANSACTION_REFERENCE", None, observed_at)
    if _HASH.fullmatch(transaction_reference) is None:
        return _result(
            PerfloChainStatus.UNAVAILABLE,
            "INVALID_TRANSACTION_REFERENCE",
            transaction_reference,
            observed_at,
        )
    if chain != "base":
        return _result(
            PerfloChainStatus.UNAVAILABLE,
            "UNSUPPORTED_PROVIDER_CHAIN",
            transaction_reference,
            observed_at,
        )
    try:
        chain_id = await rpc.call("eth_chainId", ())
        if chain_id != "0x2105":
            return _result(
                PerfloChainStatus.INDETERMINATE,
                "CHAIN_MISMATCH",
                transaction_reference,
                observed_at,
            )
        raw_receipt = await rpc.call("eth_getTransactionReceipt", (transaction_reference,))
    except EvmRpcProtocolError:
        return _result(
            PerfloChainStatus.INDETERMINATE,
            "MALFORMED_RPC_RESPONSE",
            transaction_reference,
            observed_at,
        )
    except EvmRpcError:
        return _result(
            PerfloChainStatus.UNAVAILABLE,
            "OBSERVER_UNAVAILABLE",
            transaction_reference,
            observed_at,
        )
    if raw_receipt is None:
        return _result(
            PerfloChainStatus.INDETERMINATE, "RECEIPT_PENDING", transaction_reference, observed_at
        )
    if not isinstance(raw_receipt, dict):
        return _result(
            PerfloChainStatus.INDETERMINATE, "MALFORMED_RECEIPT", transaction_reference, observed_at
        )
    receipt = cast(dict[str, JsonValue], raw_receipt)
    receipt_hash = receipt.get("transactionHash")
    if (
        not isinstance(receipt_hash, str)
        or receipt_hash.casefold() != transaction_reference.casefold()
    ):
        return _result(
            PerfloChainStatus.INDETERMINATE, "MALFORMED_RECEIPT", transaction_reference, observed_at
        )
    status = receipt.get("status")
    if status == "0x0":
        return _result(
            PerfloChainStatus.RECEIPT_REVERTED,
            "PROVIDER_FINALIZED_RECEIPT_REVERTED"
            if provider_status == "finalized"
            else "RECEIPT_REVERTED",
            transaction_reference,
            observed_at,
            contradicted=provider_status == "finalized",
        )
    if status == "0x1":
        return _result(
            PerfloChainStatus.RECEIPT_SUCCEEDED,
            "RECEIPT_SUCCEEDED_TRANSFER_NOT_CHECKED",
            transaction_reference,
            observed_at,
        )
    return _result(
        PerfloChainStatus.INDETERMINATE, "MALFORMED_RECEIPT", transaction_reference, observed_at
    )
