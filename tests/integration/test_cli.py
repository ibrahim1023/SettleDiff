from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from pydantic import JsonValue
from typer.testing import CliRunner

from settlediff.application.replay import replay_fixture
from settlediff.cli import app
from settlediff.perflo.parser import PerfloSuccessEnvelope
from settlediff.storage.sqlite import SQLiteReportRepository

runner = CliRunner()


def test_fixture_replay_requires_no_live_configuration() -> None:
    result = runner.invoke(app, ["verify-fixture", "fixtures/paid-failure", "--json"])
    assert result.exit_code == 0
    assert '"verdict":"PAID_FAILURE"' in result.stdout


def test_fixture_replay_can_persist_for_show(tmp_path: Path) -> None:
    database = tmp_path / "reports.sqlite3"
    replay = runner.invoke(
        app, ["verify-fixture", "fixtures/clean-success", "--database", str(database)]
    )
    assert replay.exit_code == 0
    report = replay_fixture(Path("fixtures/clean-success"))
    shown = runner.invoke(app, ["show", report.run_id, "--database", str(database), "--json"])
    assert shown.exit_code == 0
    assert '"run_id":"syn_run_clean"' in shown.stdout


def test_live_run_rejects_invalid_json_before_any_adapter_call() -> None:
    result = runner.invoke(
        app, ["run", "--url", "https://example.invalid", "--body", "no", "--budget", "1"]
    )
    assert result.exit_code == 2
    assert "Invalid live preflight" in result.stderr


def test_live_run_decline_does_not_execute_a_paid_call(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    class FakePerflo:
        async def inspect_service(self, _target: str) -> PerfloSuccessEnvelope:
            calls.append("check")
            return _envelope(
                {
                    "vendor_slug": "synthetic-search",
                    "url": "https://example.invalid/search",
                    "price": {"amount": "0.01", "unit": "USDC"},
                    "asset": "USDC",
                    "protocol": "mpp",
                    "chain": "tempo",
                    "request_schema": {},
                }
            )

        async def get_schema(self, _slug: str) -> PerfloSuccessEnvelope:
            calls.append("schema")
            return _envelope({"request_schema": {}})

        async def execute(self, *_args: object) -> PerfloSuccessEnvelope:
            calls.append("fetch")
            raise AssertionError("must not execute when authorization is declined")

        async def get_activity(self) -> PerfloSuccessEnvelope:
            raise AssertionError("must not read activity when authorization is declined")

    monkeypatch.setattr("settlediff.cli.PerfloClient", FakePerflo)
    result = runner.invoke(
        app,
        ["run", "--url", "https://example.invalid/search", "--body", "{}", "--budget", "0.01"],
        input="n\n",
    )
    assert result.exit_code == 1
    assert calls == ["check", "schema"]
    assert "Body digest:" in result.stdout


def _envelope(result: JsonValue) -> PerfloSuccessEnvelope:
    return PerfloSuccessEnvelope(
        ok=True,
        payload={"ok": True, "result": result},
        stdout_bytes=0,
        stderr_bytes=0,
        returncode=0,
    )


def test_show_renders_persisted_report(tmp_path: Path) -> None:
    repository = SQLiteReportRepository(tmp_path / "reports.sqlite3")
    report = replay_fixture(Path("fixtures/clean-success"))
    repository.save(report)
    repository.close()
    result = runner.invoke(
        app, ["show", report.run_id, "--database", str(tmp_path / "reports.sqlite3")]
    )
    assert result.exit_code == 0
    assert "VERIFIED" in result.stdout


def test_serve_is_loopback_only(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    def fake_run(_app: FastAPI, **kwargs: object) -> None:
        captured.update(kwargs)

    monkeypatch.setattr("settlediff.cli.uvicorn.run", fake_run)
    database = tmp_path / "reports.sqlite3"
    SQLiteReportRepository(database).close()
    result = runner.invoke(app, ["serve", "--database", str(database)])
    assert result.exit_code == 0
    assert captured["host"] == "127.0.0.1"


def test_serve_accepts_a_local_alternate_port(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    captured: dict[str, object] = {}

    def fake_run(_app: FastAPI, **kwargs: object) -> None:
        captured.update(kwargs)

    monkeypatch.setattr("settlediff.cli.uvicorn.run", fake_run)
    database = tmp_path / "reports.sqlite3"
    SQLiteReportRepository(database).close()
    result = runner.invoke(app, ["serve", "--database", str(database), "--port", "8766"])
    assert result.exit_code == 0
    assert captured == {"host": "127.0.0.1", "port": 8766}
