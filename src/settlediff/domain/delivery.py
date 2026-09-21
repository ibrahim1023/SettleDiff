"""Deterministic assessment of a paid response against an advertised contract."""

from __future__ import annotations

import re
from typing import cast

from pydantic import JsonValue

from settlediff.domain.models import (
    DeliveryAssessment,
    DeliveryObservation,
    DeliveryStatus,
    ResponseContract,
)

RESPONSE_CONTRACT_ABSENT = "RESPONSE_CONTRACT_ABSENT"
RESPONSE_EVIDENCE_MISSING = "RESPONSE_EVIDENCE_MISSING"
RESPONSE_TRUNCATED = "RESPONSE_TRUNCATED"
HTTP_STATUS_NOT_SUCCESS = "HTTP_STATUS_NOT_SUCCESS"
MEDIA_TYPE_MISSING = "MEDIA_TYPE_MISSING"
MEDIA_TYPE_MISMATCH = "MEDIA_TYPE_MISMATCH"
BODY_MISSING = "BODY_MISSING"
SCHEMA_UNSUPPORTED = "SCHEMA_UNSUPPORTED"
SCHEMA_MALFORMED = "SCHEMA_MALFORMED"
SCHEMA_MISMATCH = "SCHEMA_MISMATCH"
DELIVERY_SATISFIED = "DELIVERY_SATISFIED"

_SCHEMA_KEYWORDS = frozenset({"type", "required", "properties", "items"})
_SCHEMA_TYPES = frozenset({"object", "array", "string", "number", "integer", "boolean", "null"})
_MEDIA_TYPE = re.compile(r"^[A-Za-z0-9!#$&^_.+-]+/[A-Za-z0-9!#$&^_.+-]+$")
_MAX_SCHEMA_DEPTH = 16
_MAX_SCHEMA_PROPERTIES = 128


def assess_delivery(
    contract: ResponseContract | None,
    observation: DeliveryObservation | None,
    *,
    contract_evidence_id: str,
) -> DeliveryAssessment:
    """Assess one observed response without I/O or provider trust."""
    if contract is None:
        return DeliveryAssessment(
            status=DeliveryStatus.NOT_ASSESSED,
            reason_code=RESPONSE_CONTRACT_ABSENT,
            evidence_ids=(contract_evidence_id,),
        )
    digest = contract.digest
    evidence_ids = tuple(
        dict.fromkeys((contract_evidence_id, *(observation.evidence_ids if observation else ())))
    )[:16]
    if observation is None:
        return _assessment(
            DeliveryStatus.UNKNOWN, RESPONSE_EVIDENCE_MISSING, evidence_ids, None, digest
        )

    def outcome(status: DeliveryStatus, reason_code: str) -> DeliveryAssessment:
        return _assessment(status, reason_code, evidence_ids, observation, digest)

    if observation.truncated:
        return outcome(DeliveryStatus.UNKNOWN, RESPONSE_TRUNCATED)
    if not 200 <= observation.status_code < 300:
        return outcome(DeliveryStatus.FAILED, HTTP_STATUS_NOT_SUCCESS)
    if contract.media_type is not None:
        observed_media = _normalize_media_type(observation.media_type)
        if observed_media is None:
            return outcome(DeliveryStatus.UNKNOWN, MEDIA_TYPE_MISSING)
        if observed_media != _normalize_media_type(contract.media_type):
            return outcome(DeliveryStatus.FAILED, MEDIA_TYPE_MISMATCH)
    if observation.received_bytes == 0:
        return outcome(DeliveryStatus.UNKNOWN, BODY_MISSING)
    if contract.json_schema is not None:
        schema_error = _schema_error(contract.json_schema)
        if schema_error is not None:
            return outcome(DeliveryStatus.UNKNOWN, schema_error)
        if observation.parsed_body is None:
            return outcome(DeliveryStatus.UNKNOWN, BODY_MISSING)
        if not _matches_schema(observation.parsed_body, contract.json_schema):
            return outcome(DeliveryStatus.FAILED, SCHEMA_MISMATCH)
    return outcome(DeliveryStatus.SATISFIED, DELIVERY_SATISFIED)


def _assessment(
    status: DeliveryStatus,
    reason_code: str,
    evidence_ids: tuple[str, ...],
    observation: DeliveryObservation | None,
    digest: str,
) -> DeliveryAssessment:
    return DeliveryAssessment(
        status=status,
        reason_code=reason_code,
        evidence_ids=evidence_ids,
        observation=observation,
        response_contract_digest=digest,
    )


def _normalize_media_type(value: str | None) -> str | None:
    if value is None:
        return None
    media_type = value.split(";", maxsplit=1)[0].strip().lower()
    if not media_type or len(media_type) > 255 or _MEDIA_TYPE.fullmatch(media_type) is None:
        return None
    return media_type


def _schema_error(schema: dict[str, JsonValue], *, depth: int = 0) -> str | None:
    if depth > _MAX_SCHEMA_DEPTH:
        return SCHEMA_MALFORMED
    if set(schema) - _SCHEMA_KEYWORDS:
        return SCHEMA_UNSUPPORTED
    schema_type = schema.get("type")
    if not isinstance(schema_type, str) or schema_type not in _SCHEMA_TYPES:
        return SCHEMA_MALFORMED
    required = schema.get("required")
    properties = schema.get("properties")
    items = schema.get("items")
    if required is not None or properties is not None:
        if schema_type != "object" or not isinstance(properties, dict):
            return SCHEMA_MALFORMED
        property_mapping = cast(dict[str, JsonValue], properties)
        if len(property_mapping) > _MAX_SCHEMA_PROPERTIES:
            return SCHEMA_MALFORMED
        if required is not None and (
            not isinstance(required, list)
            or any(not isinstance(name, str) or not name for name in required)
            or len(set(cast(list[str], required))) != len(required)
            or any(name not in property_mapping for name in cast(list[str], required))
        ):
            return SCHEMA_MALFORMED
        for name, child in property_mapping.items():
            if not name or not isinstance(child, dict):
                return SCHEMA_MALFORMED
            child_error = _schema_error(cast(dict[str, JsonValue], child), depth=depth + 1)
            if child_error is not None:
                return child_error
    if items is not None:
        if schema_type != "array" or not isinstance(items, dict):
            return SCHEMA_MALFORMED
        return _schema_error(cast(dict[str, JsonValue], items), depth=depth + 1)
    if schema_type == "array":
        return SCHEMA_MALFORMED
    return None


def _matches_schema(value: JsonValue, schema: dict[str, JsonValue]) -> bool:
    schema_type = schema["type"]
    if schema_type == "object":
        if not isinstance(value, dict):
            return False
        mapping = cast(dict[str, JsonValue], value)
        required = schema.get("required")
        if isinstance(required, list) and any(
            name not in mapping for name in cast(list[str], required)
        ):
            return False
        properties = schema.get("properties")
        if isinstance(properties, dict):
            for name, child in cast(dict[str, JsonValue], properties).items():
                if name in mapping and not _matches_schema(
                    mapping[name], cast(dict[str, JsonValue], child)
                ):
                    return False
        return True
    if schema_type == "array":
        if not isinstance(value, list):
            return False
        items = cast(dict[str, JsonValue], schema["items"])
        return all(_matches_schema(item, items) for item in cast(list[JsonValue], value))
    if schema_type == "string":
        return isinstance(value, str)
    if schema_type == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if schema_type == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if schema_type == "boolean":
        return isinstance(value, bool)
    return value is None
