"""x402 v2 implementation of the rail-neutral payment adapter."""

from __future__ import annotations

import json
from collections.abc import Callable
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
from settlediff.domain.integrity import sha256_digest
from settlediff.domain.models import (
    ArtifactType,
    AssetIdentity,
    DeliveryObservation,
    EvidenceClass,
    ExecutionRecord,
    ExpectedContract,
    IndependentSettlementObservation,
    LedgerStatus,
    PaymentReceipt,
    ProviderSignatureEvidence,
    SettlementProfile,
    SettlementStatus,
)
from settlediff.domain.money import Money
from settlediff.observers.evm_transfer import ReadOnlyRpcPort
from settlediff.x402.client import X402SubmissionUncertainError
from settlediff.x402.client_contract import (
    ExternalSignerRequest,
    ExternalSignerResult,
    SignerSubmissionState,
)
from settlediff.x402.http import SellerIdentityPort, X402ResourcePort
from settlediff.x402.models import PaymentRequired, PaymentRequirements, SettlementResponse
from settlediff.x402.normalize import (
    USDC_DECIMALS,
    normalize_payment_required,
    normalize_payment_response,
)
from settlediff.x402.parser import parse_payment_required
from settlediff.x402.recovery import (
    X402SubmissionRecovery,
    recover_x402_submission,
    x402_recovery_evidence,
)
from settlediff.x402.signed import (
    SignedProviderOffer,
    SignedProviderReceipt,
    assess_signed_offer,
    assess_signed_receipt,
    seller_key_id,
)
from settlediff.x402.signed_extension import extract_offer, extract_receipt


class X402SignerPort(Protocol):
    async def execute_once(self, request: ExternalSignerRequest) -> ExternalSignerResult: ...


class X402Adapter:
    adapter_id = "x402"

    def __init__(
        self,
        resource: X402ResourcePort,
        signer: X402SignerPort | None = None,
        rpc: ReadOnlyRpcPort | None = None,
        *,
        expected_payer: str | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._resource = resource
        self._signer = signer
        self._rpc = rpc
        self._expected_payer = expected_payer
        self._clock = clock or (lambda: datetime.now(UTC))
        self._preflight_offer: SignedProviderOffer | None = None
        self._preflight_contract: ExpectedContract | None = None
        self._preflight_profile: SettlementProfile | None = None
        self._recovery: X402SubmissionRecovery | None = None

    @classmethod
    def for_inspection(cls, resource: X402ResourcePort) -> X402Adapter:
        """Build an unsigned-only adapter that cannot sign, pay, or query chain state."""
        return cls(resource)

    def _require_paid_ports(self) -> None:
        if self._signer is None or self._rpc is None or self._expected_payer is None:
            raise AdapterProtocolError(
                "x402 inspection-only adapter cannot execute or recover payments"
            )

    async def inspect(self, request: PaidExecutionRequest) -> AdapterEvidence:
        observed = await self._resource.challenge(request)
        required, requirement, contract = self._contract_from_challenge(
            observed.status_code, observed.payment_required, request
        )
        profile = None
        if self._expected_payer is not None:
            profile = _settlement_profile(requirement, self._expected_payer)
            self._preflight_profile = profile
        contract, offer = await self._capture_offer(required, contract, request, profile)
        self._preflight_contract = contract
        self._preflight_offer = offer
        return AdapterEvidence(
            adapter_id=self.adapter_id,
            protocol_version=str(required.x402_version),
            operation="inspect",
            source="x402.payment_required",
            artifact_type=ArtifactType.SERVICE_CONTRACT,
            data=cast(JsonValue, contract.model_dump(mode="json")),
            observed_at=observed.observed_at,
            source_contract=cast(JsonValue, required.model_dump(mode="json", by_alias=True)),
            settlement_profile=profile,
            provider_signatures=(_signature_evidence(offer, request.run_id),)
            if offer is not None
            else (),
        )

    async def _capture_offer(
        self,
        required: PaymentRequired,
        contract: ExpectedContract,
        request: PaidExecutionRequest,
        profile: SettlementProfile | None,
    ) -> tuple[ExpectedContract, SignedProviderOffer | None]:
        try:
            signature = extract_offer(required.extensions)
            if signature is None:
                return contract, None
            if not isinstance(self._resource, SellerIdentityPort):
                raise ValueError("seller identity capture is unavailable")
            identity = await self._resource.seller_identity(
                request.target, seller_key_id(signature)
            )
            offer = assess_signed_offer(
                signature, identity, _payment_terms(contract, request, profile), now=self._clock()
            )
            if offer.diagnostic != "SIGNED_OFFER_VALID":
                raise ValueError("seller offer is not valid for the authorized terms")
        except (ValueError, RecursionError) as error:
            raise AdapterProtocolError("x402 signed offer could not be verified safely") from error
        digest = sha256_digest(
            {
                "signature": sha256_digest(signature),
                "identity_document": identity.document_digest,
            }
        )
        return contract.model_copy(
            update={"schema_version": 4, "signed_offer_digest": digest}
        ), offer

    async def _capture_receipt(
        self,
        result: ExternalSignerResult,
        terms: PaymentTerms,
        request: PaidExecutionRequest,
    ) -> tuple[ProviderSignatureEvidence, ...]:
        offer = self._preflight_offer
        if offer is None:
            return ()
        offer_evidence = _signature_evidence(offer, request.run_id)
        try:
            if result.provider_settlement is None or not isinstance(
                self._resource, SellerIdentityPort
            ):
                raise ValueError("signed receipt is unavailable")
            extensions = result.provider_settlement.get("extensions", {})
            if not isinstance(extensions, dict):
                raise ValueError("signed receipt extensions are malformed")
            signature = extract_receipt(extensions)
            if signature is None:
                raise ValueError("advertised signed receipt is missing")
            identity = await self._resource.seller_identity(
                request.target, seller_key_id(signature)
            )
            observation = self.independent_settlement()
            receipt = assess_signed_receipt(
                signature,
                identity,
                terms,
                provider_reference=result.transaction_reference,
                observer_reference=observation.transaction_reference
                if observation is not None
                else None,
                observed_at=self._clock(),
            )
            return offer_evidence, _signature_evidence(receipt, request.run_id)
        except (ValueError, RecursionError):
            return offer_evidence, ProviderSignatureEvidence(
                kind="receipt",
                signature_validity="UNVERIFIABLE",
                seller_identity_binding="UNVERIFIABLE",
                signed_terms_match=None,
                correlation="NOT_COMPARABLE",
                diagnostic="SIGNED_RECEIPT_UNAVAILABLE",
                signature_digest=None,
                signer_identity_digest=None,
                payload_digest=None,
                observed_at=self._clock(),
                evidence_ids=(f"{request.run_id}:signed_receipt",),
            )

    async def execute_once(
        self,
        authorization: ConsumedPaidAuthorization,
        request: PaidExecutionRequest,
        quoted_price: Money,
    ) -> AdapterEvidence:
        self._require_paid_ports()
        authorization.require_exact_request(request)
        signer = cast(X402SignerPort, self._signer)
        rpc = cast(ReadOnlyRpcPort, self._rpc)
        preflight_contract = self._preflight_contract
        if preflight_contract is None:
            raise AdapterProtocolError("x402 execution requires preflight evidence")
        if preflight_contract.price != quoted_price:
            raise AdapterProtocolError("x402 execution quote differs from preflight")
        observed = await self._resource.challenge(request)
        required, requirement, contract = self._contract_from_challenge(
            observed.status_code, observed.payment_required, request
        )
        expected_payer = cast(str, self._expected_payer)
        profile = _settlement_profile(requirement, expected_payer)
        contract, _offer = await self._capture_offer(required, contract, request, profile)
        if self._preflight_profile is None:
            raise AdapterProtocolError("x402 execution requires a preflight settlement profile")
        if profile != self._preflight_profile:
            raise AdapterProtocolError("x402 settlement profile changed after preflight")
        terms = _payment_terms(contract, request, profile)
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
        result = await signer.execute_once(signer_request)
        observation = _delivery_observation(result, request)
        try:
            if result.transaction_reference is not None and (
                result.payer is None or result.payer.casefold() != expected_payer.casefold()
            ):
                raise X402SubmissionUncertainError(
                    "x402 signer payer differs from the pre-authorized payer"
                )
            returned_requirement = self._require_returned_terms(result, request, terms)
            provider_receipt = self._provider_receipt(
                result, returned_requirement, observed.observed_at
            )
        except X402SubmissionUncertainError:
            self._recovery = await recover_x402_submission(
                result,
                rpc,
                self._preflight_profile,
                scheme=requirement.scheme,
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
            return _execution_evidence(
                result,
                execution,
                observed.observed_at,
                None,
                True,
                observation,
                await self._capture_receipt(result, terms, request),
            )
        self._recovery = await recover_x402_submission(
            result,
            rpc,
            self._preflight_profile,
            scheme=requirement.scheme,
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
            observation,
            await self._capture_receipt(result, terms, request),
        )

    def independent_settlement(self) -> IndependentSettlementObservation | None:
        return self._recovery.independent_observation if self._recovery is not None else None

    async def collect_activity(self) -> AdapterEvidence:
        self._require_paid_ports()
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
            evidence_class=EvidenceClass.INDEPENDENT_OBSERVATION,
            data=records,
            observed_at=datetime.now(UTC),
        )

    async def collect_transaction(self, transaction_reference: str) -> AdapterEvidence:
        self._require_paid_ports()
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

    def _require_returned_terms(
        self,
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
            returned_offer = extract_offer(required.extensions)
            expected_offer = (
                self._preflight_offer.jws if self._preflight_offer is not None else None
            )
            if returned_offer != expected_offer:
                raise ValueError("signed offer changed after possible submission")
            if selected_terms.signed_offer_digest is not None:
                contract = contract.model_copy(
                    update={
                        "schema_version": 4,
                        "signed_offer_digest": selected_terms.signed_offer_digest,
                    }
                )
        except (ValidationError, ValueError) as error:
            raise X402SubmissionUncertainError(
                "x402 signer returned an invalid challenge after possible submission"
            ) from error
        try:
            requirement = required.selected_requirement()
        except ValueError as error:
            raise X402SubmissionUncertainError(
                "x402 signer selected an unsupported payment requirement"
            ) from error
        try:
            returned_terms = _payment_terms(
                contract,
                request,
                _settlement_profile(requirement, cast(str, self._expected_payer)),
            )
        except (ValidationError, ValueError) as error:
            raise X402SubmissionUncertainError(
                "x402 signer returned an invalid challenge after possible submission"
            ) from error
        if returned_terms.digest != selected_terms.digest:
            raise X402SubmissionUncertainError(
                "x402 signer challenge changed after possible submission"
            )
        return requirement

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
            if (
                result.payer is None
                or settlement.payer is None
                or settlement.payer.casefold() != result.payer.casefold()
            ):
                raise ValueError("provider and signer payer references differ")
            return normalize_payment_response(settlement, requirement, issued_at=observed_at)
        except (ValidationError, ValueError) as error:
            raise X402SubmissionUncertainError(
                "x402 signer returned invalid settlement evidence after possible submission"
            ) from error


def _delivery_observation(
    result: ExternalSignerResult, request: PaidExecutionRequest
) -> DeliveryObservation | None:
    service_response = result.service_response
    if service_response is None:
        return None
    return DeliveryObservation(
        observed_at=datetime.now(UTC),
        status_code=service_response.status,
        media_type=service_response.media_type,
        received_bytes=service_response.received_bytes,
        truncated=service_response.truncated,
        parsed_body=service_response.parsed_body,
        evidence_ids=(f"{request.run_id}:execution",),
    )


def _execution_evidence(
    result: ExternalSignerResult,
    execution: ExecutionRecord,
    observed_at: datetime,
    provider_receipt: PaymentReceipt | None,
    submission_uncertain: bool,
    delivery_observation: DeliveryObservation | None,
    provider_signatures: tuple[ProviderSignatureEvidence, ...] = (),
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
        delivery_observation=delivery_observation,
        provider_signatures=provider_signatures,
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


def _settlement_profile(requirement: PaymentRequirements, payer: str) -> SettlementProfile:
    return SettlementProfile(
        network=requirement.network,
        chain_id=int(requirement.network.split(":", 1)[1]),
        asset_identity=AssetIdentity(
            symbol="USDC",
            network=requirement.network,
            reference=requirement.asset,
            decimals=USDC_DECIMALS,
        ),
        atomic_amount=int(requirement.amount),
        recipient=requirement.pay_to,
        payer=payer,
    )


def _payment_terms(
    contract: ExpectedContract,
    request: PaidExecutionRequest,
    profile: SettlementProfile | None,
) -> PaymentTerms:
    if contract.price is None:
        raise AdapterProtocolError("x402 challenge omitted its quoted price")
    if contract.url is None:
        raise AdapterProtocolError("x402 challenge omitted its resource URL")
    payload: dict[str, object] = dict(
        schema_version=4 if profile is not None else 2,
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
        response_contract_digest=(
            contract.response_contract.digest if contract.response_contract is not None else None
        ),
    )
    if profile is not None:
        payload.update(
            {
                "payer": profile.payer,
                "settlement_profile_digest": sha256_digest(profile.model_dump(mode="json")),
            }
        )
        if contract.signed_offer_digest is not None:
            payload["signed_offer_digest"] = contract.signed_offer_digest
    return PaymentTerms.model_validate(payload)


def _signature_evidence(
    value: SignedProviderOffer | SignedProviderReceipt,
    run_id: str,
) -> ProviderSignatureEvidence:
    kind = "offer" if isinstance(value, SignedProviderOffer) else "receipt"
    match = (
        value.signed_terms_match
        if isinstance(value, SignedProviderOffer)
        else (
            value.diagnostic
            in {
                "SIGNED_RECEIPT_VALID",
                "SIGNED_RECEIPT_TRANSACTION_MISSING",
                "SIGNED_RECEIPT_TRANSACTION_UNAVAILABLE",
                "SIGNED_RECEIPT_TRANSACTION_DIFF",
            }
        )
    )
    methods = value.identity.document.get("verificationMethod")
    selected = (
        tuple(
            method
            for method in methods
            if isinstance(method, dict) and method.get("id") == value.identity.kid
        )
        if isinstance(methods, list)
        else ()
    )
    identity_digest = sha256_digest(
        {"did": value.identity.did, "kid": value.identity.kid, "methods": selected}
    )
    return ProviderSignatureEvidence.model_validate(
        {
            "kind": kind,
            "signature_validity": value.verification.signature_validity.value,
            "seller_identity_binding": value.verification.seller_identity_binding.value,
            "signed_terms_match": match,
            "correlation": value.correlation if isinstance(value, SignedProviderReceipt) else None,
            "diagnostic": value.diagnostic,
            "signature_digest": sha256_digest(value.jws),
            "signer_identity_digest": identity_digest,
            "payload_digest": value.verification.payload_digest,
            "observed_at": value.captured_at,
            "evidence_ids": (f"{run_id}:signed_{kind}",),
        }
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
    service_response = result.service_response
    return ExecutionRecord(
        vendor_slug=None,
        upstream_http_status=service_response.status if service_response is not None else None,
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
        response_body=service_response.parsed_body if service_response is not None else None,
        executed_at=observed_at,
        normalization_notes=(
            ("signer evidence contradicted authorized payment terms",) if force_unknown else ()
        ),
    )
