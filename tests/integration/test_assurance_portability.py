from pathlib import Path

import pytest

from scripts.assurance_portability import issue_fixture, verify_recipient


def test_recipient_checks_synthetic_delivery_without_issuer_state(tmp_path: Path) -> None:
    delivery = tmp_path / "delivery"
    fingerprint = issue_fixture(delivery)

    assert len(fingerprint) == 64
    assert {path.name for path in delivery.iterdir()} == {
        "bundle.json",
        "bundle.assurance.json",
        "legacy.bundle.json",
    }
    assert verify_recipient(delivery, fingerprint) == {
        "authentic": "AUTHENTIC",
        "tampered": "CONTENT_MISMATCH",
        "wrong_issuer": "UNTRUSTED_ISSUER",
        "revoked_issuer": "UNTRUSTED_ISSUER",
        "missing_signature": "MISSING_ARTIFACT",
        "legacy_integrity": "INTEGRITY_ONLY",
        "legacy_authentication": "CONTENT_MISMATCH",
    }


def test_recipient_trial_cannot_pass_with_the_wrong_external_pin(tmp_path: Path) -> None:
    delivery = tmp_path / "delivery"
    issue_fixture(delivery)

    with pytest.raises(RuntimeError, match="recipient check failed: authentic"):
        verify_recipient(delivery, "0" * 64)


def test_recipient_trial_rejects_unexpected_transferred_files(tmp_path: Path) -> None:
    delivery = tmp_path / "delivery"
    fingerprint = issue_fixture(delivery)
    (delivery / "issuer.sqlite").write_bytes(b"synthetic forbidden extra")

    with pytest.raises(ValueError, match="exactly the three expected files"):
        verify_recipient(delivery, fingerprint)


@pytest.mark.parametrize("stream", ["stdout", "stderr"])
def test_recipient_trial_bounds_child_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stream: str
) -> None:
    delivery = tmp_path / "delivery"
    fingerprint = issue_fixture(delivery)
    executable = tmp_path / "synthetic-child"
    redirect = " >&2" if stream == "stderr" else ""
    executable.write_text(f"#!/bin/sh\nprintf '%20000s' synthetic{redirect}\n")
    executable.chmod(0o700)
    monkeypatch.setattr("sys.executable", str(executable))

    with pytest.raises(RuntimeError, match="recipient CLI output limit exceeded"):
        verify_recipient(delivery, fingerprint)
