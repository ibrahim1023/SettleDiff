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

from settlediff.domain.integrity import Sha256Digest, sha256_digest
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


class DeliveryStatus(StrEnum):
    SATISFIED = "SATISFIED"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"
    NOT_ASSESSED = "NOT_ASSESSED"


class RetrySafety(StrEnum):
    SAFE_TO_RETRY = "SAFE_TO_RETRY"
    DO_NOT_RETRY = "DO_NOT_RETRY"
    REQUIRES_HUMAN_DECISION = "REQUIRES_HUMAN_DECISION"


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


class ResponseContract(CanonicalModel):
    schema_version: int = Field(default=1, ge=1, le=1)
    media_type: (
        Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)]
        | None
    ) = None
    json_schema: dict[str, JsonValue] | None = None
    source_fields: tuple[NonEmptyStr, ...] = Field(min_length=1, max_length=8)

    @model_validator(mode="after")
    def require_advertised_promise(self) -> Self:
        if self.media_type is None and self.json_schema is None:
            raise ValueError("response contract requires an advertised media type or JSON schema")
        return self

    @property
    def digest(self) -> Sha256Digest:
        return sha256_digest(self.model_dump(mode="json"))


class DeliveryObservation(CanonicalModel):
    schema_version: int = Field(default=1, ge=1, le=1)
    observed_at: UtcDatetime
    status_code: int = Field(ge=100, le=599)
    media_type: (
        Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)]
        | None
    )
    received_bytes: int = Field(ge=0, le=100_000_000)
    truncated: bool
    parsed_body: JsonValue | None
    evidence_ids: tuple[NonEmptyStr, ...] = Field(min_length=1, max_length=16)

    @model_validator(mode="after")
    def require_coherent_body_evidence(self) -> Self:
        if self.truncated and self.parsed_body is not None:
            raise ValueError("truncated delivery observation cannot contain a parsed body")
        return self


class DeliveryAssessment(CanonicalModel):
    schema_version: int = Field(default=1, ge=1, le=1)
    status: DeliveryStatus
    reason_code: NonEmptyStr
    evidence_ids: tuple[NonEmptyStr, ...] = Field(min_length=1, max_length=16)
    observation: DeliveryObservation | None = None
    response_contract_digest: Sha256Digest | None = None

    @model_validator(mode="after")
    def require_coherent_assessment(self) -> Self:
        if self.status in {DeliveryStatus.SATISFIED, DeliveryStatus.FAILED} and (
            self.observation is None or self.response_contract_digest is None
        ):
            raise ValueError("assessed delivery requires an observation and response contract")
        if self.status is DeliveryStatus.NOT_ASSESSED and (
            self.observation is not None or self.response_contract_digest is not None
        ):
            raise ValueError(
                "unassessed delivery cannot contain an observation or response contract"
            )
        return self


class RetryAssessment(CanonicalModel):
    schema_version: int = Field(default=1, ge=1, le=1)
    safety: RetrySafety
    reason_codes: tuple[NonEmptyStr, ...] = Field(min_length=1, max_length=16)
    evidence_ids: tuple[NonEmptyStr, ...] = Field(min_length=1, max_length=16)


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
    schema_version: int = Field(default=2, ge=1, le=4)
    vendor_slug: NonEmptyStr | None
    url: NonEmptyStr | None
    price: Money | None
    asset: NonEmptyStr | None
    protocol: NonEmptyStr | None
    chain: NonEmptyStr | None
    request_schema: dict[str, JsonValue] | None = None
    required_max_charge: Money | None = Field(default=None, exclude_if=lambda value: value is None)
    payable: bool | None = Field(default=None, exclude_if=lambda value: value is None)
    scheme: NonEmptyStr | None = None
    network: Caip2Network | None = None
    asset_identity: AssetIdentity | None = None
    recipient: NonEmptyStr | None = None
    max_timeout_seconds: int | None = Field(default=None, gt=0, le=86_400)
    response_contract: ResponseContract | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
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
        if self.schema_version < 3 and "response_contract" in self.model_fields_set:
            raise ValueError(
                f"schema version {self.schema_version} cannot contain response_contract"
            )
        if self.schema_version < 4 and "required_max_charge" in self.model_fields_set:
            raise ValueError(
                f"schema version {self.schema_version} cannot contain required_max_charge"
            )
        if self.schema_version < 4 and "payable" in self.model_fields_set:
            raise ValueError(f"schema version {self.schema_version} cannot contain payable")
        if self.schema_version < 4 and self.url is None:
            raise ValueError(f"schema version {self.schema_version} requires a resource URL")
        if self.url is None and self.vendor_slug is None:
            raise ValueError("contract requires a resource URL or vendor slug identity")
        return self

    @property
    def digest(self) -> Sha256Digest:
        return sha256_digest(self.model_dump(mode="json"))


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
    schema_version: int = Field(default=2, ge=1, le=3)
    run_id: NonEmptyStr
    intent: PurchaseIntent
    contract: ExpectedContract | None
    execution: ExecutionRecord | None
    ledger: LedgerRecord | None
    findings: tuple[Finding, ...]
    verdict: Verdict
    receipt: PaymentReceipt | None = None
    adapter_id: NonEmptyStr | None = None
    delivery: DeliveryAssessment | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    retry: RetryAssessment | None = Field(default=None, exclude_if=lambda value: value is None)

    @model_validator(mode="after")
    def require_compatible_schema(self) -> Self:
        require_v2_fields(self.schema_version, (("receipt", self.receipt),))
        future_fields = tuple(
            field for field in ("delivery", "retry") if field in self.model_fields_set
        )
        if self.schema_version < 3 and future_fields:
            raise ValueError(
                f"schema version {self.schema_version} cannot contain {', '.join(future_fields)}"
            )
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
