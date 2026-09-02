"""x402 v2 implementation of the rail-neutral payment adapter."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Protocol, cast

from pydantic import JsonValue, ValidationError

from settlediff.application.auth import (
    AuthorizationError,
    ConsumedPaidAuthorization,
    PaidExecutionCapability,
    PaidExecutionRequest,
    PaymentTerms,
)
from settlediff.application.payment_rails import AdapterEvidence, AdapterProtocolError
from settlediff.domain.models import (
    ArtifactType,
    ExecutionRecord,
    ExpectedContract,
    LedgerStatus,
    PaymentReceipt,
    SettlementStatus,
)
from settlediff.domain.money import Money
from settlediff.x402.client import X402SubmissionUncertainError
from settlediff.x402.client_contract import (
    ExternalSignerRequest,
    ExternalSignerResult,
    SignerSubmissionState,
)
from settlediff.x402.http import X402ResourcePort
from settlediff.x402.models import PaymentRequired, PaymentRequirements, SettlementResponse
from settlediff.x402.normalize import normalize_payment_required, normalize_payment_response
from settlediff.x402.parser import parse_payment_required
from settlediff.x402.recovery import (
    ReadOnlyRpcPort,
    X402SubmissionRecovery,
    recover_x402_submission,
    x402_recovery_evidence,
)


class X402SignerPort(Protocol):
    async def execute_once(self, request: ExternalSignerRequest) -> ExternalSignerResult: ...


class X402Adapter:
    adapter_id = "x402"

    def __init__(
        self,
        resource: X402ResourcePort,
        signer: X402SignerPort,
        rpc: ReadOnlyRpcPort,
    ) -> None:
        self._resource = resource
        self._signer = signer
        self._rpc = rpc
        self._preflight_contract: ExpectedContract | None = None
        self._recovery: X402SubmissionRecovery | None = None

    async def inspect(self, request: PaidExecutionRequest) -> AdapterEvidence:
        observed = await self._resource.challenge(request)
        required, _, contract = self._contract_from_challenge(
            observed.status_code, observed.payment_required, request
        )
        self._preflight_contract = contract
        return AdapterEvidence(
            adapter_id=self.adapter_id,
            protocol_version=str(required.x402_version),
            operation="inspect",
            source="x402.payment_required",
            artifact_type=ArtifactType.SERVICE_CONTRACT,
            data=cast(JsonValue, contract.model_dump(mode="json")),
            observed_at=observed.observed_at,
        )

    async def execute_once(
        self,
        authorization: ConsumedPaidAuthorization,
        request: PaidExecutionRequest,
        quoted_price: Money,
    ) -> AdapterEvidence:
        authorization.require_exact_request(request)
        preflight_contract = self._preflight_contract
        if preflight_contract is None:
            raise AdapterProtocolError("x402 execution requires preflight evidence")
        if preflight_contract.price != quoted_price:
            raise AdapterProtocolError("x402 execution quote differs from preflight")
        observed = await self._resource.challenge(request)
        _, requirement, contract = self._contract_from_challenge(
            observed.status_code, observed.payment_required, request
        )
        terms = _payment_terms(contract, request)
        try:
            authorization.require_exact_payment_terms(terms)
        except AuthorizationError as error:
            raise AdapterProtocolError("x402 payment terms changed after preflight") from error
        signer_request = ExternalSignerRequest(
            run_id=request.run_id,
            selected_requirement=0,
            target=request.target,
            method=request.method,
            body=request.body,
            body_digest=PaidExecutionCapability.body_digest_for(request.body),
            max_budget=request.budget,
            network="eip155:84532",
            scheme="exact",
            payment_terms_digest=terms.digest,
        )
        result = await self._signer.execute_once(signer_request)
        try:
            returned_requirement = self._require_returned_terms(result, request, terms)
            provider_receipt = self._provider_receipt(
                result, returned_requirement, observed.observed_at
            )
        except X402SubmissionUncertainError:
            self._recovery = await recover_x402_submission(
                result,
                self._rpc,
                requirement,
                expected_payer=None,
                observed_at=observed.observed_at,
            )
            execution = _execution(
                result,
                requirement,
                contract,
                None,
                self._recovery,
                observed.observed_at,
                force_unknown=True,
            )
            return _execution_evidence(result, execution, observed.observed_at, None, True)
        self._recovery = await recover_x402_submission(
            result,
            self._rpc,
            returned_requirement,
            expected_payer=(
                _provider_settlement(result).payer
                if result.provider_settlement is not None
                else None
            ),
            observed_at=observed.observed_at,
        )
        execution = _execution(
            result,
            returned_requirement,
            contract,
            provider_receipt,
            self._recovery,
            observed.observed_at,
        )
        return _execution_evidence(
            result,
            execution,
            observed.observed_at,
            provider_receipt,
            result.submission_state is SignerSubmissionState.SUBMISSION_UNCERTAIN,
        )

    async def collect_activity(self) -> AdapterEvidence:
        records: list[JsonValue] = []
        if self._recovery is not None and self._recovery.independent_settlement is not None:
            records.append(
                cast(JsonValue, self._recovery.independent_settlement.model_dump(mode="json"))
            )
        return AdapterEvidence(
            adapter_id=self.adapter_id,
            protocol_version="2",
            operation="activity",
            source="x402.base_sepolia.transaction_receipt",
            artifact_type=ArtifactType.ACTIVITY,
            data=records,
            observed_at=datetime.now(UTC),
        )

    async def collect_transaction(self, transaction_reference: str) -> AdapterEvidence:
        recovery = self._recovery
        if recovery is None or recovery.transaction_reference != transaction_reference:
            raise AdapterProtocolError("x402 recovery evidence is unavailable for this transaction")
        return x402_recovery_evidence(recovery, observed_at=datetime.now(UTC))

    @staticmethod
    def _contract_from_challenge(
        status_code: int,
        payment_required: str | None,
        request: PaidExecutionRequest,
    ) -> tuple[PaymentRequired, PaymentRequirements, ExpectedContract]:
        if status_code != 402:
            raise AdapterProtocolError("x402 unsigned request did not return HTTP 402")
        required = parse_payment_required(payment_required)
        contract = normalize_payment_required(
            required, request_schema=_request_schema(request.body)
        )
        if required.resource.url != request.target or contract.url != request.target:
            raise AdapterProtocolError("x402 challenge resource differs from the exact target")
        try:
            requirement = required.selected_requirement()
        except ValueError as error:
            raise AdapterProtocolError("x402 primary payment requirement is unsupported") from error
        return required, requirement, contract

    @staticmethod
    def _require_returned_terms(
        result: ExternalSignerResult,
        request: PaidExecutionRequest,
        selected_terms: PaymentTerms,
    ) -> PaymentRequirements:
        try:
            required = PaymentRequired.model_validate_json(
                json.dumps(result.challenge), strict=True
            )
            contract = normalize_payment_required(
                required, request_schema=_request_schema(request.body)
            )
            returned_terms = _payment_terms(contract, request)
        except (ValidationError, ValueError) as error:
            raise X402SubmissionUncertainError(
                "x402 signer returned an invalid challenge after possible submission"
            ) from error
        if returned_terms.digest != selected_terms.digest:
            raise X402SubmissionUncertainError(
                "x402 signer challenge changed after possible submission"
            )
        try:
            return required.selected_requirement()
        except ValueError as error:
            raise X402SubmissionUncertainError(
                "x402 signer selected an unsupported payment requirement"
            ) from error

    @staticmethod
    def _provider_receipt(
        result: ExternalSignerResult,
        requirement: PaymentRequirements,
        observed_at: datetime,
    ) -> PaymentReceipt | None:
        if result.provider_settlement is None:
            return None
        try:
            settlement = _provider_settlement(result)
            if (
                result.transaction_reference is not None
                and settlement.transaction != result.transaction_reference
            ):
                raise ValueError("provider and signer transaction references differ")
            return normalize_payment_response(settlement, requirement, issued_at=observed_at)
        except (ValidationError, ValueError) as error:
            raise X402SubmissionUncertainError(
                "x402 signer returned invalid settlement evidence after possible submission"
            ) from error


def _execution_evidence(
    result: ExternalSignerResult,
    execution: ExecutionRecord,
    observed_at: datetime,
    provider_receipt: PaymentReceipt | None,
    submission_uncertain: bool,
) -> AdapterEvidence:
    return AdapterEvidence(
        adapter_id="x402",
        protocol_version="2",
        operation="execute",
        source="x402.external_signer",
        artifact_type=ArtifactType.EXECUTION,
        data=cast(JsonValue, execution.model_dump(mode="json")),
        observed_at=observed_at,
        submission_uncertain=submission_uncertain,
        payment_reference=result.payment_reference,
        transaction_reference=result.transaction_reference,
        provider_receipt=(
            cast(JsonValue, provider_receipt.model_dump(mode="json"))
            if provider_receipt is not None
            else None
        ),
    )


def _provider_settlement(result: ExternalSignerResult) -> SettlementResponse:
    if result.provider_settlement is None:
        raise ValueError("provider settlement evidence is unavailable")
    return SettlementResponse.model_validate(result.provider_settlement)


def _request_schema(body: JsonValue | None) -> dict[str, JsonValue]:
    if body is None:
        schema_type = "null"
    elif isinstance(body, dict):
        schema_type = "object"
    elif isinstance(body, list):
        schema_type = "array"
    elif isinstance(body, bool):
        schema_type = "boolean"
    elif isinstance(body, str):
        schema_type = "string"
    else:
        schema_type = "number"
    return {"type": schema_type}


def _payment_terms(contract: ExpectedContract, request: PaidExecutionRequest) -> PaymentTerms:
    if contract.price is None:
        raise AdapterProtocolError("x402 challenge omitted its quoted price")
    return PaymentTerms(
        adapter_id="x402",
        protocol_version="2",
        scheme=contract.scheme,
        network=contract.network,
        chain=contract.chain,
        asset=contract.asset_identity,
        asset_symbol=contract.asset,
        recipient=contract.recipient,
        quoted_price=contract.price,
        max_timeout_seconds=contract.max_timeout_seconds,
        resource_url=contract.url,
        method=request.method,
        body_digest=PaidExecutionCapability.body_digest_for(request.body),
    )


def _execution(
    result: ExternalSignerResult,
    requirement: PaymentRequirements,
    contract: ExpectedContract,
    provider_receipt: PaymentReceipt | None,
    recovery: X402SubmissionRecovery,
    observed_at: datetime,
    *,
    force_unknown: bool = False,
) -> ExecutionRecord:
    independent = recovery.independent_settlement
    if force_unknown:
        settlement_status = SettlementStatus.UNKNOWN
        charge = None
    elif provider_receipt is not None:
        settlement_status = provider_receipt.settlement_status
        charge = provider_receipt.amount
    elif independent is not None and independent.status is LedgerStatus.CONFIRMED:
        settlement_status = SettlementStatus.SETTLED
        charge = independent.amount
    elif independent is not None and independent.status is LedgerStatus.FAILED:
        settlement_status = SettlementStatus.FAILED
        charge = None
    else:
        settlement_status = SettlementStatus.UNKNOWN
        charge = None
    return ExecutionRecord(
        vendor_slug=None,
        upstream_http_status=result.service_response.status,
        charge=charge,
        asset=contract.asset,
        protocol="x402",
        chain=None,
        recipient=requirement.pay_to,
        scheme=requirement.scheme,
        network=requirement.network,
        asset_identity=contract.asset_identity,
        settlement_status=settlement_status,
        transaction_id=None,
        session_id=None,
        transaction_hash=result.transaction_reference,
        response_body=result.service_response.body,
        executed_at=observed_at,
        normalization_notes=(
            ("signer evidence contradicted authorized payment terms",) if force_unknown else ()
        ),
    )
