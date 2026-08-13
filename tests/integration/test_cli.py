from __future__ import annotations

from typer.testing import CliRunner

from settlediff.cli import app

runner = CliRunner()


def test_fixture_replay_requires_no_live_configuration() -> None:
    result = runner.invoke(app, ["verify-fixture", "fixtures/paid-failure", "--json"])
    assert result.exit_code == 0
    assert '"verdict":"PAID_FAILURE"' in result.stdout


def test_live_run_rejects_invalid_json_before_any_adapter_call() -> None:
    result = runner.invoke(
        app, ["run", "--url", "https://example.invalid", "--body", "no", "--budget", "1"]
    )
    assert result.exit_code == 2
    assert "Invalid live preflight" in result.stderr
