from __future__ import annotations

from datetime import UTC, datetime
from typing import cast

from hypothesis import given
from hypothesis import strategies as st
from pydantic import JsonValue

from settlediff.domain.models import ArtifactType, EvidenceArtifact
from settlediff.domain.redaction import mask_identifier, redact_artifact

NOW = datetime(2026, 8, 12, 12, tzinfo=UTC)


def artifact_with_sensitive_data() -> EvidenceArtifact:
    return EvidenceArtifact(
        artifact_id="artifact_syn_001",
        artifact_type=ArtifactType.EXECUTION,
        source="synthetic_fixture",
        collected_at=NOW,
        redacted=False,
        data={
            "api_key": "hf_live_secret_value",
            "transactionId": "syn_transaction_identifier_001",
            "nested": {
                "session_id": "session-identifier-001",
                "contact": "alice@example.com",
            },
            "items": [
                "receipt for bob@example.com",
                "0x1234567890abcdef1234567890abcdef12345678",
            ],
        },
    )


def test_mask_identifier_handles_email_hex_and_short_values() -> None:
    assert mask_identifier("alice@example.com") == "a***@example.com"
    assert mask_identifier("0x1234567890abcdef1234567890abcdef12345678") == "0x1234…5678"
    assert mask_identifier("short") == "[REDACTED]"


def test_redact_artifact_recurses_without_mutating_source() -> None:
    artifact = artifact_with_sensitive_data()

    redacted = redact_artifact(artifact)

    assert redacted.redacted is True
    assert redacted.data == {
        "api_key": "[REDACTED]",
        "transactionId": "syn_…_001",
        "nested": {
            "session_id": "sess…-001",
            "contact": "a***@example.com",
        },
        "items": ["receipt for b***@example.com", "0x1234…5678"],
    }
    assert artifact.redacted is False
    assert isinstance(artifact.data, dict)
    original_data = cast(dict[str, JsonValue], artifact.data)
    assert original_data["api_key"] == "hf_live_secret_value"


def test_x402_signed_authorization_material_is_never_preserved() -> None:
    artifact = EvidenceArtifact(
        artifact_id="artifact_x402_signature",
        artifact_type=ArtifactType.EXECUTION,
        source="synthetic_x402",
        collected_at=NOW,
        redacted=False,
        data={
            "PAYMENT-SIGNATURE": "syn_reusable_signed_payment",
            "paymentPayload": {"signature": "syn_nested_signature"},
        },
    )

    assert redact_artifact(artifact).data == {
        "PAYMENT-SIGNATURE": "[REDACTED]",
        "paymentPayload": "[REDACTED]",
    }


def test_secret_like_keys_redact_non_string_values() -> None:
    artifact = EvidenceArtifact(
        artifact_id="artifact_syn_002",
        artifact_type=ArtifactType.SERVICE_RESPONSE,
        source="synthetic_fixture",
        collected_at=NOW,
        redacted=False,
        data={"authorization": {"nested": "value"}, "access_token": 12345},
    )

    assert redact_artifact(artifact).data == {
        "authorization": "[REDACTED]",
        "access_token": "[REDACTED]",
    }


def test_absent_identifier_values_remain_absent() -> None:
    artifact = EvidenceArtifact(
        artifact_id="artifact_syn_absent",
        artifact_type=ArtifactType.ACTIVITY,
        source="synthetic_fixture",
        collected_at=NOW,
        redacted=False,
        data={"transaction_id": None, "recipient": None},
    )

    assert redact_artifact(artifact).data == {"transaction_id": None, "recipient": None}


def test_identifier_keys_redact_non_string_values() -> None:
    artifact = EvidenceArtifact(
        artifact_id="artifact_syn_003",
        artifact_type=ArtifactType.ACTIVITY,
        source="synthetic_fixture",
        collected_at=NOW,
        redacted=False,
        data={"device_id": 12345, "recipient": {"raw": "value"}},
    )

    assert redact_artifact(artifact).data == {
        "device_id": "[REDACTED]",
        "recipient": "[REDACTED]",
    }


@given(st.text(alphabet=st.characters(categories=("L", "N")), min_size=9, max_size=80))
def test_mask_identifier_is_idempotent_and_hides_original(identifier: str) -> None:
    masked = mask_identifier(identifier)

    assert mask_identifier(masked) == masked
    assert identifier not in masked


@given(
    st.dictionaries(
        keys=st.sampled_from(["transaction_id", "sessionId", "device_id", "recipient"]),
        values=st.text(alphabet=st.characters(categories=("L", "N")), min_size=1, max_size=80),
        min_size=1,
        max_size=4,
    )
)
def test_redact_artifact_is_idempotent(data: dict[str, str]) -> None:
    artifact = EvidenceArtifact(
        artifact_id="artifact_syn_property",
        artifact_type=ArtifactType.ACTIVITY,
        source="synthetic_fixture",
        collected_at=NOW,
        redacted=False,
        data=cast(JsonValue, data),
    )

    once = redact_artifact(artifact)
    twice = redact_artifact(once)

    assert twice == once
