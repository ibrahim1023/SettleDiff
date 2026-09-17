"""Bounded decoding of x402 v2 HTTP payment headers."""

from __future__ import annotations

import base64
import binascii
import json
from typing import cast

from pydantic import BaseModel, ValidationError

from settlediff.x402.models import PaymentRequired, SettlementResponse

DEFAULT_MAX_HEADER_BYTES = 65_536
DEFAULT_MAX_DECODED_BYTES = 49_152
DEFAULT_MAX_JSON_DEPTH = 16
DEFAULT_MAX_JSON_PROPERTIES = 512
DEFAULT_MAX_JSON_STRING_BYTES = 4_096


class X402ProtocolError(ValueError):
    pass


def parse_payment_required(
    header: object,
    *,
    max_header_bytes: int = DEFAULT_MAX_HEADER_BYTES,
    max_decoded_bytes: int = DEFAULT_MAX_DECODED_BYTES,
    max_json_depth: int = DEFAULT_MAX_JSON_DEPTH,
    max_json_properties: int = DEFAULT_MAX_JSON_PROPERTIES,
    max_json_string_bytes: int = DEFAULT_MAX_JSON_STRING_BYTES,
) -> PaymentRequired:
    return _parse_header(
        header,
        PaymentRequired,
        "PAYMENT-REQUIRED",
        max_header_bytes=max_header_bytes,
        max_decoded_bytes=max_decoded_bytes,
        max_json_depth=max_json_depth,
        max_json_properties=max_json_properties,
        max_json_string_bytes=max_json_string_bytes,
    )


def parse_payment_response(
    header: object,
    *,
    max_header_bytes: int = DEFAULT_MAX_HEADER_BYTES,
    max_decoded_bytes: int = DEFAULT_MAX_DECODED_BYTES,
    max_json_depth: int = DEFAULT_MAX_JSON_DEPTH,
    max_json_properties: int = DEFAULT_MAX_JSON_PROPERTIES,
    max_json_string_bytes: int = DEFAULT_MAX_JSON_STRING_BYTES,
) -> SettlementResponse:
    return _parse_header(
        header,
        SettlementResponse,
        "PAYMENT-RESPONSE",
        max_header_bytes=max_header_bytes,
        max_decoded_bytes=max_decoded_bytes,
        max_json_depth=max_json_depth,
        max_json_properties=max_json_properties,
        max_json_string_bytes=max_json_string_bytes,
    )


def _parse_header[ModelT: BaseModel](
    header: object,
    model: type[ModelT],
    name: str,
    *,
    max_header_bytes: int,
    max_decoded_bytes: int,
    max_json_depth: int,
    max_json_properties: int,
    max_json_string_bytes: int,
) -> ModelT:
    if any(
        limit < 1
        for limit in (
            max_header_bytes,
            max_decoded_bytes,
            max_json_depth,
            max_json_properties,
            max_json_string_bytes,
        )
    ):
        raise X402ProtocolError("x402 parser limits must be positive")
    if not isinstance(header, str):
        raise X402ProtocolError(f"{name} must be a string")
    try:
        encoded = header.encode("ascii", errors="strict")
    except UnicodeEncodeError as error:
        raise X402ProtocolError(f"{name} must contain ASCII base64") from error
    if len(encoded) > max_header_bytes:
        raise X402ProtocolError(f"{name} exceeded the encoded header limit")
    try:
        decoded = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as error:
        raise X402ProtocolError(f"{name} must contain valid base64") from error
    if len(decoded) > max_decoded_bytes:
        raise X402ProtocolError(f"{name} exceeded the decoded JSON limit")
    try:
        loaded: object = json.loads(decoded.decode("utf-8", errors="strict"))
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as error:
        raise X402ProtocolError(f"{name} must contain one UTF-8 JSON value") from error
    if not isinstance(loaded, dict):
        raise X402ProtocolError(f"{name} JSON must be an object")
    mapping = cast(dict[object, object], loaded)
    if _json_depth(mapping) > max_json_depth:
        raise X402ProtocolError(f"{name} JSON exceeded the depth limit")
    if _json_property_count(mapping) > max_json_properties:
        raise X402ProtocolError(f"{name} JSON exceeded the property-count limit")
    if _max_json_string_bytes(mapping) > max_json_string_bytes:
        raise X402ProtocolError(f"{name} JSON exceeded the string-size limit")
    try:
        return model.model_validate_json(decoded, strict=True)
    except ValidationError as error:
        raise X402ProtocolError(f"invalid {name} evidence: {error}") from error


def _json_depth(value: object) -> int:
    if isinstance(value, dict):
        mapping = cast(dict[object, object], value)
        return 1 + max((_json_depth(child) for child in mapping.values()), default=0)
    if isinstance(value, list):
        items = cast(list[object], value)
        return 1 + max((_json_depth(child) for child in items), default=0)
    return 0


def _json_property_count(value: object) -> int:
    if isinstance(value, dict):
        mapping = cast(dict[object, object], value)
        return len(mapping) + sum(_json_property_count(child) for child in mapping.values())
    if isinstance(value, list):
        return sum(_json_property_count(child) for child in cast(list[object], value))
    return 0


def _max_json_string_bytes(value: object) -> int:
    if isinstance(value, str):
        return len(value.encode("utf-8"))
    if isinstance(value, dict):
        mapping = cast(dict[object, object], value)
        return max(
            (
                max(
                    len(str(key).encode("utf-8")),
                    _max_json_string_bytes(child),
                )
                for key, child in mapping.items()
            ),
            default=0,
        )
    if isinstance(value, list):
        return max(
            (_max_json_string_bytes(child) for child in cast(list[object], value)),
            default=0,
        )
    return 0
