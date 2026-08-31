"""Perflo implementation of the rail-neutral evidence adapter."""

from __future__ import annotations

from typing import Protocol, cast

from pydantic import JsonValue

from settlediff.application.auth import ConsumedPaidAuthorization, PaidExecutionRequest
from settlediff.application.payment_rails import AdapterEvidence, AdapterProtocolError
from settlediff.domain.models import ArtifactType
from settlediff.domain.money import Money
from settlediff.perflo.parser import PerfloEnvelope, PerfloSuccessEnvelope


class PerfloClientPort(Protocol):
    async def inspect_service(self, target: str) -> PerfloEnvelope: ...

    async def get_schema(self, slug: str) -> PerfloEnvelope: ...

    async def execute(
        self,
        authorization: ConsumedPaidAuthorization,
        request: PaidExecutionRequest,
        quoted_price: Money,
    ) -> PerfloEnvelope: ...

    async def get_activity(self) -> PerfloEnvelope: ...

    async def transaction_status(self, transaction_hash: str) -> PerfloEnvelope: ...


class PerfloAdapter:
    adapter_id = "perflo"

    def __init__(self, client: PerfloClientPort) -> None:
        self._client = client

    async def inspect(self, request: PaidExecutionRequest) -> AdapterEvidence:
        data = _result_data(await self._client.inspect_service(request.target), field="contract")
        return _evidence("inspect", "perflo.check", ArtifactType.SERVICE_CONTRACT, data)

    async def collect_schema(self, slug: str) -> AdapterEvidence:
        data = _result_data(await self._client.get_schema(slug), field="schema")
        return _evidence("schema", "perflo.schema", ArtifactType.CONTEXT_EVIDENCE, data)

    async def execute_once(
        self,
        authorization: ConsumedPaidAuthorization,
        request: PaidExecutionRequest,
        quoted_price: Money,
    ) -> AdapterEvidence:
        data = _result_data(await self._client.execute(authorization, request, quoted_price))
        return _evidence(
            "execute",
            "perflo.fetch",
            ArtifactType.EXECUTION,
            data,
            payment_reference=_reference(data, "transaction_id", "transactionId"),
            transaction_reference=_reference(data, "transaction_hash", "transactionHash", "txHash"),
        )

    async def collect_activity(self) -> AdapterEvidence:
        data = _activity_data(await self._client.get_activity())
        return _evidence("activity", "perflo.activity", ArtifactType.ACTIVITY, data)

    async def collect_transaction(self, transaction_reference: str) -> AdapterEvidence:
        data = _result_data(await self._client.transaction_status(transaction_reference))
        return _evidence(
            "transaction_status",
            "perflo.tx_status",
            ArtifactType.PAYMENT_RECEIPT,
            data,
            transaction_reference=transaction_reference,
        )


def _evidence(
    operation: str,
    source: str,
    artifact_type: ArtifactType,
    data: JsonValue,
    *,
    payment_reference: str | None = None,
    transaction_reference: str | None = None,
) -> AdapterEvidence:
    return AdapterEvidence(
        adapter_id="perflo",
        operation=operation,
        source=source,
        artifact_type=artifact_type,
        data=data,
        payment_reference=payment_reference,
        transaction_reference=transaction_reference,
    )


def _result_data(envelope: PerfloEnvelope, *, field: str = "result") -> JsonValue:
    if not isinstance(envelope, PerfloSuccessEnvelope):
        raise AdapterProtocolError("Perflo returned an error envelope after the client accepted it")
    result = envelope.payload.get(field)
    if result is None and field != "result":
        result = envelope.payload.get("result")
    if result is None:
        raise AdapterProtocolError(f"Perflo success envelope did not include {field} evidence")
    return cast(JsonValue, result)


def _activity_data(envelope: PerfloEnvelope) -> JsonValue:
    if not isinstance(envelope, PerfloSuccessEnvelope):
        raise AdapterProtocolError("Perflo returned an error envelope after the client accepted it")
    legacy = envelope.payload.get("result")
    if legacy is not None:
        return cast(JsonValue, legacy)
    agent = envelope.payload.get("agent")
    transactions = (
        cast(dict[str, JsonValue], agent).get("transactions") if isinstance(agent, dict) else None
    )
    if not isinstance(transactions, list):
        raise AdapterProtocolError(
            "Perflo success envelope did not include agent transaction evidence"
        )
    return cast(JsonValue, transactions)


def _reference(data: JsonValue, *fields: str) -> str | None:
    if not isinstance(data, dict):
        return None
    mapping = cast(dict[str, JsonValue], data)
    values = tuple(mapping[field] for field in fields if isinstance(mapping.get(field), str))
    if not values:
        return None
    if len(set(values)) != 1:
        raise AdapterProtocolError("Perflo evidence contains conflicting transaction references")
    return cast(str, values[0])
