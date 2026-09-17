from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from settlediff.domain.integrity import Sha256Digest, canonical_json_bytes, sha256_digest
from settlediff.domain.models import (
    DeliveryAssessment,
    DeliveryObservation,
    DeliveryStatus,
    MachineReport,
    PurchaseIntent,
    ResponseContract,
    RetryAssessment,
    RetrySafety,
    Verdict,
)
from settlediff.domain.money import Money

FIXTURES = Path(__file__).with_name("fixtures")
NOW = datetime(2026, 9, 17, tzinfo=UTC)


def report_payload() -> dict[str, object]:
    report = MachineReport(
        run_id="syn_assurance_001",
        intent=PurchaseIntent(
            run_id="syn_assurance_001",
            task="Inspect a synthetic purchase",
            max_budget=Money(amount=Decimal("0.01"), unit="USDC"),
            requested_service=None,
            created_at=NOW,
        ),
        contract=None,
        execution=None,
        ledger=None,
        findings=(),
        verdict=Verdict.UNVERIFIABLE,
    )
    return report.model_dump(mode="json")


def observation(**updates: object) -> DeliveryObservation:
    values: dict[str, object] = {
        "observed_at": NOW,
        "status_code": 200,
        "media_type": "application/json",
        "received_bytes": 22,
        "truncated": False,
        "parsed_body": {"result": "synthetic"},
        "evidence_ids": ("artifact:service-response",),
    }
    return DeliveryObservation.model_validate(values | updates)


def test_response_contract_fixture_is_strict_and_has_a_stable_digest() -> None:
    fixture = (FIXTURES / "response-contract.json").read_text()
    payload = json.loads(fixture)

    contract = ResponseContract.model_validate_json(fixture)

    assert contract.media_type == "application/json"
    assert contract.json_schema == {
        "type": "object",
        "required": ["result"],
        "properties": {"result": {"type": "string"}},
    }
    assert contract.digest == sha256_digest(contract.model_dump(mode="json"))
    assert canonical_json_bytes({"b": 2, "a": 1}) == b'{"a":1,"b":2}'
    with pytest.raises(ValidationError):
        ResponseContract.model_validate(payload | {"invented": True})


def test_sha256_digest_rejects_malformed_values() -> None:
    for value in ("a" * 63, "A" * 64, "g" * 64, 123):
        with pytest.raises(ValidationError):
            DeliveryAssessment.model_validate(
                {
                    "status": "UNKNOWN",
                    "reason_code": "EVIDENCE_UNAVAILABLE",
                    "evidence_ids": ("artifact:service-response",),
                    "response_contract_digest": value,
                }
            )
    assert Sha256Digest is not str


def test_assurance_boundaries_reject_float_money() -> None:
    with pytest.raises(ValidationError):
        Money.model_validate({"amount": 0.01, "unit": "USDC"})


def test_delivery_observation_requires_utc_decimal_free_bounded_evidence() -> None:
    with pytest.raises(ValidationError, match="UTC"):
        observation(observed_at=datetime(2026, 9, 17))
    with pytest.raises(ValidationError):
        observation(received_bytes=1.5)
    with pytest.raises(ValidationError):
        observation(evidence_ids=("",))
    with pytest.raises(ValidationError, match="truncated"):
        observation(truncated=True)


def test_delivery_assessment_rejects_incoherent_states() -> None:
    with pytest.raises(ValidationError, match="observation"):
        DeliveryAssessment(
            status=DeliveryStatus.SATISFIED,
            reason_code="CONTRACT_SATISFIED",
            evidence_ids=("artifact:service-response",),
            observation=None,
            response_contract_digest="a" * 64,
        )
    with pytest.raises(ValidationError, match="observation"):
        DeliveryAssessment(
            status=DeliveryStatus.NOT_ASSESSED,
            reason_code="NO_RESPONSE_CONTRACT",
            evidence_ids=("artifact:contract",),
            observation=observation(),
            response_contract_digest=None,
        )


def test_retry_assessment_requires_reason_and_evidence_codes() -> None:
    for updates in ({"reason_codes": ()}, {"evidence_ids": ()}, {"evidence_ids": ("",)}):
        with pytest.raises(ValidationError):
            RetryAssessment.model_validate(
                {
                    "safety": RetrySafety.REQUIRES_HUMAN_DECISION,
                    "reason_codes": ("SUBMISSION_UNCERTAIN",),
                    "evidence_ids": ("artifact:recovery",),
                }
                | updates
            )


def test_schema_v3_report_accepts_delivery_and_retry() -> None:
    payload = report_payload() | {
        "schema_version": 3,
        "delivery": DeliveryAssessment(
            status=DeliveryStatus.SATISFIED,
            reason_code="CONTRACT_SATISFIED",
            evidence_ids=("artifact:service-response",),
            observation=observation(),
            response_contract_digest="a" * 64,
        ).model_dump(mode="json"),
        "retry": RetryAssessment(
            safety=RetrySafety.DO_NOT_RETRY,
            reason_codes=("PAYMENT_CONFIRMED",),
            evidence_ids=("artifact:receipt",),
        ).model_dump(mode="json"),
    }

    report = MachineReport.model_validate_json(json.dumps(payload))

    assert report.schema_version == 3
    assert report.delivery is not None
    assert report.retry is not None


@pytest.mark.parametrize("schema_version", [1, 2])
@pytest.mark.parametrize("field", ["delivery", "retry"])
def test_old_reports_reject_future_fields_even_when_null(schema_version: int, field: str) -> None:
    payload = report_payload() | {"schema_version": schema_version, field: None}

    with pytest.raises(ValidationError, match=f"schema version {schema_version}"):
        MachineReport.model_validate_json(json.dumps(payload))


def test_schema_v2_report_serialization_remains_backward_readable() -> None:
    payload = report_payload()

    assert "delivery" not in payload
    assert "retry" not in payload
    assert MachineReport.model_validate_json(json.dumps(payload)).schema_version == 2
