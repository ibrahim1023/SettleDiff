from pathlib import Path

from typer.testing import CliRunner

from settlediff.cli import app

runner = CliRunner()


def test_authentication_never_falls_back_to_integrity(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        ["verify-assurance", str(tmp_path / "bundle"), "--signature", str(tmp_path / "signature")],
    )
    assert result.exit_code != 0
    assert "UNTRUSTED_ISSUER" in result.stdout


def test_explicit_key_setup_requires_matching_hidden_passwords(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        [
            "issuer-keygen",
            "--private-key",
            str(tmp_path / "key.pem"),
            "--public-key",
            str(tmp_path / "key.pub"),
        ],
        input="synthetic-password\ndifferent-password\n",
    )
    assert result.exit_code != 0
    assert not (tmp_path / "key.pem").exists()
    assert "synthetic-password" not in result.output


def test_missing_assurance_is_diagnostic(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        [
            "verify-assurance",
            str(tmp_path / "bundle"),
            "--signature",
            str(tmp_path / "signature"),
            "--issuer-fingerprint",
            "0" * 64,
        ],
    )
    assert result.exit_code != 0
    assert "MISSING_ARTIFACT" in result.stdout
