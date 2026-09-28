from __future__ import annotations

import base64
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest
from pydantic import JsonValue, ValidationError

from settlediff.application.payment_rails import AdapterEvidence
from settlediff.application.run import RecoveryState
from settlediff.domain.models import (
    AssetIdentity,
    EvidenceArtifact,
    IndependentSettlementStatus,
    LedgerStatus,
    RetrySafety,
    SettlementProfile,
)
from settlediff.domain.retry import (
    CONFIRMED_TRANSFER,
    EXPLICIT_NON_SUBMISSION,
    RECOVERY_UNAVAILABLE,
    SUBMISSION_UNCERTAIN,
    RetryRunStateSnapshot,
    analyze_retry,
)
from settlediff.observers.evm_rpc import EvmRpcError, EvmRpcProtocolError
from settlediff.observers.evm_transfer import TRANSFER_TOPIC
from settlediff.x402.client_contract import (
    ExternalSignerResult,
    SignerServiceResponse,
    SignerSubmissionState,
)
from settlediff.x402.parser import parse_payment_required
from settlediff.x402.recovery import (
    X402RecoveryDiagnostic,
    X402SubmissionRecovery,
    recover_x402_submission,
    x402_recovery_evidence,
)

FIXTURE = Path(__file__).parents[2] / "contract/x402/fixtures/payment-required-v2.json"
PAYER = "0x3333333333333333333333333333333333333333"
TX_HASH = "0x" + "2" * 64
NOW = datetime(2026, 8, 31, tzinfo=UTC)


def profile() -> SettlementProfile:
    selected = parse_payment_required(
        base64.b64encode(FIXTURE.read_bytes()).decode()
    ).selected_requirement()
    return SettlementProfile(
        network=selected.network,
        chain_id=int(selected.network.split(":", 1)[1]),
        asset_identity=AssetIdentity(
            symbol="USDC",
            network=selected.network,
            reference=selected.asset,
            decimals=6,
        ),
        atomic_amount=int(selected.amount),
        recipient=selected.pay_to,
        payer=PAYER,
    )


def address_topic(address: str) -> str:
    return "0x" + "0" * 24 + address[2:].lower()


def receipt(*, status: str = "0x1") -> dict[str, JsonValue]:
    value = profile()
    return {
        "transactionHash": TX_HASH,
        "status": status,
        "logs": (
            [
                {
                    "address": value.asset_identity.reference,
                    "topics": [
                        TRANSFER_TOPIC,
                        address_topic(value.payer),
                        address_topic(value.recipient),
                    ],
                    "data": "0x" + value.atomic_amount.to_bytes(32, "big").hex(),
                }
            ]
            if status == "0x1"
            else []
        ),
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
        service_response=SignerServiceResponse(
            status=200,
            media_type=None,
            received_bytes=0,
            truncated=False,
            parsed_body=None,
        ),
        payment_reference=None,
        transaction_reference=transaction_reference,
        payer=PAYER if transaction_reference is not None else None,
        notes=(),
    )


class FakeRpc:
    def __init__(
        self,
        transaction_receipt: JsonValue,
        *,
        chain_id: JsonValue = "0x14a34",
        error: EvmRpcError | None = None,
    ) -> None:
        self.transaction_receipt = transaction_receipt
        self.chain_id = chain_id
        self.error = error
        self.calls: list[tuple[str, tuple[JsonValue, ...]]] = []

    async def call(self, method: str, params: tuple[JsonValue, ...]) -> JsonValue:
        self.calls.append((method, params))
        if self.error is not None:
            raise self.error
        if method == "eth_chainId":
            return self.chain_id
        return self.transaction_receipt


async def recover(result: ExternalSignerResult, rpc: FakeRpc) -> X402SubmissionRecovery:
    return await recover_x402_submission(
        result,
        rpc,
        profile(),
        scheme="exact",
        observed_at=NOW,
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

    recovered = await recover(signer_result(state), rpc)

    assert recovered.state is RecoveryState.NOT_SUBMITTED
    assert recovered.proof_of_non_submission is True
    assert recovered.independent_settlement is None
    assert recovered.independent_observation.status is IndependentSettlementStatus.UNAVAILABLE
    assert recovered.independent_observation.diagnostic == "NO_TRANSACTION_REFERENCE"
    assert recovered.independent_observation.source == "evm_rpc:eip155:84532"
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
async def test_missing_transaction_reference_is_unresolved_without_rpc() -> None:
    rpc = FakeRpc(receipt())

    recovered = await recover(
        signer_result(
            SignerSubmissionState.SUBMISSION_UNCERTAIN,
            transaction_reference=None,
        ),
        rpc,
    )

    assert recovered.state is RecoveryState.UNRESOLVED
    assert recovered.independent_settlement is None
    assert recovered.independent_observation.status is IndependentSettlementStatus.UNAVAILABLE
    assert recovered.independent_observation.diagnostic == "NO_TRANSACTION_REFERENCE"
    assert recovered.diagnostic is None
    assert rpc.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("receipt_status", "observation_status", "ledger_status"),
    [
        ("0x1", IndependentSettlementStatus.CONFIRMED, LedgerStatus.CONFIRMED),
        ("0x0", IndependentSettlementStatus.FAILED, LedgerStatus.FAILED),
    ],
)
async def test_conclusive_receipt_proves_submission(
    receipt_status: str,
    observation_status: IndependentSettlementStatus,
    ledger_status: LedgerStatus,
) -> None:
    recovered = await recover(
        signer_result(SignerSubmissionState.SUBMISSION_UNCERTAIN),
        FakeRpc(receipt(status=receipt_status)),
    )

    assert recovered.state is RecoveryState.SUBMITTED
    assert recovered.proof_of_non_submission is False
    assert recovered.independent_observation.status is observation_status
    assert recovered.independent_settlement == recovered.independent_observation.ledger
    assert recovered.independent_settlement is not None
    assert recovered.independent_settlement.status is ledger_status
    assert recovered.diagnostic is None
    evidence = x402_recovery_evidence(recovered, observed_at=NOW)
    assert evidence.source == "x402.base_sepolia.transaction_receipt"
    assert evidence.transaction_reference == TX_HASH
    assert cast(dict[str, JsonValue], evidence.data)["status"] == ledger_status.value


@pytest.mark.asyncio
async def test_pending_receipt_remains_unresolved_without_diagnostic() -> None:
    recovered = await recover(
        signer_result(SignerSubmissionState.SUBMISSION_UNCERTAIN), FakeRpc(None)
    )

    assert recovered.state is RecoveryState.UNRESOLVED
    assert recovered.independent_observation.status is IndependentSettlementStatus.INDETERMINATE
    assert recovered.independent_observation.diagnostic == "RECEIPT_PENDING"
    assert recovered.independent_settlement is None
    assert recovered.diagnostic is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("rpc", "status", "diagnostic"),
    [
        (
            FakeRpc(receipt(), error=EvmRpcError("synthetic unavailable")),
            IndependentSettlementStatus.UNAVAILABLE,
            X402RecoveryDiagnostic.RPC_UNAVAILABLE,
        ),
        (
            FakeRpc(receipt(), error=EvmRpcProtocolError("synthetic malformed")),
            IndependentSettlementStatus.INDETERMINATE,
            X402RecoveryDiagnostic.EVIDENCE_INVALID,
        ),
        (
            FakeRpc(receipt(), chain_id="0x1"),
            IndependentSettlementStatus.INDETERMINATE,
            X402RecoveryDiagnostic.EVIDENCE_INVALID,
        ),
    ],
)
async def test_nonconclusive_observer_outcomes_preserve_uncertainty(
    rpc: FakeRpc,
    status: IndependentSettlementStatus,
    diagnostic: X402RecoveryDiagnostic,
) -> None:
    recovered = await recover(signer_result(SignerSubmissionState.SUBMISSION_UNCERTAIN), rpc)

    assert recovered.state is RecoveryState.UNRESOLVED
    assert recovered.independent_observation.status is status
    assert recovered.independent_observation.ledger is None
    assert recovered.independent_settlement is None
    assert recovered.diagnostic is diagnostic
    assert "synthetic" not in recovered.model_dump_json()
    evidence = x402_recovery_evidence(recovered, observed_at=NOW)
    assert evidence.source == "x402.read_only_recovery"
    assert cast(dict[str, JsonValue], evidence.data)["diagnostic"] == diagnostic.value


@pytest.mark.asyncio
async def test_recovery_rejects_ledger_observation_mismatch() -> None:
    confirmed = await recover(
        signer_result(SignerSubmissionState.SUBMISSION_UNCERTAIN),
        FakeRpc(receipt()),
    )
    failed = await recover(
        signer_result(SignerSubmissionState.SUBMISSION_UNCERTAIN),
        FakeRpc(receipt(status="0x0")),
    )
    assert failed.independent_settlement is not None

    with pytest.raises(ValidationError, match="must match"):
        X402SubmissionRecovery(
            state=confirmed.state,
            proof_of_non_submission=confirmed.proof_of_non_submission,
            source_submission_state=confirmed.source_submission_state,
            transaction_reference=confirmed.transaction_reference,
            independent_settlement=failed.independent_settlement,
            independent_observation=confirmed.independent_observation,
            diagnostic=confirmed.diagnostic,
        )


def _persisted(evidence: object) -> EvidenceArtifact:
    adapter_evidence = cast(AdapterEvidence, evidence)
    return EvidenceArtifact(
        artifact_id="syn_run:recovery",
        artifact_type=adapter_evidence.artifact_type,
        source=adapter_evidence.source,
        collected_at=NOW,
        redacted=False,
        data=adapter_evidence.data,
    )


@pytest.mark.asyncio
async def test_recovery_evidence_preserves_retry_classifications() -> None:
    confirmed = await recover(
        signer_result(SignerSubmissionState.SUBMISSION_UNCERTAIN),
        FakeRpc(receipt()),
    )
    confirmed_assessment = analyze_retry(
        None,
        (_persisted(x402_recovery_evidence(confirmed, observed_at=NOW)),),
        RetryRunStateSnapshot(
            run_id="syn_run", state="evidence_recovery", submission_uncertain=True
        ),
    )
    assert confirmed_assessment.safety is RetrySafety.DO_NOT_RETRY
    assert CONFIRMED_TRANSFER in confirmed_assessment.reason_codes

    proven = await recover(
        signer_result(SignerSubmissionState.PROVEN_NOT_SUBMITTED), FakeRpc(receipt())
    )
    proven_assessment = analyze_retry(
        None,
        (_persisted(x402_recovery_evidence(proven, observed_at=NOW)),),
        RetryRunStateSnapshot(
            run_id="syn_run", state="evidence_recovery", submission_uncertain=True
        ),
    )
    assert proven_assessment.safety is RetrySafety.REQUIRES_HUMAN_DECISION
    assert SUBMISSION_UNCERTAIN in proven_assessment.reason_codes
    assert EXPLICIT_NON_SUBMISSION in proven_assessment.reason_codes

    unavailable = await recover(
        signer_result(SignerSubmissionState.SUBMISSION_UNCERTAIN),
        FakeRpc(receipt(), error=EvmRpcError("synthetic failure")),
    )
    unavailable_assessment = analyze_retry(
        None,
        (_persisted(x402_recovery_evidence(unavailable, observed_at=NOW)),),
        RetryRunStateSnapshot(run_id="syn_run", state="failed", submission_uncertain=True),
    )
    assert unavailable_assessment.safety is RetrySafety.REQUIRES_HUMAN_DECISION
    assert RECOVERY_UNAVAILABLE in unavailable_assessment.reason_codes
