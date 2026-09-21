"""Strict interpretation of captured embedded Bazaar response metadata."""

from __future__ import annotations

import re
from enum import StrEnum
from typing import cast

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from settlediff.domain.models import (
    ExpectedContract,
    MachineReport,
    NonEmptyStr,
    ResponseContract,
)
from settlediff.x402.models import PaymentRequired, ResourceInfo

_SCHEMA_KEYWORDS = frozenset({"type", "required", "properties", "items"})
_SCHEMA_TYPES = frozenset({"object", "array", "string", "number", "integer", "boolean", "null"})
_MEDIA_TYPE = re.compile(r"^[A-Za-z0-9!#$&^_.+-]+/[A-Za-z0-9!#$&^_.+-]+$")
_MAX_SCHEMA_PROPERTIES = 128
_HTTP_METHODS = frozenset({"GET", "HEAD", "DELETE", "POST", "PUT", "PATCH"})

_CHALLENGE_EVIDENCE = ("bazaar:challenge",)


class BazaarContractError(ValueError):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code


def response_contract_from(
    resource: ResourceInfo, extensions: dict[str, JsonValue]
) -> ResponseContract | None:
    media_type = _media_type(resource.mime_type)
    source_fields: list[str] = []
    if media_type is not None:
        source_fields.append("resource.mimeType")

    if "bazaar" not in extensions:
        if media_type is None:
            return None
        return ResponseContract(
            media_type=media_type, json_schema=None, source_fields=tuple(source_fields)
        )
    bazaar = extensions["bazaar"]
    if not isinstance(bazaar, dict):
        raise BazaarContractError("BAZAAR_MALFORMED", "extensions.bazaar must be an object")
    bazaar_mapping = cast(dict[str, JsonValue], bazaar)
    info = bazaar_mapping.get("info")
    schema = bazaar_mapping.get("schema")
    if not isinstance(info, dict) or not isinstance(schema, dict):
        raise BazaarContractError(
            "BAZAAR_MALFORMED", "Bazaar response metadata requires info and schema objects"
        )
    output = cast(dict[str, JsonValue], info).get("output")
    if not isinstance(output, dict) or cast(dict[str, JsonValue], output).get("type") != "json":
        raise BazaarContractError(
            "BAZAAR_MALFORMED", "Bazaar info.output.type must be the captured json shape"
        )
    canonical_schema = _json_schema(cast(dict[str, JsonValue], schema), path="schema")
    source_fields.extend(("extensions.bazaar.info.output.type", "extensions.bazaar.schema"))
    return ResponseContract(
        media_type=media_type,
        json_schema=canonical_schema,
        source_fields=tuple(source_fields),
    )


def _media_type(value: str | None) -> str | None:
    if value is None:
        return None
    media_type = value.split(";", maxsplit=1)[0].strip().lower()
    if _MEDIA_TYPE.fullmatch(media_type) is None:
        raise BazaarContractError(
            "RESPONSE_CONTRACT_MALFORMED", "resource.mimeType is not a valid media type"
        )
    return media_type


def _json_schema(schema: dict[str, JsonValue], *, path: str) -> dict[str, JsonValue]:
    unsupported = tuple(sorted(set(schema) - _SCHEMA_KEYWORDS))
    if unsupported:
        raise BazaarContractError(
            "SCHEMA_UNSUPPORTED", f"{path} contains unsupported keyword {unsupported[0]}"
        )
    schema_type = schema.get("type")
    if not isinstance(schema_type, str) or schema_type not in _SCHEMA_TYPES:
        raise BazaarContractError("BAZAAR_MALFORMED", f"{path}.type is unsupported or missing")

    result: dict[str, JsonValue] = {"type": schema_type}
    required = schema.get("required")
    properties = schema.get("properties")
    items = schema.get("items")

    if required is not None or properties is not None:
        if schema_type != "object" or not isinstance(properties, dict):
            raise BazaarContractError(
                "BAZAAR_MALFORMED", f"{path} object keywords require object type and properties"
            )
        property_mapping = cast(dict[str, JsonValue], properties)
        if len(property_mapping) > _MAX_SCHEMA_PROPERTIES:
            raise BazaarContractError("BAZAAR_MALFORMED", f"{path}.properties exceeds the limit")
        canonical_properties: dict[str, JsonValue] = {}
        for name, child in property_mapping.items():
            if not name or not isinstance(child, dict):
                raise BazaarContractError(
                    "BAZAAR_MALFORMED", f"{path}.properties must map names to schemas"
                )
            canonical_properties[name] = _json_schema(
                cast(dict[str, JsonValue], child), path=f"{path}.properties.{name}"
            )
        result["properties"] = canonical_properties
        if required is not None:
            if (
                not isinstance(required, list)
                or not required
                or any(not isinstance(name, str) or not name for name in required)
                or len(set(cast(list[str], required))) != len(required)
                or any(name not in property_mapping for name in cast(list[str], required))
            ):
                raise BazaarContractError(
                    "BAZAAR_MALFORMED", f"{path}.required must name unique declared properties"
                )
            result["required"] = cast(list[JsonValue], sorted(cast(list[str], required)))

    if items is not None:
        if schema_type != "array" or not isinstance(items, dict):
            raise BazaarContractError(
                "BAZAAR_MALFORMED", f"{path}.items requires array type and one schema"
            )
        result["items"] = _json_schema(cast(dict[str, JsonValue], items), path=f"{path}.items")
    elif schema_type == "array":
        raise BazaarContractError("BAZAAR_MALFORMED", f"{path} array schema requires items")

    return result


class BazaarStatus(StrEnum):
    MATCH = "MATCH"
    DIFF = "DIFF"
    UNAVAILABLE = "UNAVAILABLE"
    UNSUPPORTED = "UNSUPPORTED"


class BazaarFieldCheck(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    schema_version: int = Field(default=1, ge=1, le=1)
    check_id: NonEmptyStr
    status: BazaarStatus
    evidence_ids: tuple[NonEmptyStr, ...] = Field(max_length=8)


def _bazaar_status(checks: tuple[BazaarFieldCheck, ...]) -> BazaarStatus:
    statuses = {check.status for check in checks}
    if BazaarStatus.UNSUPPORTED in statuses:
        return BazaarStatus.UNSUPPORTED
    if BazaarStatus.DIFF in statuses:
        return BazaarStatus.DIFF
    if BazaarStatus.MATCH in statuses:
        return BazaarStatus.MATCH
    return BazaarStatus.UNAVAILABLE


class BazaarAssessment(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    schema_version: int = Field(default=1, ge=1, le=1)
    status: BazaarStatus
    checks: tuple[BazaarFieldCheck, ...] = Field(min_length=1, max_length=20)
    run_id: NonEmptyStr | None = None

    @model_validator(mode="after")
    def require_consistent_assessment(self) -> BazaarAssessment:
        check_ids = [check.check_id for check in self.checks]
        if len(set(check_ids)) != len(check_ids):
            raise ValueError("Bazaar assessment cannot contain duplicate check ids")
        expected = _bazaar_status(self.checks)
        if self.status is not expected:
            raise ValueError(
                f"Bazaar assessment status {self.status} does not match checks {expected}"
            )
        return self


def _check(check_id: str, status: BazaarStatus, evidence: tuple[str, ...]) -> BazaarFieldCheck:
    return BazaarFieldCheck(check_id=check_id, status=status, evidence_ids=evidence)


def _input_method_status(info: dict[str, JsonValue], request_method: str) -> BazaarStatus:
    input_value = info.get("input")
    if not isinstance(input_value, dict):
        return BazaarStatus.UNSUPPORTED
    input_mapping = cast(dict[str, JsonValue], input_value)
    if input_mapping.get("type") != "http":
        return BazaarStatus.UNSUPPORTED
    method = input_mapping.get("method")
    if not isinstance(method, str) or method not in _HTTP_METHODS:
        return BazaarStatus.UNSUPPORTED
    return BazaarStatus.MATCH if method == request_method.upper() else BazaarStatus.DIFF


def _normalized_media(value: str | None) -> str | None:
    if value is None:
        return None
    return value.split(";", maxsplit=1)[0].strip().lower()


def _paid_field_status(current: object, paid: object) -> BazaarStatus:
    if current is None or paid is None:
        return BazaarStatus.UNAVAILABLE
    return BazaarStatus.MATCH if current == paid else BazaarStatus.DIFF


def _contains_redaction(value: object) -> bool:
    if isinstance(value, str):
        return "[REDACTED]" in value or "…" in value or "***@" in value
    if isinstance(value, BaseModel):
        return _contains_redaction(value.model_dump(mode="json"))
    if isinstance(value, dict):
        mapping = cast(dict[str, object], value)
        return any(_contains_redaction(child) for child in mapping.values())
    if isinstance(value, (list, tuple)):
        return any(_contains_redaction(child) for child in cast(tuple[object, ...], value))
    return False


def _sensitive_status(current: object, paid: object) -> BazaarStatus:
    if current is None or paid is None:
        return BazaarStatus.UNAVAILABLE
    if _contains_redaction(current) or _contains_redaction(paid):
        return BazaarStatus.UNAVAILABLE
    return BazaarStatus.MATCH if current == paid else BazaarStatus.DIFF


def _group_status(
    current: tuple[object, ...], paid: tuple[object, ...], *, sensitive: bool = False
) -> BazaarStatus:
    if sensitive and (_contains_redaction(current) or _contains_redaction(paid)):
        return BazaarStatus.UNAVAILABLE
    if all(value is None for value in current) or all(value is None for value in paid):
        return BazaarStatus.UNAVAILABLE
    return BazaarStatus.MATCH if current == paid else BazaarStatus.DIFF


def assess_bazaar(
    required: PaymentRequired,
    *,
    request_method: str,
    current_contract: ExpectedContract | None,
    paid_report: MachineReport | None = None,
) -> BazaarAssessment:
    """Compare a live challenge and optional persisted paid report without verdicts."""
    evidence = _CHALLENGE_EVIDENCE
    checks: list[BazaarFieldCheck] = []

    bazaar = required.extensions.get("bazaar")
    if bazaar is None:
        checks.append(_check("BAZAAR_EXTENSION", BazaarStatus.UNAVAILABLE, evidence))
        return BazaarAssessment(
            status=_bazaar_status(tuple(checks)),
            checks=tuple(checks),
            run_id=paid_report.run_id if paid_report is not None else None,
        )

    extension_status = BazaarStatus.MATCH
    schema_status = BazaarStatus.UNAVAILABLE
    input_status = BazaarStatus.UNAVAILABLE
    media_status = BazaarStatus.UNAVAILABLE
    if not isinstance(bazaar, dict):
        extension_status = BazaarStatus.UNSUPPORTED
    else:
        bazaar_mapping = cast(dict[str, JsonValue], bazaar)
        info = bazaar_mapping.get("info")
        schema = bazaar_mapping.get("schema")
        if not isinstance(info, dict) or not isinstance(schema, dict):
            extension_status = BazaarStatus.UNSUPPORTED
        else:
            info_mapping = cast(dict[str, JsonValue], info)
            output = info_mapping.get("output")
            if (
                not isinstance(output, dict)
                or cast(dict[str, JsonValue], output).get("type") != "json"
            ):
                extension_status = BazaarStatus.UNSUPPORTED
            else:
                input_status = _input_method_status(info_mapping, request_method)
                try:
                    media_type = _media_type(required.resource.mime_type)
                except BazaarContractError:
                    media_status = BazaarStatus.UNSUPPORTED
                else:
                    media_status = (
                        BazaarStatus.UNAVAILABLE
                        if media_type is None
                        else (
                            BazaarStatus.MATCH
                            if media_type == "application/json"
                            else BazaarStatus.DIFF
                        )
                    )
                try:
                    _json_schema(cast(dict[str, JsonValue], schema), path="schema")
                except BazaarContractError as error:
                    if error.code == "SCHEMA_UNSUPPORTED":
                        schema_status = BazaarStatus.UNSUPPORTED
                    else:
                        extension_status = BazaarStatus.UNSUPPORTED
                else:
                    schema_status = BazaarStatus.MATCH
    checks.append(_check("BAZAAR_EXTENSION", extension_status, evidence))

    try:
        required.selected_requirement(0)
    except ValueError:
        primary_status = BazaarStatus.UNSUPPORTED
    else:
        primary_status = BazaarStatus.MATCH
    checks.append(_check("PRIMARY_REQUIREMENT", primary_status, evidence))
    checks.append(_check("INPUT_METHOD", input_status, evidence))
    checks.append(_check("RESPONSE_SCHEMA", schema_status, evidence))
    checks.append(_check("MEDIA_TYPE", media_status, evidence))

    if paid_report is None:
        checks.append(_check("PAID_EVIDENCE", BazaarStatus.UNAVAILABLE, evidence))
    else:
        checks.extend(_paid_checks(current_contract, paid_report))
    return BazaarAssessment(
        status=_bazaar_status(tuple(checks)),
        checks=tuple(checks),
        run_id=paid_report.run_id if paid_report is not None else None,
    )


def _paid_checks(
    current: ExpectedContract | None,
    report: MachineReport,
) -> list[BazaarFieldCheck]:
    run_id = report.run_id
    evidence = ("bazaar:challenge", f"{run_id}:service_contract")
    paid_ids = (
        "RESOURCE",
        "PRICE",
        "NETWORK",
        "ASSET",
        "RECIPIENT",
        "SCHEME",
        "PROTOCOL_VERSION",
        "INPUT_CONTRACT",
        "PAID_RESPONSE_SCHEMA",
        "PAID_MEDIA_TYPE",
        "PAID_DELIVERY_CONTRACT",
    )
    if report.adapter_id != "x402":
        return [_check(check_id, BazaarStatus.UNSUPPORTED, evidence) for check_id in paid_ids]
    paid = report.contract
    if paid is None or current is None:
        return [_check(check_id, BazaarStatus.UNAVAILABLE, evidence) for check_id in paid_ids]

    resource_status = _paid_field_status(current.url, paid.url)
    if resource_status is BazaarStatus.DIFF:
        return [
            _check("RESOURCE", BazaarStatus.DIFF, evidence),
            *(_check(check_id, BazaarStatus.UNAVAILABLE, evidence) for check_id in paid_ids[1:]),
        ]

    delivery = report.delivery
    observation = delivery.observation if delivery is not None else None
    paid_response = paid.response_contract
    current_response = current.response_contract

    def paid_media() -> BazaarStatus:
        paid_media_type = _normalized_media(
            paid_response.media_type if paid_response is not None else None
        )
        current_media_type = _normalized_media(
            current_response.media_type if current_response is not None else None
        )
        status = _paid_field_status(current_media_type, paid_media_type)
        if status is not BazaarStatus.MATCH or observation is None:
            return status
        if observation.media_type is None:
            return BazaarStatus.UNAVAILABLE
        observed_media = _normalized_media(observation.media_type)
        if observed_media is None or _MEDIA_TYPE.fullmatch(observed_media) is None:
            return BazaarStatus.UNSUPPORTED
        return BazaarStatus.MATCH if observed_media == current_media_type else BazaarStatus.DIFF

    def delivery_contract() -> BazaarStatus:
        status = _paid_field_status(
            current_response.digest if current_response is not None else None,
            delivery.response_contract_digest if delivery is not None else None,
        )
        return status

    paid_values = {
        "RESOURCE": (current.url, paid.url),
        "PRICE": (current.price, paid.price),
        "SCHEME": (current.scheme, paid.scheme),
        "PROTOCOL_VERSION": (current.protocol, paid.protocol),
        "INPUT_CONTRACT": (current.request_schema, paid.request_schema),
        "PAID_RESPONSE_SCHEMA": (
            current_response.json_schema if current_response is not None else None,
            paid_response.json_schema if paid_response is not None else None,
        ),
    }
    checks: list[BazaarFieldCheck] = []
    for check_id in paid_ids:
        check_evidence = evidence
        if check_id == "PAID_DELIVERY_CONTRACT" and observation is not None:
            check_evidence = (*evidence, f"{run_id}:service_response")
        if check_id == "NETWORK":
            status = _group_status((current.network, current.chain), (paid.network, paid.chain))
        elif check_id == "ASSET":
            status = _group_status(
                (current.asset, current.asset_identity),
                (paid.asset, paid.asset_identity),
                sensitive=True,
            )
        elif check_id == "RECIPIENT":
            status = _sensitive_status(current.recipient, paid.recipient)
        elif check_id == "PAID_MEDIA_TYPE":
            status = paid_media()
        elif check_id == "PAID_DELIVERY_CONTRACT":
            status = delivery_contract()
        else:
            current_value, paid_value = paid_values[check_id]
            status = _paid_field_status(current_value, paid_value)
        checks.append(_check(check_id, status, check_evidence))
    return checks
