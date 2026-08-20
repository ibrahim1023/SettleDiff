"""Explicit raw Perflo artifact mappings for the deterministic domain core."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Final, cast

from pydantic import JsonValue

from settlediff.domain.models import (
    ArtifactType,
    EvidenceArtifact,
    ExecutionRecord,
    ExpectedContract,
    LedgerRecord,
    LedgerStatus,
    PaymentReceipt,
    SettlementStatus,
)
from settlediff.domain.money import Money

_RECOGNIZED_ASSETS: Final = frozenset({"USDC", "USDT"})
_MINOR_UNIT_EXPONENTS: Final = {"USDC": 6, "USDT": 6}
_RECOGNIZED_PROTOCOLS: Final = frozenset({"mpp"})
_RECOGNIZED_CHAINS: Final = frozenset({"base", "tempo"})


class ArtifactParseError(ValueError):
    """A raw artifact cannot satisfy a required canonical field without guessing."""

    def __init__(self, artifact_id: str, field_path: str, reason: str) -> None:
        super().__init__(f"artifact {artifact_id} cannot parse {field_path}: {reason}")
        self.artifact_id = artifact_id
        self.field_path = field_path


def normalize_contract(raw: EvidenceArtifact) -> ExpectedContract:
    """Map a service-contract artifact into strict canonical fields."""
    data = _artifact_object(raw, ArtifactType.SERVICE_CONTRACT)
    notes = _stored_notes(data, raw)
    price = _money(data, raw, amount_field="price_minor", unit_field="asset", required=False)
    return ExpectedContract(
        vendor_slug=_optional_string(data, raw, "vendor_slug"),
        url=_required_string(data, raw, "url"),
        price=price,
        asset=_normalized_name(data, raw, "asset", _RECOGNIZED_ASSETS, str.upper, notes),
        protocol=_normalized_name(data, raw, "protocol", _RECOGNIZED_PROTOCOLS, str.lower, notes),
        chain=_normalized_name(data, raw, "chain", _RECOGNIZED_CHAINS, str.lower, notes),
        request_schema=_required_schema(data, raw),
        normalization_notes=tuple(notes),
    )


def normalize_execution(raw: EvidenceArtifact) -> ExecutionRecord:
    """Map an execution artifact without treating settlement as service success."""
    data = _artifact_object(raw, ArtifactType.EXECUTION)
    notes = _stored_notes(data, raw)
    return ExecutionRecord(
        vendor_slug=_optional_string(data, raw, "vendor_slug"),
        upstream_http_status=_optional_http_status(data, raw),
        charge=_money(data, raw, amount_field="amount_minor", unit_field="asset", required=False),
        asset=_normalized_name(data, raw, "asset", _RECOGNIZED_ASSETS, str.upper, notes),
        protocol=_normalized_name(data, raw, "protocol", _RECOGNIZED_PROTOCOLS, str.lower, notes),
        chain=_normalized_name(data, raw, "chain", _RECOGNIZED_CHAINS, str.lower, notes),
        recipient=_optional_string(data, raw, "recipient"),
        settlement_status=_settlement_status(data, raw, notes),
        transaction_id=_optional_string(data, raw, "transaction_id"),
        session_id=_optional_string(data, raw, "session_id"),
        transaction_hash=_optional_string(data, raw, "transaction_hash"),
        response_body=_optional_json(data, raw, "response_body"),
        executed_at=_required_timestamp(data, raw, "executed_at"),
        normalization_notes=tuple(notes),
    )


def normalize_receipt(raw: EvidenceArtifact) -> PaymentReceipt:
    """Map a receipt only to fields used by deterministic consistency checks."""
    data = _artifact_object(raw, ArtifactType.PAYMENT_RECEIPT)
    notes = _stored_notes(data, raw)
    return PaymentReceipt(
        amount=_money(data, raw, amount_field="amount", unit_field="asset", required=False),
        asset=_normalized_name(data, raw, "asset", _RECOGNIZED_ASSETS, str.upper, notes),
        protocol=_normalized_name(data, raw, "protocol", _RECOGNIZED_PROTOCOLS, str.lower, notes),
        chain=_normalized_name(data, raw, "chain", _RECOGNIZED_CHAINS, str.lower, notes),
        recipient=_optional_string(data, raw, "recipient"),
        settlement_status=_settlement_status(data, raw, notes),
        transaction_id=_optional_string(data, raw, "transaction_id"),
        session_id=_optional_string(data, raw, "session_id"),
        transaction_hash=_optional_string(data, raw, "transaction_hash"),
        issued_at=_optional_timestamp(data, raw, "issued_at"),
        normalization_notes=tuple(notes),
    )


def normalize_activity(raw: EvidenceArtifact) -> tuple[LedgerRecord, ...]:
    """Map each Activity candidate; matching remains a later deterministic step."""
    if raw.artifact_type is not ArtifactType.ACTIVITY:
        raise ArtifactParseError(raw.artifact_id, "artifact_type", "expected activity")
    if not isinstance(raw.data, list):
        raise ArtifactParseError(raw.artifact_id, "data", "expected a JSON array")

    records: list[LedgerRecord] = []
    for index, entry in enumerate(raw.data):
        if not isinstance(entry, dict):
            raise ArtifactParseError(raw.artifact_id, f"data[{index}]", "expected a JSON object")
        data = cast(dict[str, JsonValue], entry)
        notes = _stored_notes(data, raw, prefix=f"data[{index}].")
        records.append(
            LedgerRecord(
                ledger_id=_required_string(data, raw, "ledger_id", prefix=f"data[{index}]."),
                vendor_slug=_optional_string(data, raw, "vendor_slug", prefix=f"data[{index}]."),
                amount=_money(
                    data,
                    raw,
                    amount_field="amount",
                    unit_field="asset",
                    required=False,
                    prefix=f"data[{index}].",
                ),
                asset=_normalized_name(
                    data,
                    raw,
                    "asset",
                    _RECOGNIZED_ASSETS,
                    str.upper,
                    notes,
                    prefix=f"data[{index}].",
                ),
                protocol=_normalized_name(
                    data,
                    raw,
                    "protocol",
                    _RECOGNIZED_PROTOCOLS,
                    str.lower,
                    notes,
                    prefix=f"data[{index}].",
                ),
                chain=_normalized_name(
                    data,
                    raw,
                    "chain",
                    _RECOGNIZED_CHAINS,
                    str.lower,
                    notes,
                    prefix=f"data[{index}].",
                ),
                recipient=_optional_string(data, raw, "recipient", prefix=f"data[{index}]."),
                status=_ledger_status(data, raw, notes, prefix=f"data[{index}]."),
                error_reason=_optional_string(data, raw, "error_reason", prefix=f"data[{index}]."),
                transaction_id=_optional_string(
                    data, raw, "transaction_id", prefix=f"data[{index}]."
                ),
                session_id=_optional_string(data, raw, "session_id", prefix=f"data[{index}]."),
                transaction_hash=_optional_string(
                    data, raw, "transaction_hash", prefix=f"data[{index}]."
                ),
                occurred_at=_required_timestamp(data, raw, "occurred_at", prefix=f"data[{index}]."),
                normalization_notes=tuple(notes),
            )
        )
    return tuple(records)


_ALIASES: Final[dict[str, tuple[str, ...]]] = {
    "vendor_slug": ("vendor_slug", "vendorSlug"),
    "request_schema": ("request_schema", "requestSchema"),
    "price_minor": ("price_minor", "priceMinor"),
    "price_minor_units": ("price_minor_units", "priceMinorUnits"),
    "amount_minor": ("amount_minor", "amountMinor"),
    "amount_minor_units": ("amount_minor_units", "amountMinorUnits"),
    "upstream_http_status": ("upstream_http_status", "upstreamHttpStatus"),
    "settlement_status": ("settlement_status", "settlementStatus"),
    "transaction_id": ("transaction_id", "transactionId"),
    "session_id": ("session_id", "sessionId"),
    "transaction_hash": ("transaction_hash", "transactionHash", "txHash"),
    "response_body": ("response_body", "responseBody"),
    "executed_at": ("executed_at", "executedAt"),
    "issued_at": ("issued_at", "issuedAt"),
    "ledger_id": ("ledger_id", "ledgerId", "id"),
    "error_reason": ("error_reason", "errorReason"),
    "occurred_at": ("occurred_at", "occurredAt", "timestamp"),
}


def _artifact_object(raw: EvidenceArtifact, expected_type: ArtifactType) -> dict[str, JsonValue]:
    if raw.artifact_type is not expected_type:
        raise ArtifactParseError(
            raw.artifact_id, "artifact_type", f"expected {expected_type.value}"
        )
    if not isinstance(raw.data, dict):
        raise ArtifactParseError(raw.artifact_id, "data", "expected a JSON object")
    return cast(dict[str, JsonValue], raw.data)


def _field(
    data: dict[str, JsonValue], raw: EvidenceArtifact, field: str, *, prefix: str = "data."
) -> JsonValue | None:
    aliases = _ALIASES.get(field, (field,))
    present = [(alias, data[alias]) for alias in aliases if alias in data]
    if not present:
        return None
    _, first_value = present[0]
    if any(value != first_value for _, value in present[1:]):
        raise ArtifactParseError(
            raw.artifact_id, f"{prefix}{field}", "conflicting documented aliases"
        )
    return first_value


def _required_string(
    data: dict[str, JsonValue], raw: EvidenceArtifact, field: str, *, prefix: str = "data."
) -> str:
    value = _field(data, raw, field, prefix=prefix)
    if not isinstance(value, str) or not value.strip():
        raise ArtifactParseError(raw.artifact_id, f"{prefix}{field}", "required non-empty string")
    return value.strip()


def _optional_string(
    data: dict[str, JsonValue], raw: EvidenceArtifact, field: str, *, prefix: str = "data."
) -> str | None:
    value = _field(data, raw, field, prefix=prefix)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ArtifactParseError(raw.artifact_id, f"{prefix}{field}", "string or null")
    return value.strip()


def _required_schema(data: dict[str, JsonValue], raw: EvidenceArtifact) -> dict[str, JsonValue]:
    value = _field(data, raw, "request_schema")
    if isinstance(value, dict):
        return cast(dict[str, JsonValue], value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as error:
            raise ArtifactParseError(
                raw.artifact_id, "data.request_schema", "required JSON object"
            ) from error
        if isinstance(parsed, dict):
            return cast(dict[str, JsonValue], parsed)
    raise ArtifactParseError(raw.artifact_id, "data.request_schema", "required JSON object")


def _optional_json(
    data: dict[str, JsonValue], raw: EvidenceArtifact, field: str
) -> JsonValue | None:
    return _field(data, raw, field)


def _normalized_name(
    data: dict[str, JsonValue],
    raw: EvidenceArtifact,
    field: str,
    recognized: frozenset[str],
    normalize: Callable[[str], str],
    notes: list[str],
    *,
    prefix: str = "data.",
) -> str | None:
    value = _optional_string(data, raw, field, prefix=prefix)
    if value is None:
        return None
    normalized = normalize(value)
    if normalized == "unknown":
        return normalized
    if normalized not in recognized:
        notes.append(f"unknown {field} at {prefix}{field}")
        return "unknown"
    return normalized


def _optional_http_status(data: dict[str, JsonValue], raw: EvidenceArtifact) -> int | None:
    value = _field(data, raw, "upstream_http_status")
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or not 100 <= value <= 599:
        raise ArtifactParseError(
            raw.artifact_id, "data.upstream_http_status", "HTTP status from 100 to 599"
        )
    return value


def _stored_notes(
    data: dict[str, JsonValue], raw: EvidenceArtifact, *, prefix: str = "data."
) -> list[str]:
    value = data.get("normalization_notes")
    if value is None:
        return []
    if not isinstance(value, list) or any(not isinstance(note, str) or not note for note in value):
        raise ArtifactParseError(
            raw.artifact_id, f"{prefix}normalization_notes", "array of strings"
        )
    return [cast(str, note) for note in value]


def _settlement_status(
    data: dict[str, JsonValue], raw: EvidenceArtifact, notes: list[str]
) -> SettlementStatus:
    value = _field(data, raw, "settlement_status")
    if value is None:
        return SettlementStatus.UNKNOWN
    if not isinstance(value, str):
        raise ArtifactParseError(raw.artifact_id, "data.settlement_status", "string or null")
    try:
        return SettlementStatus(value.lower())
    except ValueError:
        notes.append("unknown settlement status at data.settlement_status")
        return SettlementStatus.UNKNOWN


def _ledger_status(
    data: dict[str, JsonValue], raw: EvidenceArtifact, notes: list[str], *, prefix: str
) -> LedgerStatus:
    value = _field(data, raw, "status", prefix=prefix)
    if value is None:
        return LedgerStatus.UNKNOWN
    if not isinstance(value, str):
        raise ArtifactParseError(raw.artifact_id, f"{prefix}status", "string or null")
    normalized = value.lower()
    aliases = {"confirmed": LedgerStatus.CONFIRMED, "settled": LedgerStatus.CONFIRMED}
    if normalized in aliases:
        return aliases[normalized]
    try:
        return LedgerStatus(normalized)
    except ValueError:
        notes.append(f"unknown status at {prefix}status")
        return LedgerStatus.UNKNOWN


def _required_timestamp(
    data: dict[str, JsonValue], raw: EvidenceArtifact, field: str, *, prefix: str = "data."
) -> datetime:
    value = _field(data, raw, field, prefix=prefix)
    if not isinstance(value, str):
        raise ArtifactParseError(
            raw.artifact_id, f"{prefix}{field}", "required UTC timestamp string"
        )
    return _parse_utc_timestamp(value, raw, f"{prefix}{field}")


def _optional_timestamp(
    data: dict[str, JsonValue], raw: EvidenceArtifact, field: str
) -> datetime | None:
    value = _field(data, raw, field)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ArtifactParseError(raw.artifact_id, f"data.{field}", "UTC timestamp string or null")
    return _parse_utc_timestamp(value, raw, f"data.{field}")


def _parse_utc_timestamp(value: str, raw: EvidenceArtifact, field_path: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ArtifactParseError(raw.artifact_id, field_path, "ISO-8601 UTC timestamp") from error
    if parsed.utcoffset() != UTC.utcoffset(parsed):
        raise ArtifactParseError(raw.artifact_id, field_path, "timezone-aware UTC timestamp")
    return parsed.astimezone(UTC)


def _money(
    data: dict[str, JsonValue],
    raw: EvidenceArtifact,
    *,
    amount_field: str,
    unit_field: str,
    required: bool,
    prefix: str = "data.",
) -> Money | None:
    amount = _field(data, raw, amount_field, prefix=prefix)
    canonical_field = {"price_minor": "price", "amount_minor": "charge"}.get(amount_field)
    if canonical_field is not None and canonical_field in data:
        if amount is not None:
            raise ArtifactParseError(
                raw.artifact_id,
                f"{prefix}{amount_field}",
                "cannot mix minor and canonical money fields",
            )
        amount = data[canonical_field]
    if amount is None:
        if required:
            raise ArtifactParseError(
                raw.artifact_id, f"{prefix}{amount_field}", "required money value"
            )
        return None
    unit = _field(data, raw, unit_field, prefix=prefix)
    if not isinstance(unit, str) or not unit.strip():
        raise ArtifactParseError(raw.artifact_id, f"{prefix}{unit_field}", "required money unit")

    try:
        if isinstance(amount, dict):
            return Money.model_validate(amount)
        minor_units = _field(data, raw, f"{amount_field}_units", prefix=prefix)
        if amount_field.endswith("_minor") and canonical_field is None:
            raise AssertionError("minor money field must have a canonical counterpart")
        if amount_field.endswith("_minor") and canonical_field not in data and minor_units is None:
            minor_units = _MINOR_UNIT_EXPONENTS.get(unit.strip().upper())
            if minor_units is None:
                raise ArtifactParseError(
                    raw.artifact_id,
                    f"{prefix}{amount_field}_units",
                    "required minor-unit exponent",
                )
        if minor_units is not None:
            if isinstance(minor_units, bool) or not isinstance(minor_units, int):
                raise ArtifactParseError(
                    raw.artifact_id, f"{prefix}{amount_field}_units", "non-negative integer"
                )
            return Money(
                amount=_decimal(amount, raw, f"{prefix}{amount_field}"),
                unit=unit,
                minor_units=minor_units,
            )
        return Money(amount=_decimal_cost(amount, raw, f"{prefix}{amount_field}"), unit=unit)
    except ArtifactParseError:
        raise
    except ValueError as error:
        raise ArtifactParseError(raw.artifact_id, f"{prefix}{amount_field}", str(error)) from error


def _decimal(value: JsonValue, raw: EvidenceArtifact, field_path: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise ArtifactParseError(raw.artifact_id, field_path, "integer or decimal string")
    try:
        return Decimal(str(value))
    except InvalidOperation as error:
        raise ArtifactParseError(raw.artifact_id, field_path, "valid decimal") from error


def _decimal_cost(value: JsonValue, raw: EvidenceArtifact, field_path: str) -> Decimal:
    if not isinstance(value, str):
        return _decimal(value, raw, field_path)
    raw_number = value[1:] if value.startswith("$") else value
    return _decimal(raw_number, raw, field_path)
