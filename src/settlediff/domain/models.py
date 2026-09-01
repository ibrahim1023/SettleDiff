"""Strict canonical records shared across SettleDiff boundaries."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
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
CAIP2_NETWORK_PATTERN = r"^[a-z0-9-]{3,8}:[A-Za-z0-9_-]{1,32}$"
Caip2Network = Annotated[
    str,
    StringConstraints(strip_whitespace=True, pattern=CAIP2_NETWORK_PATTERN),
]
EvidenceValue = Money | JsonValue


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


class ExplanationSource(StrEnum):
    PROVIDER = "provider"
    FALLBACK = "fallback"


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


def require_v2_fields(schema_version: int, fields: tuple[tuple[str, object | None], ...]) -> None:
    present = tuple(name for name, value in fields if value is not None)
    if schema_version < 2 and present:
        raise ValueError(f"schema version {schema_version} cannot contain {', '.join(present)}")


class AssetIdentity(CanonicalModel):
    schema_version: int = Field(default=1, ge=1)
    symbol: NonEmptyStr
    network: Caip2Network
    reference: NonEmptyStr
    decimals: int = Field(ge=0, le=255)


class PurchaseIntent(CanonicalModel):
    schema_version: int = Field(default=1, ge=1)
    run_id: NonEmptyStr
    task: NonEmptyStr
    max_budget: Money
    requested_service: NonEmptyStr | None
    created_at: UtcDatetime


class ExpectedContract(CanonicalModel):
    schema_version: int = Field(default=2, ge=1)
    vendor_slug: NonEmptyStr | None
    url: NonEmptyStr
    price: Money | None
    asset: NonEmptyStr | None
    protocol: NonEmptyStr | None
    chain: NonEmptyStr | None
    request_schema: dict[str, JsonValue]
    scheme: NonEmptyStr | None = None
    network: Caip2Network | None = None
    asset_identity: AssetIdentity | None = None
    recipient: NonEmptyStr | None = None
    max_timeout_seconds: int | None = Field(default=None, gt=0, le=86_400)
    normalization_notes: tuple[NonEmptyStr, ...] = ()

    @model_validator(mode="after")
    def require_compatible_schema(self) -> Self:
        require_v2_fields(
            self.schema_version,
            (
                ("scheme", self.scheme),
                ("network", self.network),
                ("asset_identity", self.asset_identity),
                ("recipient", self.recipient),
                ("max_timeout_seconds", self.max_timeout_seconds),
            ),
        )
        return self


class ExecutionRecord(CanonicalModel):
    schema_version: int = Field(default=2, ge=1)
    vendor_slug: NonEmptyStr | None
    upstream_http_status: int | None = Field(default=None, ge=100, le=599)
    charge: Money | None
    asset: NonEmptyStr | None
    protocol: NonEmptyStr | None
    chain: NonEmptyStr | None
    recipient: NonEmptyStr | None
    scheme: NonEmptyStr | None = None
    network: Caip2Network | None = None
    asset_identity: AssetIdentity | None = None
    settlement_status: SettlementStatus
    transaction_id: NonEmptyStr | None
    session_id: NonEmptyStr | None
    transaction_hash: NonEmptyStr | None
    response_body: JsonValue | None
    executed_at: UtcDatetime | None
    normalization_notes: tuple[NonEmptyStr, ...] = ()

    @model_validator(mode="after")
    def require_compatible_schema(self) -> Self:
        require_v2_fields(
            self.schema_version,
            (
                ("scheme", self.scheme),
                ("network", self.network),
                ("asset_identity", self.asset_identity),
            ),
        )
        return self


class PaymentReceipt(CanonicalModel):
    schema_version: int = Field(default=2, ge=1)
    amount: Money | None
    asset: NonEmptyStr | None
    protocol: NonEmptyStr | None
    chain: NonEmptyStr | None
    recipient: NonEmptyStr | None
    scheme: NonEmptyStr | None = None
    network: Caip2Network | None = None
    asset_identity: AssetIdentity | None = None
    settlement_status: SettlementStatus
    transaction_id: NonEmptyStr | None
    session_id: NonEmptyStr | None
    transaction_hash: NonEmptyStr | None
    issued_at: UtcDatetime | None
    normalization_notes: tuple[NonEmptyStr, ...] = ()

    @model_validator(mode="after")
    def require_compatible_schema(self) -> Self:
        require_v2_fields(
            self.schema_version,
            (
                ("scheme", self.scheme),
                ("network", self.network),
                ("asset_identity", self.asset_identity),
            ),
        )
        return self


class LedgerRecord(CanonicalModel):
    schema_version: int = Field(default=2, ge=1)
    ledger_id: NonEmptyStr
    vendor_slug: NonEmptyStr | None
    amount: Money | None
    asset: NonEmptyStr | None
    protocol: NonEmptyStr | None
    chain: NonEmptyStr | None
    recipient: NonEmptyStr | None
    scheme: NonEmptyStr | None = None
    network: Caip2Network | None = None
    asset_identity: AssetIdentity | None = None
    status: LedgerStatus
    error_reason: str | None
    transaction_id: NonEmptyStr | None
    session_id: NonEmptyStr | None
    transaction_hash: NonEmptyStr | None
    occurred_at: UtcDatetime
    normalization_notes: tuple[NonEmptyStr, ...] = ()

    @model_validator(mode="after")
    def require_compatible_schema(self) -> Self:
        require_v2_fields(
            self.schema_version,
            (
                ("scheme", self.scheme),
                ("network", self.network),
                ("asset_identity", self.asset_identity),
            ),
        )
        return self


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
    schema_version: int = Field(default=2, ge=1)
    run_id: NonEmptyStr
    intent: PurchaseIntent
    contract: ExpectedContract | None
    execution: ExecutionRecord | None
    ledger: LedgerRecord | None
    findings: tuple[Finding, ...]
    verdict: Verdict
    receipt: PaymentReceipt | None = None
    adapter_id: NonEmptyStr | None = None

    @model_validator(mode="after")
    def require_compatible_schema(self) -> Self:
        require_v2_fields(self.schema_version, (("receipt", self.receipt),))
        return self


class InvestigationExplanation(CanonicalModel):
    schema_version: int = Field(default=1, ge=1)
    run_id: NonEmptyStr
    summary: NonEmptyStr
    evidence_used: tuple[NonEmptyStr, ...]
    finding_ids: tuple[NonEmptyStr, ...]
    deterministic_verdict: Verdict
    recommended_next_step: NonEmptyStr | None


class ExplanationRecord(CanonicalModel):
    schema_version: int = Field(default=1, ge=1)
    explanation: InvestigationExplanation
    source: ExplanationSource
    tool_calls: int = Field(ge=0, le=25)
    model_requests: int = Field(default=0, ge=0, le=10)
    input_tokens: int = Field(default=0, ge=0, le=100_000)
    output_tokens: int = Field(default=0, ge=0, le=10_000)
    model_cost: Decimal | None = Field(default=None, ge=Decimal("0"), le=Decimal("1000"))
    rejected_output: str | None = Field(default=None, min_length=1, max_length=2048)
