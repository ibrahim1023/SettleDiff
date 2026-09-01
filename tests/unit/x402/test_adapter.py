from __future__ import annotations

import base64
import json
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import cast

import pytest
from pydantic import JsonValue

from settlediff.application.auth import (
    ConsumedPaidAuthorization,
    PaidExecutionCapability,
    PaidExecutionRequest,
    PaymentTerms,
)
from settlediff.application.run import LiveEvidenceCollector
from settlediff.contextdev.client import ContextEvidencePort
from settlediff.domain.models import (
    ArtifactType,
    ExpectedContract,
    LedgerStatus,
    SettlementStatus,
    Verdict,
)
from settlediff.domain.money import Money
from settlediff.x402.adapter import X402Adapter
from settlediff.x402.client_contract import (
    ExternalSignerRequest,
    ExternalSignerResult,
    SignerServiceResponse,
    SignerSubmissionState,
)
from settlediff.x402.http import X402ResourceResponse
from settlediff.x402.parser import parse_payment_required
from settlediff.x402.recovery import TRANSFER_TOPIC

FIXTURE = Path(__file__).parents[2] / "contract/x402/fixtures/payment-required-v2.json"
NOW = datetime(2026, 9, 1, tzinfo=UTC)
TX_HASH = "0x" + "2" * 64
PAYER = "0x3333333333333333333333333333333333333333"
TARGET = "https://example.invalid/paid"


def required_payload() -> dict[str, JsonValue]:
    return cast(
        dict[str, JsonValue],
        parse_payment_required(required_header()).model_dump(mode="json", by_alias=True),
    )


def required_header(payload: dict[str, JsonValue] | None = None) -> str:
    if payload is None:
        loaded = json.loads(FIXTURE.read_text())
        assert isinstance(loaded, dict)
        payload = cast(dict[str, JsonValue], loaded)
        resource = cast(dict[str, JsonValue], payload["resource"])
        resource["url"] = TARGET
    return base64.b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode()


def request() -> PaidExecutionRequest:
    return PaidExecutionRequest(
        run_id="syn_x402_adapter",
        target=TARGET,
        method="POST",
        body={"query": "synthetic"},
        budget=Money(amount=Decimal("0.01"), unit="USDC"),
    )


def response(header: str) -> X402ResourceResponse:
    return X402ResourceResponse(
        status_code=402,
        payment_required=header,
        body={"error": "payment required"},
        observed_at=NOW,
    )


def address_topic(address: str) -> str:
    return "0x" + "0" * 24 + address[2:].lower()


def receipt() -> dict[str, JsonValue]:
    selected = parse_payment_required(required_header()).accepts[0]
    return {
        "transactionHash": TX_HASH,
        "status": "0x1",
        "from": "0x5555555555555555555555555555555555555555",
        "logs": [
            {
                "address": selected.asset.lower(),
                "topics": [
                    TRANSFER_TOPIC,
                    address_topic(PAYER),
                    address_topic(selected.pay_to),
                ],
                "data": "0x" + int(selected.amount).to_bytes(32, "big").hex(),
            }
        ],
    }


class FakeResource:
    def __init__(self, *responses: X402ResourceResponse) -> None:
        self.responses = list(responses)
        self.requests: list[PaidExecutionRequest] = []

    async def challenge(self, request: PaidExecutionRequest) -> X402ResourceResponse:
        self.requests.append(request)
        return self.responses.pop(0)


class FakeSigner:
    def __init__(self, result: ExternalSignerResult) -> None:
        self.result = result
        self.requests: list[ExternalSignerRequest] = []

    async def execute_once(self, request: ExternalSignerRequest) -> ExternalSignerResult:
        self.requests.append(request)
        return self.result


class FakeRpc:
    def __init__(self, value: JsonValue) -> None:
        self.value = value
        self.calls: list[str] = []

    async def call(self, method: str, params: tuple[JsonValue, ...]) -> JsonValue:
        del params
        self.calls.append(method)
        return "0x14a34" if method == "eth_chainId" else self.value


def signer_result(
    *,
    challenge: dict[str, JsonValue] | None = None,
    state: SignerSubmissionState = SignerSubmissionState.SUBMITTED_CONFIRMED,
) -> ExternalSignerResult:
    provider: dict[str, JsonValue] | None = {
        "success": True,
        "errorReason": None,
        "payer": PAYER,
        "transaction": TX_HASH,
        "network": "eip155:84532",
        "amount": "1000",
        "extensions": {},
    }
    if state is SignerSubmissionState.SUBMISSION_UNCERTAIN:
        provider = None
    return ExternalSignerResult(
        adapter="x402",
        submission_state=state,
        challenge=challenge or required_payload(),
        provider_settlement=provider,
        service_response=SignerServiceResponse(status=200, body={"result": "synthetic"}),
        payment_reference="syn_x402_payment",
        transaction_reference=TX_HASH,
        notes=(),
    )


def payment_terms(contract: ExpectedContract, value: PaidExecutionRequest) -> PaymentTerms:
    assert contract.price is not None
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
        method=value.method,
        body_digest=PaidExecutionCapability.body_digest_for(value.body),
    )


async def authorization(
    contract: ExpectedContract, value: PaidExecutionRequest
) -> ConsumedPaidAuthorization:
    terms = payment_terms(contract, value)
    return await PaidExecutionCapability.issue(
        value,
        payment_terms=terms,
        expires_at=NOW + timedelta(minutes=5),
    ).consume(value, payment_terms=terms, now=NOW)


@pytest.mark.asyncio
async def test_adapter_revalidates_terms_and_keeps_provider_and_independent_evidence() -> None:
    resource = FakeResource(response(required_header()), response(required_header()))
    signer = FakeSigner(signer_result())
    rpc = FakeRpc(receipt())
    adapter = X402Adapter(resource, signer, rpc)
    value = request()

    inspected = await adapter.inspect(value)
    contract = ExpectedContract.model_validate_json(json.dumps(inspected.data))
    executed = await adapter.execute_once(
        await authorization(contract, value), value, cast(Money, contract.price)
    )
    activity = await adapter.collect_activity()

    assert inspected.adapter_id == "x402"
    assert inspected.protocol_version == "2"
    assert inspected.artifact_type is ArtifactType.SERVICE_CONTRACT
    assert len(resource.requests) == 2
    assert len(signer.requests) == 1
    assert signer.requests[0].selected_requirement == 0
    assert signer.requests[0].payment_terms_digest == payment_terms(contract, value).digest
    assert executed.artifact_type is ArtifactType.EXECUTION
    assert executed.provider_receipt is not None
    assert executed.transaction_reference == TX_HASH
    assert executed.submission_uncertain is False
    assert activity.artifact_type is ArtifactType.ACTIVITY
    records = cast(list[JsonValue], activity.data)
    assert len(records) == 1
    assert cast(dict[str, JsonValue], records[0])["status"] == "confirmed"
    assert rpc.calls == ["eth_chainId", "eth_getTransactionReceipt"]


@pytest.mark.asyncio
async def test_changed_preflight_terms_fail_before_signer_invocation() -> None:
    changed = deepcopy(required_payload())
    accepts = cast(list[JsonValue], changed["accepts"])
    cast(dict[str, JsonValue], accepts[0])["payTo"] = "0x4444444444444444444444444444444444444444"
    resource = FakeResource(response(required_header()), response(required_header(changed)))
    signer = FakeSigner(signer_result())
    adapter = X402Adapter(resource, signer, FakeRpc(receipt()))
    value = request()
    inspected = await adapter.inspect(value)
    contract = ExpectedContract.model_validate_json(json.dumps(inspected.data))

    with pytest.raises(ValueError, match="terms changed"):
        await adapter.execute_once(
            await authorization(contract, value), value, cast(Money, contract.price)
        )

    assert signer.requests == []


@pytest.mark.asyncio
async def test_signer_returned_challenge_drift_is_submission_uncertain() -> None:
    changed = deepcopy(required_payload())
    accepts = cast(list[JsonValue], changed["accepts"])
    cast(dict[str, JsonValue], accepts[0])["payTo"] = "0x4444444444444444444444444444444444444444"
    resource = FakeResource(response(required_header()), response(required_header()))
    signer = FakeSigner(signer_result(challenge=changed))
    adapter = X402Adapter(resource, signer, FakeRpc(receipt()))
    value = request()
    contract = ExpectedContract.model_validate_json(json.dumps((await adapter.inspect(value)).data))

    executed = await adapter.execute_once(
        await authorization(contract, value), value, cast(Money, contract.price)
    )

    assert executed.submission_uncertain is True
    assert executed.transaction_reference == TX_HASH
    assert executed.provider_receipt is None
    assert cast(dict[str, JsonValue], executed.data)["settlement_status"] == "unknown"
    assert len(signer.requests) == 1


@pytest.mark.asyncio
async def test_uncertain_submission_exposes_read_only_recovery_without_resubmission() -> None:
    resource = FakeResource(response(required_header()), response(required_header()))
    signer = FakeSigner(signer_result(state=SignerSubmissionState.SUBMISSION_UNCERTAIN))
    adapter = X402Adapter(resource, signer, FakeRpc(None))
    value = request()
    contract = ExpectedContract.model_validate_json(json.dumps((await adapter.inspect(value)).data))

    executed = await adapter.execute_once(
        await authorization(contract, value), value, cast(Money, contract.price)
    )
    recovered = await adapter.collect_transaction(TX_HASH)

    assert executed.submission_uncertain is True
    assert recovered.operation == "transaction_status"
    assert cast(dict[str, JsonValue], recovered.data)["status"] == "unresolved"
    assert len(signer.requests) == 1


@pytest.mark.asyncio
async def test_collector_keeps_provider_receipt_separate_from_independent_ledger() -> None:
    adapter = X402Adapter(
        FakeResource(response(required_header()), response(required_header())),
        FakeSigner(signer_result()),
        FakeRpc(receipt()),
    )
    collector = LiveEvidenceCollector(adapter, cast(ContextEvidencePort, object()))
    value = request()
    await collector.preflight(value)
    terms = collector.payment_terms
    consumed = await PaidExecutionCapability.issue(
        value,
        payment_terms=terms,
        expires_at=NOW + timedelta(minutes=5),
    ).consume(value, payment_terms=terms, now=NOW)

    await collector.execute(consumed, value)
    report = await collector.verify(value)

    assert report.verdict is Verdict.VERIFIED
    assert report.adapter_id == "x402"
    assert report.receipt is not None
    assert report.receipt.settlement_status is SettlementStatus.SETTLED
    assert report.ledger is not None
    assert report.ledger.status is LedgerStatus.CONFIRMED
    assert report.receipt is not report.ledger
    assert {artifact.artifact_type for artifact in collector.artifacts} >= {
        ArtifactType.PAYMENT_RECEIPT,
        ArtifactType.ACTIVITY,
    }
