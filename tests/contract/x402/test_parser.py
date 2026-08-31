from __future__ import annotations

import base64
import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from settlediff.x402.parser import (
    X402ProtocolError,
    parse_payment_required,
    parse_payment_response,
)

BASE_SEPOLIA_USDC = "0x036CbD53842c5426634e7929541eC2318f3dCF7e"
SYNTHETIC_RECIPIENT = "0x1111111111111111111111111111111111111111"
FIXTURES = Path(__file__).with_name("fixtures")


def encoded(value: object) -> str:
    return base64.b64encode(json.dumps(value, separators=(",", ":")).encode()).decode()


def challenge() -> dict[str, Any]:
    return json.loads((FIXTURES / "payment-required-v2.json").read_text())


def test_parse_captured_payment_required_header() -> None:
    parsed = parse_payment_required(encoded(challenge()))

    assert parsed.x402_version == 2
    assert parsed.resource.url == "http://127.0.0.1:4021/weather"
    assert parsed.accepts[0].scheme == "exact"
    assert parsed.accepts[0].network == "eip155:84532"
    assert parsed.accepts[0].amount == "1000"
    assert parsed.accepts[0].asset == BASE_SEPOLIA_USDC
    assert parsed.accepts[0].pay_to == SYNTHETIC_RECIPIENT
    assert parsed.accepts[0].extra.asset_transfer_method == "eip3009"


@pytest.mark.parametrize(
    ("case", "message"),
    [
        ("version", "x402Version"),
        ("scheme", "scheme"),
        ("network", "network"),
        ("missing_pay_to", "payTo"),
        ("invalid_pay_to", "payTo"),
        ("amount", "amount"),
        ("url", "url"),
        ("extra", "extra"),
    ],
)
def test_parse_payment_required_rejects_unsupported_or_malformed_fields(
    case: str, message: str
) -> None:
    payload = deepcopy(challenge())
    requirement = payload["accepts"][0]
    if case == "version":
        payload["x402Version"] = 1
    elif case == "scheme":
        requirement["scheme"] = "upto"
    elif case == "network":
        requirement["network"] = "eip155:8453"
    elif case == "missing_pay_to":
        requirement.pop("payTo")
    elif case == "invalid_pay_to":
        requirement["payTo"] = "not-an-address"
    elif case == "amount":
        requirement["amount"] = "0"
    elif case == "url":
        payload["resource"]["url"] = "relative/path"
    else:
        payload["invented"] = True

    with pytest.raises(X402ProtocolError, match=message):
        parse_payment_required(encoded(payload))


@pytest.mark.parametrize("header", ["%%not-base64%%", encoded(["not", "an", "object"])])
def test_parse_payment_required_rejects_invalid_encoding_or_shape(header: str) -> None:
    with pytest.raises(X402ProtocolError):
        parse_payment_required(header)


def test_parse_payment_required_enforces_encoded_and_decoded_limits() -> None:
    header = encoded(challenge())

    with pytest.raises(X402ProtocolError, match="encoded"):
        parse_payment_required(header, max_header_bytes=len(header) - 1)
    with pytest.raises(X402ProtocolError, match="decoded"):
        parse_payment_required(header, max_decoded_bytes=10)


def test_parse_payment_required_rejects_excessive_json_depth() -> None:
    payload = challenge()
    nested: dict[str, object] = {}
    payload["extensions"] = nested
    for _ in range(20):
        child: dict[str, object] = {}
        nested["nested"] = child
        nested = child

    with pytest.raises(X402ProtocolError, match="depth"):
        parse_payment_required(encoded(payload), max_json_depth=12)


@pytest.mark.parametrize(
    "payload",
    [
        {
            "success": True,
            "transaction": "0x2222222222222222222222222222222222222222222222222222222222222222",
            "network": "eip155:84532",
            "payer": "0x3333333333333333333333333333333333333333",
            "amount": "1000",
        },
        {
            "success": False,
            "errorReason": "insufficient_funds",
            "transaction": "",
            "network": "eip155:84532",
        },
        {
            "success": False,
            "errorReason": "settlement_pending",
            "transaction": "0x2222222222222222222222222222222222222222222222222222222222222222",
            "network": "eip155:84532",
        },
    ],
)
def test_parse_payment_response_accepts_specified_terminal_and_pending_shapes(
    payload: dict[str, object],
) -> None:
    parsed = parse_payment_response(encoded(payload))

    assert parsed.success is payload["success"]
    assert parsed.network == "eip155:84532"


@pytest.mark.parametrize(
    "payload",
    [
        {"success": True, "transaction": "", "network": "eip155:84532"},
        {
            "success": False,
            "errorReason": "settlement_pending",
            "transaction": "",
            "network": "eip155:84532",
        },
        {"success": False, "transaction": "", "network": "eip155:84532"},
        {
            "success": False,
            "errorReason": "failed",
            "transaction": "",
            "network": "eip155:8453",
        },
    ],
)
def test_parse_payment_response_rejects_incoherent_or_unsupported_shapes(
    payload: dict[str, object],
) -> None:
    with pytest.raises(X402ProtocolError):
        parse_payment_response(encoded(payload))
