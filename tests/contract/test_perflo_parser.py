from __future__ import annotations

from pathlib import Path

import pytest

from settlediff.perflo.parser import (
    PerfloErrorEnvelope,
    PerfloProtocolError,
    PerfloSuccessEnvelope,
    parse_perflo_envelope,
)

FIXTURES = Path(__file__).parent / "perflo"


def fixture_bytes(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


@pytest.mark.parametrize("name", ["success.json", "paid_failure.json"])
def test_success_envelopes_preserve_the_complete_payload(name: str) -> None:
    stdout = fixture_bytes(name)

    envelope = parse_perflo_envelope(stdout, b"synthetic stderr", 0)

    assert isinstance(envelope, PerfloSuccessEnvelope)
    assert envelope.ok is True
    assert envelope.payload["result"]
    assert envelope.stdout_bytes == len(stdout)
    assert envelope.stderr_bytes == len(b"synthetic stderr")
    assert envelope.returncode == 0


def test_known_refusal_preserves_stable_error_fields() -> None:
    stdout = fixture_bytes("refusal.json")

    envelope = parse_perflo_envelope(stdout, b"", 1)

    assert isinstance(envelope, PerfloErrorEnvelope)
    assert envelope.error.code == "GUARDRAIL_DENIED"
    assert envelope.error.recoverable is False
    assert envelope.error.details == {"limit": "0.05", "attempted": "0.06"}
    assert envelope.error.hint == "Use a synthetic amount within the configured limit."
    assert envelope.error.submission_uncertain is False
    assert envelope.returncode == 1


def test_schema_evolution_fields_remain_in_raw_payload() -> None:
    envelope = parse_perflo_envelope(fixture_bytes("schema_evolution.json"), b"", 0)

    assert isinstance(envelope, PerfloSuccessEnvelope)
    assert envelope.payload["futureEnvelopeField"] == "preserve-me"
    result = envelope.payload["result"]
    assert isinstance(result, dict)
    assert result["futureResultField"] == {"contractVersion": 2}


@pytest.mark.parametrize(
    ("stdout", "message"),
    [
        (fixture_bytes("malformed.stdout"), "valid JSON"),
        (b"[]", "JSON object"),
        (b'{"ok": "true"}', "boolean 'ok'"),
        (b'{"ok": true} trailing', "valid JSON"),
        (b"\xff", "UTF-8"),
    ],
)
def test_protocol_errors_never_infer_state_from_prose(stdout: bytes, message: str) -> None:
    with pytest.raises(PerfloProtocolError, match=message) as error:
        parse_perflo_envelope(stdout, b"submissionUncertain=false", 1)

    assert error.value.stdout_bytes == len(stdout)
    assert error.value.stderr_bytes == len(b"submissionUncertain=false")
    assert error.value.returncode == 1


def test_output_limit_is_enforced_before_decoding() -> None:
    with pytest.raises(PerfloProtocolError, match="output limit"):
        parse_perflo_envelope(b"{}" * 10, b"", 0, max_output_bytes=8)


def test_error_envelope_requires_typed_stable_fields() -> None:
    with pytest.raises(PerfloProtocolError, match="error.recoverable"):
        parse_perflo_envelope(
            b'{"ok":false,"error":{"code":"NETWORK","message":"synthetic"}}',
            b"",
            1,
        )
