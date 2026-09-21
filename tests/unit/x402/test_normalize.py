from __future__ import annotations

import base64
import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from settlediff.domain.models import ResponseContract, SettlementStatus
from settlediff.domain.money import Money
from settlediff.x402.normalize import (
    X402NormalizationError,
    normalize_payment_required,
    normalize_payment_response,
)
from settlediff.x402.parser import parse_payment_required, parse_payment_response

NOW = datetime(2026, 8, 31, tzinfo=UTC)
BASE_SEPOLIA_USDC = "0x036CbD53842c5426634e7929541eC2318f3dCF7e"
FIXTURE = Path(__file__).parents[2] / "contract/x402/fixtures/payment-required-v2.json"


def challenge() -> dict[str, Any]:
    return json.loads(FIXTURE.read_text())


def encoded(value: object) -> str:
    return base64.b64encode(json.dumps(value, separators=(",", ":")).encode()).decode()


def test_normalize_payment_required_maps_exact_base_sepolia_usdc() -> None:
    required = parse_payment_required(encoded(challenge()))

    contract = normalize_payment_required(
        required,
        request_schema={"method": "GET", "body": None},
    )

    assert contract.price == Money(amount=Decimal("0.001"), unit="USDC")
    assert contract.asset == "USDC"
    assert contract.protocol == "x402"
    assert contract.scheme == "exact"
    assert contract.chain is None
    assert contract.network == "eip155:84532"
    assert contract.recipient == "0x1111111111111111111111111111111111111111"
    assert contract.max_timeout_seconds == 300
    assert contract.response_contract == ResponseContract(
        media_type="application/json",
        json_schema=None,
        source_fields=("resource.mimeType",),
    )
    assert contract.asset_identity is not None
    assert contract.asset_identity.symbol == "USDC"
    assert contract.asset_identity.network == "eip155:84532"
    assert contract.asset_identity.reference == BASE_SEPOLIA_USDC
    assert contract.asset_identity.decimals == 6


def test_normalize_payment_required_never_promotes_bazaar_schema_to_response_contract() -> None:
    payload = challenge()
    payload["extensions"] = {
        "bazaar": {
            "info": {
                "input": {"type": "http", "method": "GET", "queryParams": {}},
                "output": {"type": "json", "example": {"result": "ok"}},
            },
            "schema": {
                "type": "object",
                "required": ["input", "output"],
                "properties": {
                    "input": {"type": "object"},
                    "output": {"type": "object"},
                },
            },
        }
    }

    contract = normalize_payment_required(
        parse_payment_required(encoded(payload)), request_schema={"method": "GET"}
    )

    assert contract.schema_version == 3
    assert contract.response_contract == ResponseContract(
        media_type="application/json",
        json_schema=None,
        source_fields=("resource.mimeType",),
    )
    assert not any(
        note.startswith("x402 Bazaar declaration:") for note in contract.normalization_notes
    )


def test_normalize_payment_required_preserves_absent_response_contract() -> None:
    payload = challenge()
    payload["resource"].pop("mimeType")
    payload["extensions"] = {
        "bazaar": {
            "info": {
                "input": {"type": "http", "method": "GET"},
                "output": {"type": "json", "example": {"result": "ok"}},
            },
            "schema": {"type": "object"},
        }
    }

    contract = normalize_payment_required(
        parse_payment_required(encoded(payload)), request_schema={"method": "GET"}
    )

    assert contract.schema_version == 2
    assert contract.response_contract is None
    assert "response_contract" not in contract.model_dump(mode="json")


@pytest.mark.parametrize(
    ("bazaar", "diagnostic"),
    [
        (
            {
                "info": {"output": {"type": "json"}},
                "schema": {
                    "type": "object",
                    "properties": {"info": {"type": "object", "maxLength": 20}},
                },
            },
            "BAZAAR_DECLARATION_SCHEMA_UNSUPPORTED",
        ),
        (
            {
                "info": {"output": {"type": "json"}},
                "schema": {"type": "bogus"},
            },
            "BAZAAR_DECLARATION_MALFORMED",
        ),
        (
            {
                "info": {"output": {"type": "json"}},
                "schema": {"type": "object", "required": ["missing"]},
            },
            "BAZAAR_DECLARATION_DIFF",
        ),
        (None, "BAZAAR_DECLARATION_MALFORMED"),
    ],
)
def test_normalize_payment_required_flags_bazaar_declaration_without_raising(
    bazaar: object, diagnostic: str
) -> None:
    payload = challenge()
    payload["extensions"] = {"bazaar": bazaar}

    contract = normalize_payment_required(
        parse_payment_required(encoded(payload)), request_schema={"method": "GET"}
    )

    assert f"x402 Bazaar declaration: {diagnostic}" in contract.normalization_notes
    assert contract.response_contract is not None
    assert contract.response_contract.json_schema is None


def test_normalize_payment_required_rejects_unsupported_primary_requirement() -> None:
    payload = json.loads((FIXTURE.parent / "payment-required-multi-network-v2.json").read_text())
    payload["accepts"] = [payload["accepts"][1], payload["accepts"][0]]
    required = parse_payment_required(encoded(payload))

    with pytest.raises(X402NormalizationError, match="selected payment requirement"):
        normalize_payment_required(required, request_schema={"method": "GET"})


def test_normalize_payment_required_rejects_untrusted_asset_identity() -> None:
    payload = challenge()
    payload["accepts"][0]["asset"] = "0x4444444444444444444444444444444444444444"
    required = parse_payment_required(encoded(payload))

    with pytest.raises(X402NormalizationError, match="asset"):
        normalize_payment_required(required, request_schema={"method": "GET"})


@pytest.mark.parametrize(
    ("payload", "expected_status", "expected_amount"),
    [
        (
            {
                "success": True,
                "transaction": "0x2222222222222222222222222222222222222222222222222222222222222222",
                "network": "eip155:84532",
                "payer": "0x3333333333333333333333333333333333333333",
                "amount": "1000",
            },
            SettlementStatus.SETTLED,
            Money(amount=Decimal("0.001"), unit="USDC"),
        ),
        (
            {
                "success": False,
                "errorReason": "insufficient_funds",
                "transaction": "",
                "network": "eip155:84532",
            },
            SettlementStatus.FAILED,
            None,
        ),
        (
            {
                "success": False,
                "errorReason": "settlement_pending",
                "transaction": "0x2222222222222222222222222222222222222222222222222222222222222222",
                "network": "eip155:84532",
            },
            SettlementStatus.PENDING,
            None,
        ),
    ],
)
def test_normalize_provider_settlement_preserves_claim_without_inventing_fields(
    payload: dict[str, object],
    expected_status: SettlementStatus,
    expected_amount: Money | None,
) -> None:
    required = parse_payment_required(encoded(challenge()))
    response = parse_payment_response(encoded(payload))

    receipt = normalize_payment_response(response, required.selected_requirement(), issued_at=NOW)

    assert receipt.settlement_status is expected_status
    assert receipt.amount == expected_amount
    assert receipt.asset == ("USDC" if expected_amount is not None else None)
    assert receipt.asset_identity == (
        normalize_payment_required(required, request_schema={}).asset_identity
        if expected_amount is not None
        else None
    )
    assert receipt.protocol == "x402"
    assert receipt.scheme == "exact"
    assert receipt.chain is None
    assert receipt.network == "eip155:84532"
    assert receipt.recipient is None
    assert receipt.transaction_id is None
    assert receipt.session_id is None
    assert receipt.issued_at == NOW
