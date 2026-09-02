from __future__ import annotations

import base64
from copy import deepcopy
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import cast

import pytest
from pydantic import JsonValue

from settlediff.application.run import RecoveryState
from settlediff.domain.models import LedgerStatus
from settlediff.domain.money import Money
from settlediff.x402.client_contract import (
    ExternalSignerResult,
    SignerServiceResponse,
    SignerSubmissionState,
)
from settlediff.x402.models import PaymentRequirements
from settlediff.x402.parser import parse_payment_required
from settlediff.x402.recovery import (
    TRANSFER_TOPIC,
    X402RecoveryDiagnostic,
    X402SettlementError,
    recover_x402_submission,
    verify_exact_usdc_settlement,
    x402_recovery_evidence,
)
from settlediff.x402.rpc import X402RpcError

FIXTURE = Path(__file__).parents[2] / "contract/x402/fixtures/payment-required-v2.json"
PAYER = "0x3333333333333333333333333333333333333333"
FACILITATOR = "0x5555555555555555555555555555555555555555"
TX_HASH = "0x2222222222222222222222222222222222222222222222222222222222222222"
NOW = datetime(2026, 8, 31, tzinfo=UTC)


def requirement() -> PaymentRequirements:
    payload = FIXTURE.read_bytes()
    return parse_payment_required(base64.b64encode(payload).decode()).selected_requirement()


def address_topic(address: str) -> str:
    return "0x" + "0" * 24 + address[2:].lower()


def receipt() -> dict[str, JsonValue]:
    selected = requirement()
    return {
        "transactionHash": TX_HASH,
        "status": "0x1",
        "from": FACILITATOR,
        "to": selected.asset,
        "blockNumber": "0x123",
        "logs": [
            {
                "address": selected.asset.lower(),
                "topics": [
                    TRANSFER_TOPIC,
                    address_topic(PAYER),
                    address_topic(selected.pay_to),
                ],
                "data": "0x" + int(selected.amount).to_bytes(32, "big").hex(),
                "logIndex": "0x0",
            }
        ],
    }


def signer_result(
    state: SignerSubmissionState,
    *,
    transaction_reference: str | None = TX_HASH,
) -> ExternalSignerResult:
    provider: dict[str, JsonValue] | None = (
        {"success": True} if state is SignerSubmissionState.SUBMITTED_CONFIRMED else None
    )
    if state in {
        SignerSubmissionState.NOT_SUBMITTED,
        SignerSubmissionState.PROVEN_NOT_SUBMITTED,
    }:
        transaction_reference = None
    return ExternalSignerResult(
        adapter="x402",
        submission_state=state,
        challenge={"x402Version": 2},
        provider_settlement=provider,
        service_response=SignerServiceResponse(status=200, body=None),
        payment_reference=None,
        transaction_reference=transaction_reference,
        notes=(),
    )


class FakeRpc:
    def __init__(self, transaction_receipt: JsonValue, *, chain_id: str = "0x14a34") -> None:
        self.transaction_receipt = transaction_receipt
        self.chain_id = chain_id
        self.calls: list[tuple[str, tuple[JsonValue, ...]]] = []

    async def call(self, method: str, params: tuple[JsonValue, ...]) -> JsonValue:
        self.calls.append((method, params))
        if method == "eth_chainId":
            return self.chain_id
        if method == "eth_getTransactionReceipt":
            return self.transaction_receipt
        raise AssertionError(method)


@pytest.mark.asyncio
async def test_confirmed_receipt_requires_exact_usdc_transfer_not_transaction_sender() -> None:
    rpc = FakeRpc(receipt())

    ledger = await verify_exact_usdc_settlement(
        rpc,
        TX_HASH,
        requirement(),
        expected_payer=PAYER,
        observed_at=NOW,
    )

    assert ledger is not None
    assert ledger.status is LedgerStatus.CONFIRMED
    assert ledger.amount == Money(amount=Decimal("1000"), unit="USDC", minor_units=6)
    assert ledger.asset == "USDC"
    assert ledger.asset_identity is not None
    assert ledger.asset_identity.reference == requirement().asset
    assert ledger.network == "eip155:84532"
    assert ledger.recipient == requirement().pay_to
    assert ledger.transaction_hash == TX_HASH
    assert ledger.occurred_at == NOW
    assert cast(dict[str, JsonValue], rpc.transaction_receipt)["from"] == FACILITATOR
    assert rpc.calls == [
        ("eth_chainId", ()),
        ("eth_getTransactionReceipt", (TX_HASH,)),
    ]


@pytest.mark.asyncio
async def test_unrelated_receipt_logs_do_not_create_transfer_ambiguity() -> None:
    value = receipt()
    logs = cast(list[JsonValue], value["logs"])
    logs.insert(
        0,
        {
            "address": "0x4444444444444444444444444444444444444444",
            "topics": [TRANSFER_TOPIC, address_topic(PAYER), address_topic(requirement().pay_to)],
            "data": "0x" + (1).to_bytes(32, "big").hex(),
        },
    )

    ledger = await verify_exact_usdc_settlement(
        FakeRpc(value), TX_HASH, requirement(), expected_payer=PAYER, observed_at=NOW
    )

    assert ledger is not None
    assert ledger.status is LedgerStatus.CONFIRMED
    assert ledger.amount == Money(amount=Decimal("1000"), unit="USDC", minor_units=6)


@pytest.mark.asyncio
async def test_reverted_receipt_records_failure_without_inventing_transfer() -> None:
    failed = receipt()
    failed["status"] = "0x0"
    failed["logs"] = []

    ledger = await verify_exact_usdc_settlement(
        FakeRpc(failed), TX_HASH, requirement(), expected_payer=PAYER, observed_at=NOW
    )

    assert ledger is not None
    assert ledger.status is LedgerStatus.FAILED
    assert ledger.amount is None
    assert ledger.asset is None
    assert ledger.recipient is None
    assert ledger.error_reason == "transaction reverted"


@pytest.mark.asyncio
async def test_missing_or_pending_receipt_remains_unavailable() -> None:
    rpc = FakeRpc(None)

    assert (
        await verify_exact_usdc_settlement(
            rpc, TX_HASH, requirement(), expected_payer=PAYER, observed_at=NOW
        )
        is None
    )
    assert len(rpc.calls) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("transaction_hash", "asset", "payer", "message"),
    [
        ("not-a-hash", None, PAYER, "transaction hash"),
        (TX_HASH, "0x4444444444444444444444444444444444444444", PAYER, "asset"),
        (TX_HASH, None, "not-a-payer", "payer"),
    ],
)
async def test_invalid_expected_terms_stop_before_rpc(
    transaction_hash: str,
    asset: str | None,
    payer: str,
    message: str,
) -> None:
    rpc = FakeRpc(receipt())
    selected = requirement()
    if asset is not None:
        selected = selected.model_copy(update={"asset": asset})

    with pytest.raises(X402SettlementError, match=message):
        await verify_exact_usdc_settlement(
            rpc,
            transaction_hash,
            selected,
            expected_payer=payer,
            observed_at=NOW,
        )

    assert rpc.calls == []


@pytest.mark.asyncio
async def test_wrong_rpc_chain_stops_before_receipt_lookup() -> None:
    rpc = FakeRpc(receipt(), chain_id="0x1")

    with pytest.raises(X402SettlementError, match="chain"):
        await verify_exact_usdc_settlement(
            rpc, TX_HASH, requirement(), expected_payer=PAYER, observed_at=NOW
        )

    assert rpc.calls == [("eth_chainId", ())]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("case", "message"),
    [
        ("wrong_hash", "hash"),
        ("wrong_token", "transfer"),
        ("missing_transfer", "transfer"),
        ("multiple_transfers", "exactly one"),
        ("wrong_payer", "payer"),
        ("wrong_recipient", "recipient"),
        ("wrong_amount", "amount"),
        ("malformed_topics", "topics"),
        ("malformed_status", "status"),
    ],
)
async def test_receipt_contradictions_and_malformed_evidence_fail_closed(
    case: str, message: str
) -> None:
    value = deepcopy(receipt())
    log = cast(dict[str, JsonValue], cast(list[JsonValue], value["logs"])[0])
    topics = cast(list[JsonValue], log["topics"])
    if case == "wrong_hash":
        value["transactionHash"] = "0x" + "9" * 64
    elif case == "wrong_token":
        log["address"] = "0x4444444444444444444444444444444444444444"
    elif case == "missing_transfer":
        value["logs"] = []
    elif case == "multiple_transfers":
        value["logs"] = [log, deepcopy(log)]
    elif case == "wrong_payer":
        topics[1] = address_topic("0x4444444444444444444444444444444444444444")
    elif case == "wrong_recipient":
        topics[2] = address_topic("0x4444444444444444444444444444444444444444")
    elif case == "wrong_amount":
        log["data"] = "0x" + (999).to_bytes(32, "big").hex()
    elif case == "malformed_topics":
        log["topics"] = [TRANSFER_TOPIC]
    else:
        value["status"] = "confirmed"

    with pytest.raises(X402SettlementError, match=message):
        await verify_exact_usdc_settlement(
            FakeRpc(value), TX_HASH, requirement(), expected_payer=PAYER, observed_at=NOW
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "state",
    [
        SignerSubmissionState.NOT_SUBMITTED,
        SignerSubmissionState.PROVEN_NOT_SUBMITTED,
    ],
)
async def test_pre_submission_result_proves_no_submission_without_rpc(
    state: SignerSubmissionState,
) -> None:
    rpc = FakeRpc(receipt())

    recovered = await recover_x402_submission(
        signer_result(state), rpc, requirement(), expected_payer=PAYER, observed_at=NOW
    )

    assert recovered.state is RecoveryState.NOT_SUBMITTED
    assert recovered.proof_of_non_submission is True
    assert recovered.independent_settlement is None
    assert recovered.diagnostic is None
    assert rpc.calls == []
    evidence = x402_recovery_evidence(recovered, observed_at=NOW)
    assert evidence.operation == "transaction_status"
    assert evidence.source == "x402.external_signer.recovery"
    assert cast(dict[str, JsonValue], evidence.data) == {
        "status": "not_submitted",
        "proof_of_non_submission": True,
        "source_submission_state": state.value,
        "diagnostic": None,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "state",
    [
        SignerSubmissionState.SUBMITTED_CONFIRMED,
        SignerSubmissionState.SUBMISSION_UNCERTAIN,
    ],
)
@pytest.mark.parametrize("receipt_status", ["0x1", "0x0"])
async def test_mined_receipt_proves_submission_even_when_transaction_reverted(
    state: SignerSubmissionState,
    receipt_status: str,
) -> None:
    value = receipt()
    value["status"] = receipt_status
    if receipt_status == "0x0":
        value["logs"] = []

    recovered = await recover_x402_submission(
        signer_result(state),
        FakeRpc(value),
        requirement(),
        expected_payer=PAYER,
        observed_at=NOW,
    )

    assert recovered.state is RecoveryState.SUBMITTED
    assert recovered.proof_of_non_submission is False
    assert recovered.independent_settlement is not None
    assert recovered.independent_settlement.status is (
        LedgerStatus.CONFIRMED if receipt_status == "0x1" else LedgerStatus.FAILED
    )
    assert recovered.diagnostic is None
    evidence = x402_recovery_evidence(recovered, observed_at=NOW)
    assert evidence.source == "x402.base_sepolia.transaction_receipt"
    assert evidence.transaction_reference == TX_HASH
    assert cast(dict[str, JsonValue], evidence.data)["status"] == (
        "confirmed" if receipt_status == "0x1" else "failed"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("transaction_reference", [TX_HASH, None])
async def test_uncertain_missing_reference_or_receipt_remains_unresolved(
    transaction_reference: str | None,
) -> None:
    rpc = FakeRpc(None)

    recovered = await recover_x402_submission(
        signer_result(
            SignerSubmissionState.SUBMISSION_UNCERTAIN,
            transaction_reference=transaction_reference,
        ),
        rpc,
        requirement(),
        expected_payer=PAYER,
        observed_at=NOW,
    )

    assert recovered.state is RecoveryState.UNRESOLVED
    assert recovered.proof_of_non_submission is False
    assert recovered.independent_settlement is None
    assert recovered.diagnostic is None
    assert len(rpc.calls) == (2 if transaction_reference is not None else 0)


@pytest.mark.asyncio
@pytest.mark.parametrize(("chain_id", "diagnostic"), [("0x1", "evidence_invalid")])
async def test_invalid_independent_evidence_preserves_uncertainty(
    chain_id: str,
    diagnostic: str,
) -> None:
    recovered = await recover_x402_submission(
        signer_result(SignerSubmissionState.SUBMISSION_UNCERTAIN),
        FakeRpc(receipt(), chain_id=chain_id),
        requirement(),
        expected_payer=PAYER,
        observed_at=NOW,
    )

    assert recovered.state is RecoveryState.UNRESOLVED
    assert recovered.diagnostic is X402RecoveryDiagnostic(diagnostic)


@pytest.mark.asyncio
async def test_rpc_failure_preserves_uncertainty_without_exception_details() -> None:
    class FailedRpc:
        async def call(self, method: str, params: tuple[JsonValue, ...]) -> JsonValue:
            del method, params
            raise X402RpcError("synthetic secret RPC failure")

    recovered = await recover_x402_submission(
        signer_result(SignerSubmissionState.SUBMISSION_UNCERTAIN),
        FailedRpc(),
        requirement(),
        expected_payer=PAYER,
        observed_at=NOW,
    )

    assert recovered.state is RecoveryState.UNRESOLVED
    assert recovered.diagnostic is X402RecoveryDiagnostic.RPC_UNAVAILABLE
    assert "secret" not in recovered.model_dump_json()
    evidence = x402_recovery_evidence(recovered, observed_at=NOW)
    assert evidence.source == "x402.read_only_recovery"
    assert cast(dict[str, JsonValue], evidence.data)["diagnostic"] == "rpc_unavailable"
