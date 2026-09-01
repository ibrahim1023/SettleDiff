"""Rail-neutral paid-execution evidence contracts."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from settlediff.application.auth import ConsumedPaidAuthorization, PaidExecutionRequest
from settlediff.domain.models import ArtifactType, NonEmptyStr, UtcDatetime
from settlediff.domain.money import Money


class AdapterProtocolError(ValueError):
    pass


class SubmissionUncertainError(RuntimeError):
    pass


class AdapterEvidence(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    schema_version: int = Field(default=1, ge=1)
    adapter_id: NonEmptyStr
    protocol_version: NonEmptyStr | None = None
    operation: NonEmptyStr
    source: NonEmptyStr
    artifact_type: ArtifactType
    data: JsonValue
    observed_at: UtcDatetime | None = None
    submission_uncertain: bool = False
    payment_reference: NonEmptyStr | None = None
    transaction_reference: NonEmptyStr | None = None


@runtime_checkable
class PaymentRailAdapter(Protocol):
    adapter_id: str

    async def inspect(self, request: PaidExecutionRequest) -> AdapterEvidence: ...

    async def execute_once(
        self,
        authorization: ConsumedPaidAuthorization,
        request: PaidExecutionRequest,
        quoted_price: Money,
    ) -> AdapterEvidence: ...

    async def collect_activity(self) -> AdapterEvidence: ...


@runtime_checkable
class SchemaEvidencePort(Protocol):
    async def collect_schema(self, slug: str) -> AdapterEvidence: ...


@runtime_checkable
class TransactionEvidencePort(Protocol):
    async def collect_transaction(self, transaction_reference: str) -> AdapterEvidence: ...
