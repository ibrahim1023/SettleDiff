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


class X402ProtocolError(ValueError):
    pass


def parse_payment_required(
    header: object,
    *,
    max_header_bytes: int = DEFAULT_MAX_HEADER_BYTES,
    max_decoded_bytes: int = DEFAULT_MAX_DECODED_BYTES,
    max_json_depth: int = DEFAULT_MAX_JSON_DEPTH,
) -> PaymentRequired:
    return _parse_header(
        header,
        PaymentRequired,
        "PAYMENT-REQUIRED",
        max_header_bytes=max_header_bytes,
        max_decoded_bytes=max_decoded_bytes,
        max_json_depth=max_json_depth,
    )


def parse_payment_response(
    header: object,
    *,
    max_header_bytes: int = DEFAULT_MAX_HEADER_BYTES,
    max_decoded_bytes: int = DEFAULT_MAX_DECODED_BYTES,
    max_json_depth: int = DEFAULT_MAX_JSON_DEPTH,
) -> SettlementResponse:
    return _parse_header(
        header,
        SettlementResponse,
        "PAYMENT-RESPONSE",
        max_header_bytes=max_header_bytes,
        max_decoded_bytes=max_decoded_bytes,
        max_json_depth=max_json_depth,
    )


def _parse_header[ModelT: BaseModel](
    header: object,
    model: type[ModelT],
    name: str,
    *,
    max_header_bytes: int,
    max_decoded_bytes: int,
    max_json_depth: int,
) -> ModelT:
    if max_header_bytes < 1 or max_decoded_bytes < 1 or max_json_depth < 1:
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
