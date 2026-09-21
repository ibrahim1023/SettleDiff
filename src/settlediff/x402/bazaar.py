"""Strict interpretation of captured embedded Bazaar declaration metadata."""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Literal, cast
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from settlediff.domain.integrity import canonical_json_bytes
from settlediff.domain.models import (
    ExpectedContract,
    MachineReport,
    NonEmptyStr,
    ResponseContract,
)
from settlediff.x402.models import PaymentRequired, ResourceInfo

_MEDIA_TYPE = re.compile(r"^[A-Za-z0-9!#$&^_.+-]+/[A-Za-z0-9!#$&^_.+-]+$")
_HTTP_METHODS = frozenset({"GET", "HEAD", "DELETE", "POST", "PUT", "PATCH"})

_CHALLENGE_EVIDENCE = ("bazaar:challenge",)


class BazaarContractError(ValueError):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code


def response_contract_from(resource: ResourceInfo) -> ResponseContract | None:
    media_type = _media_type(resource.mime_type)
    if media_type is None:
        return None
    return ResponseContract(
        media_type=media_type,
        json_schema=None,
        source_fields=("resource.mimeType",),
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


_DECLARATION_KEYWORDS = frozenset(
    {
        "$schema",
        "type",
        "properties",
        "required",
        "additionalProperties",
        "items",
        "const",
        "enum",
        "format",
    }
)
_DECLARATION_TYPES = frozenset(
    {"object", "array", "string", "number", "integer", "boolean", "null"}
)
_DECLARATION_SCHEMA_ID = "https://json-schema.org/draft/2020-12/schema"
_MAX_DECLARATION_DEPTH = 16
_MAX_DECLARATION_PROPERTIES = 128
_MAX_DECLARATION_ENUM = 32
_MAX_DECLARATION_STRING_BYTES = 4096


class _DeclarationMalformed(ValueError):
    pass


class _DeclarationUnsupported(ValueError):
    pass


class BazaarStatus(StrEnum):
    MATCH = "MATCH"
    DIFF = "DIFF"
    UNAVAILABLE = "UNAVAILABLE"
    UNSUPPORTED = "UNSUPPORTED"


class BazaarDeclarationResult(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    status: BazaarStatus
    diagnostic: Literal[
        "BAZAAR_DECLARATION_MATCH",
        "BAZAAR_DECLARATION_MALFORMED",
        "BAZAAR_DECLARATION_SCHEMA_UNSUPPORTED",
        "BAZAAR_DECLARATION_DIFF",
    ]

    @model_validator(mode="after")
    def _coherent_status_diagnostic(self) -> BazaarDeclarationResult:
        coherent = {
            (BazaarStatus.MATCH, "BAZAAR_DECLARATION_MATCH"),
            (BazaarStatus.DIFF, "BAZAAR_DECLARATION_DIFF"),
            (BazaarStatus.UNSUPPORTED, "BAZAAR_DECLARATION_MALFORMED"),
            (BazaarStatus.UNSUPPORTED, "BAZAAR_DECLARATION_SCHEMA_UNSUPPORTED"),
        }
        if (self.status, self.diagnostic) not in coherent:
            raise ValueError("declaration status/diagnostic mismatch")
        return self


def _canonical(value: JsonValue) -> bytes:
    return canonical_json_bytes(value)


def _check_declaration_schema(schema: object, *, depth: int, properties_seen: list[int]) -> None:
    if not isinstance(schema, dict) or depth > _MAX_DECLARATION_DEPTH:
        raise _DeclarationMalformed("declaration schema must be a bounded object")
    mapping = cast(dict[str, JsonValue], schema)
    unsupported = set(mapping) - _DECLARATION_KEYWORDS
    if unsupported:
        raise _DeclarationUnsupported(
            f"declaration schema uses unsupported keyword {sorted(unsupported)[0]}"
        )
    dialect = mapping.get("$schema")
    if dialect is not None and dialect != _DECLARATION_SCHEMA_ID:
        raise _DeclarationUnsupported("declaration $schema is not the captured draft")
    if "format" in mapping and mapping["format"] != "uri":
        raise _DeclarationUnsupported("declaration format is unsupported")
    schema_type = mapping.get("type")
    if schema_type is not None and (
        not isinstance(schema_type, str) or schema_type not in _DECLARATION_TYPES
    ):
        raise _DeclarationMalformed("declaration type is unsupported or missing")
    enum = mapping.get("enum")
    if enum is not None:
        if not isinstance(enum, list) or not 1 <= len(enum) <= _MAX_DECLARATION_ENUM:
            raise _DeclarationMalformed("declaration enum must contain 1..32 values")
        if len({_canonical(item) for item in cast(list[JsonValue], enum)}) != len(enum):
            raise _DeclarationMalformed("declaration enum values must be unique")
    properties = mapping.get("properties")
    if properties is not None:
        if not isinstance(properties, dict):
            raise _DeclarationMalformed("declaration properties must be an object")
        property_mapping = cast(dict[str, JsonValue], properties)
        properties_seen[0] += len(property_mapping)
        if properties_seen[0] > _MAX_DECLARATION_PROPERTIES:
            raise _DeclarationMalformed("declaration properties exceed the limit")
        for child in property_mapping.values():
            _check_declaration_schema(child, depth=depth + 1, properties_seen=properties_seen)
    required = mapping.get("required")
    if required is not None and (
        not isinstance(required, list)
        or any(not isinstance(name, str) or not name for name in required)
        or len(set(cast(list[object], required))) != len(required)
        or (
            isinstance(properties, dict)
            and any(name not in properties for name in cast(list[str], required))
        )
    ):
        raise _DeclarationMalformed("declaration required must name declared properties")
    additional = mapping.get("additionalProperties")
    if additional is not None and not isinstance(additional, (bool, dict)):
        raise _DeclarationMalformed("declaration additionalProperties must be bool or schema")
    if isinstance(additional, dict):
        _check_declaration_schema(additional, depth=depth + 1, properties_seen=properties_seen)
    items = mapping.get("items")
    if items is not None:
        if not isinstance(items, dict):
            raise _DeclarationMalformed("declaration items must be one schema")
        _check_declaration_schema(items, depth=depth + 1, properties_seen=properties_seen)


def _type_matches(value: JsonValue, schema_type: str) -> bool:
    if schema_type == "object":
        return isinstance(value, dict)
    if schema_type == "array":
        return isinstance(value, list)
    if schema_type == "string":
        return isinstance(value, str)
    if schema_type == "boolean":
        return isinstance(value, bool)
    if schema_type == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if schema_type == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if schema_type == "null":
        return value is None
    return False


def _is_declaration_uri(value: str) -> bool:
    parsed = urlsplit(value)
    return (
        parsed.scheme in {"http", "https"}
        and bool(parsed.netloc)
        and parsed.username is None
        and parsed.password is None
    )


def _declaration_matches(value: JsonValue, schema: dict[str, JsonValue], depth: int) -> bool:
    if depth > _MAX_DECLARATION_DEPTH:
        return False
    schema_type = schema.get("type")
    if isinstance(schema_type, str) and not _type_matches(value, schema_type):
        return False
    if "const" in schema and _canonical(value) != _canonical(schema["const"]):
        return False
    enum = schema.get("enum")
    if isinstance(enum, list) and _canonical(value) not in {
        _canonical(item) for item in cast(list[JsonValue], enum)
    }:
        return False
    if schema.get("format") == "uri" and not (
        isinstance(value, str) and _is_declaration_uri(value)
    ):
        return False
    if isinstance(value, dict):
        mapping = cast(dict[str, JsonValue], value)
        required = schema.get("required")
        if isinstance(required, list) and any(
            name not in mapping for name in cast(list[object], required)
        ):
            return False
        properties = schema.get("properties")
        property_mapping = (
            cast(dict[str, JsonValue], properties) if isinstance(properties, dict) else {}
        )
        for name, child in mapping.items():
            child_schema = property_mapping.get(name)
            if child_schema is not None:
                if not _declaration_matches(
                    child, cast(dict[str, JsonValue], child_schema), depth + 1
                ):
                    return False
                continue
            additional = schema.get("additionalProperties")
            if additional is False:
                return False
            if isinstance(additional, dict) and not _declaration_matches(
                child, cast(dict[str, JsonValue], additional), depth + 1
            ):
                return False
    if isinstance(value, list):
        items = schema.get("items")
        if isinstance(items, dict):
            item_schema = cast(dict[str, JsonValue], items)
            if any(
                not _declaration_matches(item, item_schema, depth + 1)
                for item in cast(list[JsonValue], value)
            ):
                return False
    return True


def _declaration_string_bounded(value: str) -> bool:
    return len(value.encode("utf-8")) <= _MAX_DECLARATION_STRING_BYTES


def _check_declaration_value(value: JsonValue, *, depth: int, properties_seen: list[int]) -> None:
    if depth > _MAX_DECLARATION_DEPTH:
        raise _DeclarationMalformed("declaration value exceeds depth limit")
    if isinstance(value, str) and not _declaration_string_bounded(value):
        raise _DeclarationMalformed("declaration string exceeds the byte limit")
    if isinstance(value, dict):
        mapping = cast(dict[str, JsonValue], value)
        properties_seen[0] += len(mapping)
        if properties_seen[0] > _MAX_DECLARATION_PROPERTIES:
            raise _DeclarationMalformed("declaration value exceeds property limit")
        for key, child in mapping.items():
            if not _declaration_string_bounded(key):
                raise _DeclarationMalformed("declaration key exceeds the byte limit")
            _check_declaration_value(child, depth=depth + 1, properties_seen=properties_seen)
    elif isinstance(value, list):
        for item in cast(list[JsonValue], value):
            _check_declaration_value(item, depth=depth + 1, properties_seen=properties_seen)


def assess_bazaar_declaration(info: JsonValue, schema: JsonValue) -> BazaarDeclarationResult:
    """Validate the captured declaration schema and check ``info`` against it."""
    try:
        _check_declaration_value(info, depth=1, properties_seen=[0])
        _check_declaration_value(schema, depth=1, properties_seen=[0])
        _check_declaration_schema(schema, depth=1, properties_seen=[0])
    except _DeclarationUnsupported:
        return BazaarDeclarationResult(
            status=BazaarStatus.UNSUPPORTED,
            diagnostic="BAZAAR_DECLARATION_SCHEMA_UNSUPPORTED",
        )
    except _DeclarationMalformed:
        return BazaarDeclarationResult(
            status=BazaarStatus.UNSUPPORTED, diagnostic="BAZAAR_DECLARATION_MALFORMED"
        )
    if not isinstance(schema, dict) or not _declaration_matches(
        info, cast(dict[str, JsonValue], schema), depth=1
    ):
        return BazaarDeclarationResult(
            status=BazaarStatus.DIFF, diagnostic="BAZAAR_DECLARATION_DIFF"
        )
    return BazaarDeclarationResult(status=BazaarStatus.MATCH, diagnostic="BAZAAR_DECLARATION_MATCH")


def bazaar_declaration_diagnostic(extensions: dict[str, JsonValue]) -> str | None:
    """Return the stable declaration diagnostic, or None when absent or matching."""
    if "bazaar" not in extensions:
        return None
    bazaar = extensions["bazaar"]
    if not isinstance(bazaar, dict):
        return "BAZAAR_DECLARATION_MALFORMED"
    bazaar_mapping = cast(dict[str, JsonValue], bazaar)
    info = bazaar_mapping.get("info")
    schema = bazaar_mapping.get("schema")
    if not isinstance(info, dict) or not isinstance(schema, dict):
        return "BAZAAR_DECLARATION_MALFORMED"
    result = assess_bazaar_declaration(info, schema)
    if result.status is BazaarStatus.MATCH:
        return None
    return result.diagnostic


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

    if "bazaar" not in required.extensions:
        checks.append(_check("BAZAAR_EXTENSION", BazaarStatus.UNAVAILABLE, evidence))
        return BazaarAssessment(
            status=_bazaar_status(tuple(checks)),
            checks=tuple(checks),
            run_id=paid_report.run_id if paid_report is not None else None,
        )

    bazaar = required.extensions["bazaar"]
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
                declaration = assess_bazaar_declaration(info, cast(JsonValue, schema))
                schema_status = declaration.status
    checks.append(_check("BAZAAR_EXTENSION", extension_status, evidence))

    try:
        required.selected_requirement(0)
    except ValueError:
        primary_status = BazaarStatus.UNSUPPORTED
    else:
        primary_status = BazaarStatus.MATCH
    checks.append(_check("PRIMARY_REQUIREMENT", primary_status, evidence))
    checks.append(_check("INPUT_METHOD", input_status, evidence))
    checks.append(_check("DECLARATION_SCHEMA", schema_status, evidence))
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
