"""Deterministic redaction before evidence crosses a storage or display boundary."""

from __future__ import annotations

import re
from typing import cast

from pydantic import JsonValue

from settlediff.domain.models import EvidenceArtifact

REDACTED = "[REDACTED]"
EMAIL_PATTERN = re.compile(
    r"(?P<local>[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+)@"
    r"(?P<domain>[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?)"
)
PREFIXED_HEX_PATTERN = re.compile(r"0x[0-9a-fA-F]{16,}")
BARE_HEX_PATTERN = re.compile(r"\b[0-9a-fA-F]{32,128}\b")
SECRET_KEYS = {
    "apikey",
    "authorization",
    "bearer",
    "clientsecret",
    "cookie",
    "credential",
    "idtoken",
    "password",
    "privatekey",
    "refreshtoken",
    "secret",
    "token",
    "accesstoken",
}
IDENTIFIER_KEYS = {
    "accountid",
    "deviceid",
    "recipient",
    "recipientaddress",
    "sessionid",
    "transactionhash",
    "transactionid",
    "wallet",
    "walletaddress",
}


def normalize_key(key: str) -> str:
    return "".join(character for character in key.lower() if character.isalnum())


def _mask_email(match: re.Match[str]) -> str:
    local = match.group("local")
    visible = local[0] if len(local) > 1 else ""
    return f"{visible}***@{match.group('domain')}"


def _mask_hex(match: re.Match[str]) -> str:
    value = match.group(0)
    if value.startswith("0x"):
        return f"{value[:6]}…{value[-4:]}"
    return f"{value[:4]}…{value[-4:]}"


def redact_embedded_identifiers(value: str) -> str:
    redacted = EMAIL_PATTERN.sub(_mask_email, value)
    redacted = PREFIXED_HEX_PATTERN.sub(_mask_hex, redacted)
    return BARE_HEX_PATTERN.sub(_mask_hex, redacted)


def mask_identifier(value: str) -> str:
    """Mask one identifier while retaining a small correlation hint."""
    if value == REDACTED or "…" in value or "***@" in value:
        return value

    embedded_redacted = redact_embedded_identifiers(value)
    if embedded_redacted != value:
        return embedded_redacted
    if len(value) <= 8:
        return REDACTED
    return f"{value[:4]}…{value[-4:]}"


def redact_value(value: JsonValue, *, key: str | None = None) -> JsonValue:
    normalized_key = normalize_key(key) if key is not None else None
    if normalized_key in SECRET_KEYS:
        return REDACTED
    if normalized_key in IDENTIFIER_KEYS:
        return mask_identifier(value) if isinstance(value, str) else REDACTED

    if isinstance(value, dict):
        mapping = cast(dict[str, JsonValue], value)
        return {
            child_key: redact_value(child, key=child_key) for child_key, child in mapping.items()
        }
    if isinstance(value, list):
        items = cast(list[JsonValue], value)
        return [redact_value(item) for item in items]
    if isinstance(value, str):
        return redact_embedded_identifiers(value)
    return value


def redact_artifact(artifact: EvidenceArtifact) -> EvidenceArtifact:
    """Return a redacted copy of an evidence artifact."""
    return artifact.model_copy(update={"data": redact_value(artifact.data), "redacted": True})
