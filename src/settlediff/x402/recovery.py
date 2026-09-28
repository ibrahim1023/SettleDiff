"""Deterministic recovery mapping for x402 settlement evidence."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Self, cast

from pydantic import BaseModel, ConfigDict, JsonValue, model_validator

from settlediff.application.payment_rails import AdapterEvidence
from settlediff.application.run import RecoveryState
from settlediff.domain.models import (
    ArtifactType,
    EvidenceClass,
    IndependentSettlementObservation,
    IndependentSettlementStatus,
    LedgerRecord,
    SettlementProfile,
)
from settlediff.observers.evm_transfer import (
    ReadOnlyRpcPort,
    observe_exact_erc20_transfer,
    unavailable_settlement,
)
from settlediff.x402.client_contract import ExternalSignerResult, SignerSubmissionState


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
    independent_observation: IndependentSettlementObservation
    diagnostic: X402RecoveryDiagnostic | None = None

    @model_validator(mode="after")
    def require_coherent_recovery(self) -> Self:
        if self.independent_settlement != self.independent_observation.ledger:
            raise ValueError("recovery ledger must match the independent observation")
        if self.state is RecoveryState.NOT_SUBMITTED:
            if not self.proof_of_non_submission or self.independent_settlement is not None:
                raise ValueError("non-submission recovery is incoherent")
        elif self.state is RecoveryState.SUBMITTED:
            if self.proof_of_non_submission or self.independent_settlement is None:
                raise ValueError("submitted recovery requires independent settlement evidence")
        elif self.proof_of_non_submission or self.independent_settlement is not None:
            raise ValueError("unresolved recovery cannot assert submission certainty")
        return self


async def recover_x402_submission(
    result: ExternalSignerResult,
    rpc: ReadOnlyRpcPort,
    profile: SettlementProfile,
    *,
    scheme: str,
    observed_at: datetime,
) -> X402SubmissionRecovery:
    transaction_reference = result.transaction_reference
    source = "evm_rpc:" + profile.network
    if result.submission_state in {
        SignerSubmissionState.NOT_SUBMITTED,
        SignerSubmissionState.PROVEN_NOT_SUBMITTED,
    }:
        observation = unavailable_settlement(
            "NO_TRANSACTION_REFERENCE",
            source=source,
            observed_at=observed_at,
            profile=profile,
            transaction_reference=transaction_reference,
        )
        return X402SubmissionRecovery(
            state=RecoveryState.NOT_SUBMITTED,
            proof_of_non_submission=True,
            source_submission_state=result.submission_state,
            transaction_reference=transaction_reference,
            independent_settlement=None,
            independent_observation=observation,
        )
    if transaction_reference is None:
        observation = unavailable_settlement(
            "NO_TRANSACTION_REFERENCE",
            source=source,
            observed_at=observed_at,
            profile=profile,
        )
        return X402SubmissionRecovery(
            state=RecoveryState.UNRESOLVED,
            proof_of_non_submission=False,
            source_submission_state=result.submission_state,
            transaction_reference=None,
            independent_settlement=None,
            independent_observation=observation,
        )
    observation = await observe_exact_erc20_transfer(
        rpc,
        profile,
        transaction_reference,
        source=source,
        protocol="x402",
        scheme=scheme,
        observed_at=observed_at,
    )
    if observation.status in {
        IndependentSettlementStatus.CONFIRMED,
        IndependentSettlementStatus.FAILED,
    }:
        return X402SubmissionRecovery(
            state=RecoveryState.SUBMITTED,
            proof_of_non_submission=False,
            source_submission_state=result.submission_state,
            transaction_reference=transaction_reference,
            independent_settlement=observation.ledger,
            independent_observation=observation,
        )
    diagnostic = None
    if observation.status is IndependentSettlementStatus.UNAVAILABLE:
        diagnostic = X402RecoveryDiagnostic.RPC_UNAVAILABLE
    elif observation.diagnostic != "RECEIPT_PENDING":
        diagnostic = X402RecoveryDiagnostic.EVIDENCE_INVALID
    return X402SubmissionRecovery(
        state=RecoveryState.UNRESOLVED,
        proof_of_non_submission=False,
        source_submission_state=result.submission_state,
        transaction_reference=transaction_reference,
        independent_settlement=None,
        independent_observation=observation,
        diagnostic=diagnostic,
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
        evidence_class=(
            EvidenceClass.INDEPENDENT_OBSERVATION
            if source == "x402.base_sepolia.transaction_receipt"
            else EvidenceClass.PROVIDER_ASSERTION
        ),
        data=data,
        observed_at=observed_at,
        transaction_reference=recovery.transaction_reference,
    )
