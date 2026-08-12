from __future__ import annotations

from pathlib import Path

import pytest

from settlediff.application.replay import assert_sanitized_fixture, replay_fixture


@pytest.mark.parametrize(
    "value",
    [
        "a@b.example",
        "Bearer secret-value",
        "0x1234567890123456789012345678901234567890",
        "x4pZq8Nv9Lm2Cr7Yt5Wb1Kd6Hs3Fa0Ju9Qe8Rx4T",
    ],
)
def test_fixture_sanitizer_rejects_sensitive_or_high_entropy_content(value: str) -> None:
    with pytest.raises(ValueError):
        assert_sanitized_fixture(value)


def test_fixture_sanitizer_allows_explained_synthetic_identifiers() -> None:
    assert_sanitized_fixture('{"transactionId":"syn_tx_paid_failure_001"}')


def test_replay_sanitizes_intent_input(tmp_path: Path) -> None:
    (tmp_path / "manifest.json").write_text(
        '{"schema_version":1,"scenario":"synthetic","synthetic":true,'
        '"expected_verdict":"VERIFIED","artifacts":[]}'
    )
    (tmp_path / "intent.json").write_text('{"email":"person@example.invalid"}')

    with pytest.raises(ValueError, match="email"):
        replay_fixture(tmp_path)
