from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest
from pydantic import JsonValue

from settlediff.domain.delivery import assess_delivery
from settlediff.domain.models import (
    DeliveryObservation,
    DeliveryStatus,
    ResponseContract,
)

FIXTURE = Path(__file__).with_name("fixtures") / "delivery-cases.json"
CONTRACT_EVIDENCE_ID = "syn_run:contract"
NOW = datetime(2026, 9, 1, tzinfo=UTC)


def _fixture() -> dict[str, JsonValue]:
    return cast(dict[str, JsonValue], json.loads(FIXTURE.read_text()))


def _cases() -> list[dict[str, JsonValue]]:
    return cast(list[dict[str, JsonValue]], _fixture()["cases"])


def _contract(payload: dict[str, JsonValue] | None = None) -> ResponseContract:
    if payload is None:
        payload = cast(dict[str, JsonValue], _fixture()["contract"])
    return ResponseContract.model_validate_json(json.dumps(payload), strict=True)


def _observation(payload: JsonValue) -> DeliveryObservation | None:
    if payload is None:
        return None
    assert isinstance(payload, dict)
    return DeliveryObservation.model_validate_json(json.dumps(payload), strict=True)


@pytest.mark.parametrize("case", _cases(), ids=lambda case: case["name"])
def test_delivery_cases(case: dict[str, JsonValue]) -> None:
    assessment = assess_delivery(
        _contract(),
        _observation(case["observation"]),
        contract_evidence_id=CONTRACT_EVIDENCE_ID,
    )

    assert assessment.status is DeliveryStatus(case["expected_status"])
    assert assessment.reason_code == case["expected_reason"]
    assert assessment.response_contract_digest == _contract().digest
    expected_evidence = (
        (CONTRACT_EVIDENCE_ID, "syn_run:execution")
        if case["observation"] is not None
        else (CONTRACT_EVIDENCE_ID,)
    )
    assert assessment.evidence_ids == expected_evidence
    if case["observation"] is None:
        assert assessment.observation is None
    else:
        assert assessment.observation == _observation(case["observation"])


def test_absent_contract_is_not_assessed_without_observation_or_digest() -> None:
    assessment = assess_delivery(
        None,
        _observation(_cases()[0]["observation"]),
        contract_evidence_id=CONTRACT_EVIDENCE_ID,
    )

    assert assessment.status is DeliveryStatus.NOT_ASSESSED
    assert assessment.reason_code == "RESPONSE_CONTRACT_ABSENT"
    assert assessment.evidence_ids == (CONTRACT_EVIDENCE_ID,)
    assert assessment.observation is None
    assert assessment.response_contract_digest is None


def _obs(
    *,
    status_code: int = 200,
    media_type: str | None = "application/json",
    received_bytes: int = 2,
    truncated: bool = False,
    parsed_body: JsonValue = None,
) -> DeliveryObservation:
    return DeliveryObservation(
        observed_at=NOW,
        status_code=status_code,
        media_type=media_type,
        received_bytes=received_bytes,
        truncated=truncated,
        parsed_body=parsed_body,
        evidence_ids=("syn_run:execution",),
    )


def _schema_contract(schema: dict[str, JsonValue]) -> ResponseContract:
    return ResponseContract(
        media_type=None,
        json_schema=schema,
        source_fields=("extensions.bazaar.schema",),
    )


def test_nested_unsupported_keyword_is_unknown() -> None:
    contract = _schema_contract(
        {
            "type": "object",
            "properties": {"result": {"type": "string", "minLength": 1}},
        }
    )
    assessment = assess_delivery(
        contract,
        _obs(parsed_body={"result": "synthetic"}),
        contract_evidence_id=CONTRACT_EVIDENCE_ID,
    )

    assert assessment.status is DeliveryStatus.UNKNOWN
    assert assessment.reason_code == "SCHEMA_UNSUPPORTED"


@pytest.mark.parametrize(
    "schema",
    [
        {"properties": {"result": {"type": "string"}}},
        {"type": "object", "required": ["missing"], "properties": {"result": {"type": "string"}}},
        {"type": "object", "required": "result", "properties": {"result": {"type": "string"}}},
        {"type": "array", "items": {"type": "string", "other": True}},
        {"type": "array"},
        {"type": "object", "properties": {str(index): {"type": "null"} for index in range(129)}},
    ],
)
def test_malformed_schema_is_unknown(schema: dict[str, JsonValue]) -> None:
    assessment = assess_delivery(
        _schema_contract(schema),
        _obs(parsed_body={"result": "synthetic"}),
        contract_evidence_id=CONTRACT_EVIDENCE_ID,
    )

    assert assessment.status is DeliveryStatus.UNKNOWN
    assert assessment.reason_code in {"SCHEMA_MALFORMED", "SCHEMA_UNSUPPORTED"}


def test_array_and_primitive_schemas_validate_recursively() -> None:
    array_contract = _schema_contract({"type": "array", "items": {"type": "integer"}})
    satisfied = assess_delivery(
        array_contract, _obs(parsed_body=[1, 2, 3]), contract_evidence_id=CONTRACT_EVIDENCE_ID
    )
    mismatch = assess_delivery(
        array_contract, _obs(parsed_body=[1, "x"]), contract_evidence_id=CONTRACT_EVIDENCE_ID
    )
    primitive = assess_delivery(
        _schema_contract({"type": "string"}),
        _obs(parsed_body="synthetic"),
        contract_evidence_id=CONTRACT_EVIDENCE_ID,
    )
    boolean_is_not_integer = assess_delivery(
        array_contract, _obs(parsed_body=[True]), contract_evidence_id=CONTRACT_EVIDENCE_ID
    )

    assert satisfied.status is DeliveryStatus.SATISFIED
    assert mismatch.status is DeliveryStatus.FAILED
    assert mismatch.reason_code == "SCHEMA_MISMATCH"
    assert primitive.status is DeliveryStatus.SATISFIED
    assert boolean_is_not_integer.status is DeliveryStatus.FAILED


def test_additional_properties_and_normalized_media_are_accepted() -> None:
    assessment = assess_delivery(
        _contract(),
        _obs(
            media_type="Application/JSON; charset=utf-8",
            parsed_body={"result": "synthetic", "extra": 1},
        ),
        contract_evidence_id=CONTRACT_EVIDENCE_ID,
    )

    assert assessment.status is DeliveryStatus.SATISFIED


@pytest.mark.parametrize("media_type", [None, "not-a-media-type"])
def test_missing_or_malformed_observation_media_is_unknown(media_type: str | None) -> None:
    assessment = assess_delivery(
        _contract(),
        _obs(media_type=media_type, parsed_body={"result": "synthetic"}),
        contract_evidence_id=CONTRACT_EVIDENCE_ID,
    )

    assert assessment.status is DeliveryStatus.UNKNOWN
    assert assessment.reason_code == "MEDIA_TYPE_MISSING"


def test_schema_free_contract_is_satisfied_without_parsed_body() -> None:
    contract = ResponseContract(
        media_type="application/json",
        json_schema=None,
        source_fields=("resource.mimeType",),
    )
    assessment = assess_delivery(
        contract,
        _obs(parsed_body=None, received_bytes=9),
        contract_evidence_id=CONTRACT_EVIDENCE_ID,
    )

    assert assessment.status is DeliveryStatus.SATISFIED
    assert assessment.reason_code == "DELIVERY_SATISFIED"
