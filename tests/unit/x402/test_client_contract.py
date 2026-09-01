from __future__ import annotations

import hashlib
import json
from decimal import Decimal

import pytest
from pydantic import JsonValue, ValidationError

from settlediff.domain.money import Money
from settlediff.x402.client_contract import (
    ExternalSignerRequest,
    ExternalSignerResult,
    SignerSubmissionState,
    body_digest_for,
)

BODY: JsonValue = {"query": "synthetic"}
BODY_DIGEST = hashlib.sha256(
    json.dumps(BODY, sort_keys=True, separators=(",", ":")).encode()
).hexdigest()
TERMS_DIGEST = "a" * 64


def request() -> ExternalSignerRequest:
    return ExternalSignerRequest(
        run_id="syn_run",
        target="https://example.invalid/paid",
        method="POST",
        body=BODY,
        body_digest=BODY_DIGEST,
        max_budget=Money(amount=Decimal("0.001"), unit="USDC"),
        network="eip155:84532",
        scheme="exact",
        payment_terms_digest=TERMS_DIGEST,
    )


def result(**updates: object) -> ExternalSignerResult:
    values: dict[str, object] = {
        "adapter": "x402",
        "submission_state": SignerSubmissionState.SUBMITTED_CONFIRMED,
        "challenge": {"x402Version": 2},
        "provider_settlement": {"success": True},
        "service_response": {"status": 200, "body": {"value": "synthetic"}},
        "payment_reference": "syn_payment",
        "transaction_reference": "syn_transaction",
        "notes": (),
    }
    values.update(updates)
    return ExternalSignerResult.model_validate(values)


def test_signer_request_binds_body_and_selected_terms() -> None:
    value = request()

    assert value.schema_version == 1
    assert value.adapter == "x402"
    assert value.x402_version == 2
    assert value.selected_requirement == 0
    assert value.body_digest == body_digest_for(BODY)
    assert value.payment_terms_digest == TERMS_DIGEST
    assert value.network == "eip155:84532"
    assert value.scheme == "exact"


@pytest.mark.parametrize(
    "updates",
    [
        {"adapter": "perflo"},
        {"x402_version": 1},
        {"selected_requirement": 1},
        {"target": "http://example.invalid/paid"},
        {"target": "https://user@example.invalid/paid"},
        {"target": "https://example.invalid/paid#fragment"},
        {"method": "DELETE"},
        {"body_digest": "b" * 64},
        {"network": "eip155:8453"},
        {"scheme": "upto"},
        {"payment_terms_digest": "not-a-digest"},
        {"max_budget": Money(amount=Decimal("0"), unit="USDC")},
        {"max_budget": Money(amount=Decimal("0.001"), unit="EUR")},
    ],
)
def test_signer_request_rejects_unbound_or_unsupported_terms(
    updates: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        ExternalSignerRequest.model_validate({**request().model_dump(), **updates})


def test_absent_body_has_one_canonical_digest() -> None:
    value = ExternalSignerRequest(
        run_id="syn_get",
        target="https://example.invalid/weather",
        method="GET",
        body=None,
        body_digest=body_digest_for(None),
        max_budget=Money(amount=Decimal("0.001"), unit="USDC"),
        network="eip155:84532",
        scheme="exact",
        payment_terms_digest=TERMS_DIGEST,
    )

    assert value.body_digest == hashlib.sha256(b"null").hexdigest()


def test_signer_result_is_strict_and_carries_no_payment_payload() -> None:
    value = result()

    assert value.schema_version == 1
    assert value.submission_state is SignerSubmissionState.SUBMITTED_CONFIRMED
    assert not hasattr(value, "payment_payload")
    assert ExternalSignerResult.model_validate_json(value.model_dump_json()) == value
    with pytest.raises(ValidationError):
        ExternalSignerResult.model_validate({**value.model_dump(), "invented": True})


@pytest.mark.parametrize(
    "unsafe",
    [
        {"authorization": "syn_payment_authorization"},
        {"PAYMENT-SIGNATURE": "syn_signature"},
        {"paymentPayload": {"safe": False}},
        {"nested": {"signature": "syn_signature"}},
        {"private_key": "syn_private_key"},
        {"mnemonic": "synthetic words"},
    ],
)
def test_signer_result_rejects_secret_bearing_material(unsafe: dict[str, object]) -> None:
    with pytest.raises(ValidationError, match="secret-bearing"):
        result(challenge=unsafe)


@pytest.mark.parametrize(
    "state",
    [
        SignerSubmissionState.NOT_SUBMITTED,
        SignerSubmissionState.SUBMISSION_UNCERTAIN,
        SignerSubmissionState.PROVEN_NOT_SUBMITTED,
    ],
)
def test_nonconfirmed_result_does_not_require_provider_settlement(
    state: SignerSubmissionState,
) -> None:
    value = result(
        submission_state=state,
        provider_settlement=None,
        transaction_reference=None,
    )

    assert value.submission_state is state


@pytest.mark.parametrize(
    "updates",
    [
        {
            "submission_state": SignerSubmissionState.NOT_SUBMITTED,
            "provider_settlement": None,
            "transaction_reference": "syn_transaction",
        },
        {
            "submission_state": SignerSubmissionState.NOT_SUBMITTED,
            "provider_settlement": {"success": True},
            "transaction_reference": None,
        },
        {
            "submission_state": SignerSubmissionState.PROVEN_NOT_SUBMITTED,
            "provider_settlement": {"success": True},
            "transaction_reference": "syn_transaction",
        },
    ],
)
def test_non_submission_result_rejects_contradictory_evidence(
    updates: dict[str, object],
) -> None:
    with pytest.raises(ValidationError, match="non-submission"):
        result(**updates)
