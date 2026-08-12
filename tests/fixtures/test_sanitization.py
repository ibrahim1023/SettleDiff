from __future__ import annotations

import pytest

from settlediff.application.replay import assert_sanitized_fixture


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
