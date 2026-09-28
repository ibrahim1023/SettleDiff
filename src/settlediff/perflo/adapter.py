"""Perflo implementation of the rail-neutral evidence adapter."""

from __future__ import annotations

import re
from typing import Protocol, cast

from pydantic import JsonValue

from settlediff.application.auth import (
    CatalogResourceReference,
    ConsumedPaidAuthorization,
    PaidExecutionRequest,
)
from settlediff.application.payment_rails import AdapterEvidence, AdapterProtocolError
from settlediff.domain.models import ArtifactType
from settlediff.domain.money import Money
from settlediff.perflo.parser import PerfloEnvelope, PerfloSuccessEnvelope

_TX_HASH_PATTERN = re.compile(r"^0x[0-9a-fA-F]{64}$")
_PAY_STATUSES = frozenset({"succeeded", "running", "indeterminate", "failed"})
_TX_STATUSES = frozenset({"submitted", "processing", "executing", "success", "failed"})


class PerfloClientPort(Protocol):
    async def inspect_service(self, slug: str) -> PerfloEnvelope: ...

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
        if not isinstance(request.resource, CatalogResourceReference):
            raise AdapterProtocolError(
                "Perflo v8 vendor inspection requires a catalog resource reference"
            )
        return await self._vendor_evidence(request.resource.slug)

    async def reinspect(self, request: PaidExecutionRequest) -> AdapterEvidence:
        if not isinstance(request.resource, CatalogResourceReference):
            raise AdapterProtocolError(
                "Perflo v8 vendor inspection requires a catalog resource reference"
            )
        return await self._vendor_evidence(request.resource.slug)

    async def _vendor_evidence(self, slug: str) -> AdapterEvidence:
        vendor = _payload_object(await self._client.inspect_service(slug), "vendor")
        evidence = _evidence("inspect", "perflo.vendor", ArtifactType.SERVICE_CONTRACT, vendor)
        return evidence.model_copy(update={"source_contract": vendor})

    async def collect_schema(self, slug: str) -> AdapterEvidence:
        data = _payload_object(await self._client.get_schema(slug), "vendor")
        return _evidence("schema", "perflo.schema", ArtifactType.CONTEXT_EVIDENCE, data)

    async def execute_once(
        self,
        authorization: ConsumedPaidAuthorization,
        request: PaidExecutionRequest,
        quoted_price: Money,
    ) -> AdapterEvidence:
        data = _payload_object(
            await self._client.execute(authorization, request, quoted_price), "result"
        )
        status = data.get("status")
        if not isinstance(status, str) or status not in _PAY_STATUSES:
            raise AdapterProtocolError("Perflo pay result did not include a declared status")
        return _evidence(
            "execute",
            "perflo.pay",
            ArtifactType.EXECUTION,
            data,
            payment_reference=_string_field(data, "transactionId"),
            transaction_reference=_settlement_reference(data),
        )

    async def collect_activity(self) -> AdapterEvidence:
        envelope = await self._client.get_activity()
        if not isinstance(envelope, PerfloSuccessEnvelope):
            raise AdapterProtocolError(
                "Perflo returned an error envelope after the client accepted it"
            )
        agent = envelope.payload.get("agent")
        if not isinstance(agent, dict):
            raise AdapterProtocolError(
                "Perflo success envelope did not include agent activity evidence"
            )
        agent_data = cast(dict[str, JsonValue], agent)
        if not isinstance(agent_data.get("rows"), list) or not isinstance(
            agent_data.get("meta"), dict
        ):
            raise AdapterProtocolError(
                "Perflo agent activity evidence requires rows and meta objects"
            )
        return _evidence("activity", "perflo.activity.agent", ArtifactType.ACTIVITY, agent_data)

    async def collect_transaction(self, transaction_reference: str) -> AdapterEvidence:
        envelope = await self._client.transaction_status(transaction_reference)
        if not isinstance(envelope, PerfloSuccessEnvelope):
            raise AdapterProtocolError(
                "Perflo returned an error envelope after the client accepted it"
            )
        data = {key: value for key, value in envelope.payload.items() if key != "ok"}
        status = data.get("status")
        if not isinstance(status, str) or status not in _TX_STATUSES:
            raise AdapterProtocolError(
                "Perflo transaction status did not include a declared status"
            )
        returned = data.get("txHash")
        if not isinstance(returned, str) or not _same_transaction_hash(
            returned, transaction_reference
        ):
            raise AdapterProtocolError(
                "Perflo transaction status did not report the requested transaction hash"
            )
        return _evidence(
            "transaction_status",
            "perflo.tx_status",
            ArtifactType.PAYMENT_RECEIPT,
            cast(JsonValue, data),
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


def _payload_object(envelope: PerfloEnvelope, field: str) -> dict[str, JsonValue]:
    if not isinstance(envelope, PerfloSuccessEnvelope):
        raise AdapterProtocolError("Perflo returned an error envelope after the client accepted it")
    value = envelope.payload.get(field)
    if value is None:
        raise AdapterProtocolError(f"Perflo success envelope did not include {field} evidence")
    if not isinstance(value, dict):
        raise AdapterProtocolError(f"Perflo {field} evidence must be a JSON object")
    return cast(dict[str, JsonValue], value)


def _string_field(data: JsonValue, field: str) -> str | None:
    if not isinstance(data, dict):
        return None
    value = cast(dict[str, JsonValue], data).get(field)
    return value if isinstance(value, str) and value.strip() else None


def _settlement_reference(data: JsonValue) -> str | None:
    if not isinstance(data, dict):
        return None
    result = cast(dict[str, JsonValue], data)
    if result.get("chargedTo") == "credit":
        return None
    settlement = result.get("settlement")
    if not isinstance(settlement, dict):
        return None
    tx_hash = cast(dict[str, JsonValue], settlement).get("txHash")
    if isinstance(tx_hash, str) and tx_hash.strip():
        return tx_hash
    return None


def _same_transaction_hash(returned: str, requested: str) -> bool:
    if _TX_HASH_PATTERN.fullmatch(requested):
        return returned.lower() == requested.lower()
    return returned == requested
