"""Strict canonical records shared across SettleDiff boundaries."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Annotated, Self

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    StringConstraints,
    model_validator,
)

from settlediff.domain.money import Money


def require_utc(value: datetime) -> datetime:
    if value.utcoffset() != timedelta(0):
        raise ValueError("timestamp must be timezone-aware UTC")
    return value.astimezone(UTC)


UtcDatetime = Annotated[datetime, AfterValidator(require_utc)]
NonEmptyStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
EvidenceValue = JsonValue | Money


class Verdict(StrEnum):
    VERIFIED = "VERIFIED"
    VERIFIED_WITH_WARNINGS = "VERIFIED_WITH_WARNINGS"
    PAID_FAILURE = "PAID_FAILURE"
    PAYMENT_FAILURE = "PAYMENT_FAILURE"
    UNVERIFIABLE = "UNVERIFIABLE"


class CheckStatus(StrEnum):
    PASS = "PASS"
    WARN = "WARN"
    DIFF = "DIFF"
    FAIL = "FAIL"
    UNKNOWN = "UNKNOWN"


class Severity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    HIGH = "high"


class ArtifactType(StrEnum):
    SERVICE_CONTRACT = "service_contract"
    EXECUTION = "execution"
    PAYMENT_RECEIPT = "payment_receipt"
    SERVICE_RESPONSE = "service_response"
    ACTIVITY = "activity"
    CONTEXT_EVIDENCE = "context_evidence"


class SettlementStatus(StrEnum):
    SETTLED = "settled"
    FAILED = "failed"
    PENDING = "pending"
    UNKNOWN = "unknown"


class LedgerStatus(StrEnum):
    CONFIRMED = "confirmed"
    FAILED = "failed"
    PENDING = "pending"
    UNKNOWN = "unknown"


class CanonicalModel(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)


class PurchaseIntent(CanonicalModel):
    schema_version: int = Field(default=1, ge=1)
    run_id: NonEmptyStr
    task: NonEmptyStr
    max_budget: Money
    requested_service: NonEmptyStr | None
    created_at: UtcDatetime


class ExpectedContract(CanonicalModel):
    schema_version: int = Field(default=1, ge=1)
    vendor_slug: NonEmptyStr
    url: NonEmptyStr
    price: Money | None
    asset: NonEmptyStr | None
    protocol: NonEmptyStr | None
    chain: NonEmptyStr | None
    request_schema: dict[str, JsonValue]


class ExecutionRecord(CanonicalModel):
    schema_version: int = Field(default=1, ge=1)
    vendor_slug: NonEmptyStr | None
    upstream_http_status: int | None = Field(default=None, ge=100, le=599)
    charge: Money | None
    asset: NonEmptyStr | None
    protocol: NonEmptyStr | None
    chain: NonEmptyStr | None
    recipient: NonEmptyStr | None
    settlement_status: SettlementStatus
    transaction_id: NonEmptyStr | None
    session_id: NonEmptyStr | None
    transaction_hash: NonEmptyStr | None
    response_body: JsonValue | None
    executed_at: UtcDatetime


class LedgerRecord(CanonicalModel):
    schema_version: int = Field(default=1, ge=1)
    ledger_id: NonEmptyStr
    vendor_slug: NonEmptyStr | None
    amount: Money | None
    asset: NonEmptyStr | None
    protocol: NonEmptyStr | None
    chain: NonEmptyStr | None
    recipient: NonEmptyStr | None
    status: LedgerStatus
    error_reason: str | None
    transaction_id: NonEmptyStr | None
    session_id: NonEmptyStr | None
    transaction_hash: NonEmptyStr | None
    occurred_at: UtcDatetime


class EvidenceArtifact(CanonicalModel):
    schema_version: int = Field(default=1, ge=1)
    artifact_id: NonEmptyStr
    artifact_type: ArtifactType
    source: NonEmptyStr
    collected_at: UtcDatetime
    redacted: bool
    data: JsonValue


class Finding(CanonicalModel):
    schema_version: int = Field(default=1, ge=1)
    finding_id: NonEmptyStr
    check_id: NonEmptyStr
    severity: Severity
    status: CheckStatus
    expected: EvidenceValue | None
    observed: EvidenceValue | None
    message: NonEmptyStr
    artifact_ids: tuple[NonEmptyStr, ...]
    field_paths: tuple[NonEmptyStr, ...]

    @model_validator(mode="after")
    def require_observed_citation(self) -> Self:
        if self.observed is not None and not self.artifact_ids:
            raise ValueError("an observed value requires at least one artifact citation")
        return self


class MachineReport(CanonicalModel):
    schema_version: int = Field(default=1, ge=1)
    run_id: NonEmptyStr
    intent: PurchaseIntent
    contract: ExpectedContract | None
    execution: ExecutionRecord | None
    ledger: LedgerRecord | None
    findings: tuple[Finding, ...]
    verdict: Verdict


class InvestigationExplanation(CanonicalModel):
    schema_version: int = Field(default=1, ge=1)
    run_id: NonEmptyStr
    summary: NonEmptyStr
    evidence_used: tuple[NonEmptyStr, ...]
    finding_ids: tuple[NonEmptyStr, ...]
    deterministic_verdict: Verdict
    recommended_next_step: NonEmptyStr | None
