"""Versioned contract for an independently owned x402 signer process."""

from __future__ import annotations

import hashlib
import json
from enum import StrEnum
from typing import Annotated, Literal, Self, cast
from urllib.parse import urlsplit

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    StringConstraints,
    field_validator,
    model_validator,
)

from settlediff.domain.models import NonEmptyStr
from settlediff.domain.money import Money

Sha256Digest = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
_FORBIDDEN_OUTPUT_KEYS = frozenset(
    {
        "authorization",
        "credential",
        "mnemonic",
        "paymentpayload",
        "paymentsignature",
        "privatekey",
        "seed",
        "signature",
    }
)


class SignerSubmissionState(StrEnum):
    NOT_SUBMITTED = "not_submitted"
    SUBMITTED_CONFIRMED = "submitted_confirmed"
    SUBMISSION_UNCERTAIN = "submission_uncertain"
    PROVEN_NOT_SUBMITTED = "proven_not_submitted"


class SignerContractModel(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)


class ExternalSignerRequest(SignerContractModel):
    schema_version: Literal[1] = 1
    adapter: Literal["x402"] = "x402"
    run_id: NonEmptyStr
    x402_version: Literal[2] = 2
    selected_requirement: Literal[0] = 0
    target: NonEmptyStr
    method: Literal["GET", "POST"]
    body: JsonValue | None
    body_digest: Sha256Digest
    max_budget: Money
    network: Literal["eip155:84532"]
    scheme: Literal["exact"]
    payment_terms_digest: Sha256Digest

    @field_validator("target")
    @classmethod
    def require_safe_target(cls, value: str) -> str:
        parsed = urlsplit(value)
        if (
            parsed.scheme != "https"
            or not parsed.netloc
            or parsed.username is not None
            or parsed.password is not None
            or parsed.fragment
        ):
            raise ValueError("signer target must be absolute HTTPS without credentials or fragment")
        return value

    @model_validator(mode="after")
    def require_bound_body_and_budget(self) -> Self:
        if self.body_digest != body_digest_for(self.body):
            raise ValueError("body digest does not match the canonical request body")
        if self.max_budget.amount <= 0 or self.max_budget.unit != "USDC":
            raise ValueError("x402 signer budget must be positive USDC")
        return self


class SignerServiceResponse(SignerContractModel):
    status: int = Field(ge=100, le=599)
    body: JsonValue | None


class ExternalSignerResult(SignerContractModel):
    schema_version: Literal[1] = 1
    adapter: Literal["x402"]
    submission_state: SignerSubmissionState
    challenge: dict[str, JsonValue]
    provider_settlement: dict[str, JsonValue] | None
    service_response: SignerServiceResponse
    payment_reference: NonEmptyStr | None
    transaction_reference: NonEmptyStr | None
    notes: tuple[str, ...]

    @model_validator(mode="after")
    def require_safe_coherent_result(self) -> Self:
        payload = cast(JsonValue, self.model_dump(mode="json"))
        if _contains_forbidden_key(payload):
            raise ValueError("signer result contains secret-bearing payment material")
        if self.submission_state is SignerSubmissionState.SUBMITTED_CONFIRMED and (
            self.provider_settlement is None or self.transaction_reference is None
        ):
            raise ValueError("confirmed submission requires settlement and transaction evidence")
        if self.submission_state is SignerSubmissionState.NOT_SUBMITTED and (
            self.provider_settlement is not None or self.transaction_reference is not None
        ):
            raise ValueError("non-submission contradicts transaction or settlement evidence")
        if (
            self.submission_state is SignerSubmissionState.PROVEN_NOT_SUBMITTED
            and self.provider_settlement is not None
        ):
            raise ValueError("proven non-submission contradicts settlement evidence")
        return self


def body_digest_for(body: JsonValue | None) -> str:
    encoded = json.dumps(
        body,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _contains_forbidden_key(value: JsonValue) -> bool:
    if isinstance(value, dict):
        mapping = cast(dict[str, JsonValue], value)
        for key, child in mapping.items():
            normalized = "".join(character for character in key.lower() if character.isalnum())
            if normalized in _FORBIDDEN_OUTPUT_KEYS or _contains_forbidden_key(child):
                return True
    elif isinstance(value, list):
        return any(_contains_forbidden_key(child) for child in cast(list[JsonValue], value))
    return False
