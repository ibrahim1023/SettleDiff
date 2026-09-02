"""Deterministic verification of x402 exact-USDC settlement on Base Sepolia."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Protocol, Self, cast

from pydantic import BaseModel, ConfigDict, JsonValue, model_validator

from settlediff.application.payment_rails import AdapterEvidence
from settlediff.application.run import RecoveryState
from settlediff.domain.models import ArtifactType, AssetIdentity, LedgerRecord, LedgerStatus
from settlediff.domain.money import Money
from settlediff.x402.client_contract import ExternalSignerResult, SignerSubmissionState
from settlediff.x402.models import PaymentRequirements
from settlediff.x402.normalize import BASE_SEPOLIA, BASE_SEPOLIA_USDC, USDC_DECIMALS
from settlediff.x402.rpc import X402RpcError

TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
_BASE_SEPOLIA_CHAIN_ID = "0x14a34"


class X402SettlementError(ValueError):
    pass


class X402RecoveryDiagnostic(StrEnum):
    RPC_UNAVAILABLE = "rpc_unavailable"
    EVIDENCE_INVALID = "evidence_invalid"


class X402SubmissionRecovery(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    state: RecoveryState
    proof_of_non_submission: bool
    source_submission_state: SignerSubmissionState
    transaction_reference: str | None
    independent_settlement: LedgerRecord | None
    diagnostic: X402RecoveryDiagnostic | None = None

    @model_validator(mode="after")
    def require_coherent_recovery(self) -> Self:
        if self.state is RecoveryState.NOT_SUBMITTED:
            if not self.proof_of_non_submission or self.independent_settlement is not None:
                raise ValueError("non-submission recovery is incoherent")
        elif self.state is RecoveryState.SUBMITTED:
            if self.proof_of_non_submission or self.independent_settlement is None:
                raise ValueError("submitted recovery requires independent settlement evidence")
        elif self.proof_of_non_submission or self.independent_settlement is not None:
            raise ValueError("unresolved recovery cannot assert submission certainty")
        return self


class ReadOnlyRpcPort(Protocol):
    async def call(self, method: str, params: tuple[JsonValue, ...]) -> JsonValue: ...


async def recover_x402_submission(
    result: ExternalSignerResult,
    rpc: ReadOnlyRpcPort,
    requirement: PaymentRequirements,
    *,
    expected_payer: str | None,
    observed_at: datetime,
) -> X402SubmissionRecovery:
    if result.submission_state in {
        SignerSubmissionState.NOT_SUBMITTED,
        SignerSubmissionState.PROVEN_NOT_SUBMITTED,
    }:
        return X402SubmissionRecovery(
            state=RecoveryState.NOT_SUBMITTED,
            proof_of_non_submission=True,
            source_submission_state=result.submission_state,
            transaction_reference=result.transaction_reference,
            independent_settlement=None,
        )
    transaction_reference = result.transaction_reference
    if transaction_reference is None:
        return X402SubmissionRecovery(
            state=RecoveryState.UNRESOLVED,
            proof_of_non_submission=False,
            source_submission_state=result.submission_state,
            transaction_reference=None,
            independent_settlement=None,
        )
    try:
        settlement = await verify_exact_usdc_settlement(
            rpc,
            transaction_reference,
            requirement,
            expected_payer=expected_payer,
            observed_at=observed_at,
        )
    except X402RpcError:
        return X402SubmissionRecovery(
            state=RecoveryState.UNRESOLVED,
            proof_of_non_submission=False,
            source_submission_state=result.submission_state,
            transaction_reference=transaction_reference,
            independent_settlement=None,
            diagnostic=X402RecoveryDiagnostic.RPC_UNAVAILABLE,
        )
    except X402SettlementError:
        return X402SubmissionRecovery(
            state=RecoveryState.UNRESOLVED,
            proof_of_non_submission=False,
            source_submission_state=result.submission_state,
            transaction_reference=transaction_reference,
            independent_settlement=None,
            diagnostic=X402RecoveryDiagnostic.EVIDENCE_INVALID,
        )
    if settlement is None:
        return X402SubmissionRecovery(
            state=RecoveryState.UNRESOLVED,
            proof_of_non_submission=False,
            source_submission_state=result.submission_state,
            transaction_reference=transaction_reference,
            independent_settlement=None,
        )
    return X402SubmissionRecovery(
        state=RecoveryState.SUBMITTED,
        proof_of_non_submission=False,
        source_submission_state=result.submission_state,
        transaction_reference=transaction_reference,
        independent_settlement=settlement,
    )


def x402_recovery_evidence(
    recovery: X402SubmissionRecovery, *, observed_at: datetime
) -> AdapterEvidence:
    data: JsonValue
    if recovery.independent_settlement is not None:
        source = "x402.base_sepolia.transaction_receipt"
        data = cast(JsonValue, recovery.independent_settlement.model_dump(mode="json"))
    else:
        source = (
            "x402.external_signer.recovery"
            if recovery.state is RecoveryState.NOT_SUBMITTED
            else "x402.read_only_recovery"
        )
        data = {
            "status": recovery.state.value,
            "proof_of_non_submission": recovery.proof_of_non_submission,
            "source_submission_state": recovery.source_submission_state.value,
            "diagnostic": recovery.diagnostic.value if recovery.diagnostic is not None else None,
        }
    return AdapterEvidence(
        adapter_id="x402",
        protocol_version="2",
        operation="transaction_status",
        source=source,
        artifact_type=ArtifactType.PAYMENT_RECEIPT,
        data=data,
        observed_at=observed_at,
        transaction_reference=recovery.transaction_reference,
    )


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
    if expected_payer is None:
        raise X402SettlementError("expected payer is required for confirmed settlement")
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
    if payer.casefold() != expected_payer.casefold():
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
