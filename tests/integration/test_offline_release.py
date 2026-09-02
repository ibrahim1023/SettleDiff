from __future__ import annotations

import socket
from pathlib import Path
from typing import NoReturn

import pytest
from fastapi.testclient import TestClient
from pydantic_ai import models
from typer.testing import CliRunner

from settlediff.api.app import create_app
from settlediff.application.replay import replay_fixture
from settlediff.cli import app
from settlediff.domain.models import MachineReport
from settlediff.domain.redaction import redact_report
from settlediff.storage.sqlite import SQLiteReportRepository


def test_complete_fixture_path_remains_offline(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def block_network(*_args: object, **_kwargs: object) -> NoReturn:
        raise AssertionError("the offline release path attempted a network connection")

    monkeypatch.setattr(socket, "create_connection", block_network)
    monkeypatch.setattr(socket.socket, "connect", block_network)
    assert models.ALLOW_MODEL_REQUESTS is False

    runner = CliRunner()
    database = tmp_path / "offline-release.sqlite3"
    fixture_reports = tuple(
        (path, replay_fixture(path)) for path in sorted(Path("fixtures").iterdir()) if path.is_dir()
    )
    assert len(fixture_reports) == 16
    assert {path.name for path, _report in fixture_reports} >= {
        "x402-clean-success",
        "x402-paid-failure",
        "x402-uncertain-submission",
        "x402-provider-success-independent-failure",
        "x402-provider-failure-independent-confirmation",
        "x402-wrong-recipient",
        "x402-wrong-amount",
        "x402-wrong-asset",
        "x402-wrong-network",
    }

    for fixture_path, report in fixture_reports:
        human = runner.invoke(
            app,
            ["verify-fixture", str(fixture_path), "--database", str(database)],
        )
        assert human.exit_code == 0, human.output
        assert human.stdout.splitlines()[0] == report.verdict.value

        canonical = runner.invoke(app, ["verify-fixture", str(fixture_path), "--json"])
        assert canonical.exit_code == 0, canonical.output
        assert MachineReport.model_validate_json(canonical.stdout) == report
        persisted = runner.invoke(
            app, ["show", report.run_id, "--database", str(database), "--json"]
        )
        assert persisted.exit_code == 0, persisted.output
        persisted_report = MachineReport.model_validate_json(persisted.stdout)
        assert persisted_report == redact_report(report)

    expected_reports = tuple(report for _path, report in fixture_reports)
    repository = SQLiteReportRepository(database)
    client = TestClient(create_app(repository))
    listing = client.get("/runs")
    assert listing.status_code == 200
    for report in expected_reports:
        assert repository.get(report.run_id) == redact_report(report)
        detail = client.get(f"/runs/{report.run_id}")
        assert detail.status_code == 200
        assert report.verdict.value in detail.text
        assert "Expected · Executed · Recorded" in detail.text
    repository.close()
