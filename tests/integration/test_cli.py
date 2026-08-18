from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest
from fastapi import FastAPI
from pydantic import JsonValue, SecretStr
from typer.testing import CliRunner

from settlediff.agent.grounding import fallback_explanation
from settlediff.application.replay import replay_fixture
from settlediff.application.run import LiveEvidenceCollector
from settlediff.cli import app
from settlediff.config import Settings
from settlediff.contextdev.client import ContextEvidencePort
from settlediff.domain.models import (
    ArtifactType,
    EvidenceArtifact,
    ExplanationRecord,
    ExplanationSource,
)
from settlediff.perflo.parser import PerfloSuccessEnvelope
from settlediff.storage.sqlite import SQLiteReportRepository

runner = CliRunner()


def isolated_settings() -> Settings:
    """Settings without the developer's local .env values."""
    return Settings(_env_file=None)  # pyright: ignore[reportCallIssue]


def live_settings() -> Settings:
    return Settings(
        _env_file=None,  # pyright: ignore[reportCallIssue]
        contextdev_api_key=SecretStr("syn-contextdev-key"),
    )


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


def test_live_run_requires_contextdev_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakePerflo:
        def __getattr__(self, name: str) -> object:
            raise AssertionError(f"Perflo must not be called without Context.dev: {name}")

    monkeypatch.setattr("settlediff.cli.Settings", isolated_settings)
    monkeypatch.setattr("settlediff.cli.PerfloClient", FakePerflo)
    result = runner.invoke(
        app, ["run", "--url", "https://example.invalid", "--body", "{}", "--budget", "1"]
    )
    assert result.exit_code == 2
    assert "Context.dev configuration is required for live investigations" in result.stderr


def test_live_run_decline_does_not_build_a_model(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def forbidden_model_factory(_settings: Settings) -> object:
        calls.append("model")
        raise AssertionError("model must not be constructed when authorization is declined")

    monkeypatch.setattr("settlediff.cli._build_model_if_configured", forbidden_model_factory)

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

    monkeypatch.setattr("settlediff.cli.Settings", live_settings)
    monkeypatch.setattr("settlediff.cli.PerfloClient", FakePerflo)
    result = runner.invoke(
        app,
        ["run", "--url", "https://example.invalid/search", "--body", "{}", "--budget", "0.01"],
        input="n\n",
    )
    assert result.exit_code == 1
    assert calls == ["check", "schema"]
    assert "Body digest:" in result.stdout


def test_transaction_handle_comes_only_from_captured_execution_evidence() -> None:
    artifact = EvidenceArtifact(
        artifact_id="run:execution",
        artifact_type=ArtifactType.EXECUTION,
        source="perflo.fetch",
        collected_at=datetime.now(UTC),
        redacted=False,
        data={"transaction_hash": "syn_hash_recovered"},
    )

    class FakePerflo:
        async def inspect_service(self, target: str) -> PerfloSuccessEnvelope:
            del target
            raise AssertionError

        async def get_schema(self, slug: str) -> PerfloSuccessEnvelope:
            del slug
            raise AssertionError

        async def execute(self, authorization: object, request: object) -> PerfloSuccessEnvelope:
            del authorization, request
            raise AssertionError

        async def get_activity(self) -> PerfloSuccessEnvelope:
            raise AssertionError

        async def get_execution(self) -> PerfloSuccessEnvelope:
            raise AssertionError

        async def transaction_status(self, transaction_hash: str) -> PerfloSuccessEnvelope:
            del transaction_hash
            raise AssertionError

    class FakeContextDev:
        async def verify(self, _request: object) -> object:
            raise AssertionError

    collector = LiveEvidenceCollector(FakePerflo(), cast(ContextEvidencePort, FakeContextDev()))
    collector._execution = artifact  # pyright: ignore[reportPrivateUsage]

    from settlediff.cli import _transaction_handle  # pyright: ignore[reportPrivateUsage]

    assert _transaction_handle(collector) == "syn_hash_recovered"


def test_run_reports_unresolved_submission_recovery(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    class FakePerflo:
        async def inspect_service(self, target: str) -> PerfloSuccessEnvelope:
            del target
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

        async def get_schema(self, slug: str) -> PerfloSuccessEnvelope:
            del slug
            calls.append("schema")
            return _envelope({"request_schema": {}})

        async def execute(self, authorization: object, request: object) -> PerfloSuccessEnvelope:
            del authorization, request
            calls.append("fetch")
            from pathlib import Path as FixturePath

            from settlediff.perflo.client import PerfloMutationUncertainError

            self._execution = json.loads(
                (FixturePath("fixtures/clean-success") / "execution.json").read_text()
            )
            raise PerfloMutationUncertainError("synthetic timeout")

        async def get_execution(self) -> PerfloSuccessEnvelope:
            return _envelope(self._execution)

        async def get_activity(self) -> PerfloSuccessEnvelope:
            calls.append("activity")
            from pathlib import Path as FixturePath

            return _envelope(
                json.loads((FixturePath("fixtures/clean-success") / "activity.json").read_text())
            )

        async def transaction_status(self, transaction_hash: str) -> PerfloSuccessEnvelope:
            del transaction_hash
            raise AssertionError("no transaction handle exists after the timeout")

    monkeypatch.setattr("settlediff.cli.Settings", live_settings)
    monkeypatch.setattr("settlediff.cli.PerfloClient", FakePerflo)
    result = runner.invoke(
        app,
        ["run", "--url", "https://example.invalid/search", "--body", "{}", "--budget", "0.01"],
        input="y\n",
    )

    assert result.exit_code == 0
    assert calls == ["check", "schema", "fetch", "activity", "activity"]
    assert "Submission: unresolved" in result.stdout
    assert "proof of non-submission: no" in result.stdout


def test_show_renders_persisted_explanation_without_recomputing(tmp_path: Path) -> None:
    report = replay_fixture(Path("fixtures/clean-success"))
    explanation = fallback_explanation(report, set())
    record = ExplanationRecord(
        explanation=explanation,
        source=ExplanationSource.FALLBACK,
        tool_calls=0,
    )
    repository = SQLiteReportRepository(tmp_path / "reports.sqlite3")
    repository.save(report, explanation=record)
    repository.close()

    result = runner.invoke(
        app, ["show", report.run_id, "--database", str(tmp_path / "reports.sqlite3")]
    )

    assert result.exit_code == 0
    assert "Explanation (fallback):" in result.stdout
    assert explanation.summary in result.stdout


def test_json_show_renders_persisted_explanation(tmp_path: Path) -> None:
    report = replay_fixture(Path("fixtures/clean-success"))
    record = ExplanationRecord(
        explanation=fallback_explanation(report, set()),
        source=ExplanationSource.FALLBACK,
        tool_calls=0,
    )
    repository = SQLiteReportRepository(tmp_path / "reports.sqlite3")
    repository.save(report, explanation=record)
    repository.close()

    result = runner.invoke(
        app, ["show", report.run_id, "--database", str(tmp_path / "reports.sqlite3"), "--json"]
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["report"]["verdict"] == "VERIFIED"
    assert payload["explanation"]["source"] == "fallback"
    assert payload["explanation"]["explanation"]["deterministic_verdict"] == "VERIFIED"


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
