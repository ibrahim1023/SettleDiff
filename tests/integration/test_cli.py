from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest
from fastapi import FastAPI
from pydantic import JsonValue, SecretStr
from typer.testing import CliRunner

from settlediff import __version__
from settlediff.agent.grounding import fallback_explanation
from settlediff.application.replay import replay_fixture
from settlediff.application.run import LiveEvidenceCollector, RunEvent, RunState
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


def test_version_option_reports_package_version_without_running_a_command() -> None:
    result = runner.invoke(app, ["--version"])

    assert result.exit_code == 0
    assert result.stdout == f"settlediff {__version__}\n"


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

        async def get_execution(self) -> PerfloSuccessEnvelope:
            raise AssertionError("must not read execution when authorization is declined")

        async def transaction_status(self, _hash: str) -> PerfloSuccessEnvelope:
            raise AssertionError("must not read transaction status when authorization is declined")

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
    assert "Investigation budget:" in result.stdout
    assert "Context.dev calls: 1" in result.stdout
    assert "model requests: 4" in result.stdout


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

        async def execute(
            self, authorization: object, request: object, quoted_price: object
        ) -> PerfloSuccessEnvelope:
            del authorization, request, quoted_price
            raise AssertionError

        async def get_activity(self) -> PerfloSuccessEnvelope:
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


def test_run_reports_submitted_activity_recovery(monkeypatch: pytest.MonkeyPatch) -> None:
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

        async def execute(
            self, authorization: object, request: object, quoted_price: object
        ) -> PerfloSuccessEnvelope:
            del authorization, request, quoted_price
            calls.append("fetch")
            from settlediff.perflo.client import PerfloMutationUncertainError

            raise PerfloMutationUncertainError("synthetic timeout")

        async def get_execution(self) -> PerfloSuccessEnvelope:
            raise AssertionError("Perflo 4.1 has no execution status command")

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
    assert calls == ["check", "schema", "fetch", "activity"]
    assert "UNVERIFIABLE" in result.stdout
    assert "Submission: submitted" in result.stdout
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
    assert "Usage: requests=0, tool_calls=0, input_tokens=0, output_tokens=0" in result.stdout


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


def _persisted_fixture_report(tmp_path: Path) -> tuple[Path, str]:
    """Persist the clean-success fixture (created 2026-08-12) and return (database, run_id)."""
    database = tmp_path / "reports.sqlite3"
    report = replay_fixture(Path("fixtures/clean-success"))
    repository = SQLiteReportRepository(database)
    repository.save(report)
    repository.close()
    return database, report.run_id


def _read_report(database: Path, run_id: str) -> object:
    repository = SQLiteReportRepository(database)
    try:
        return repository.get(run_id)
    finally:
        repository.close()


def test_delete_shows_run_details_and_cancels_without_deleting(tmp_path: Path) -> None:
    database, run_id = _persisted_fixture_report(tmp_path)

    result = runner.invoke(app, ["delete", run_id, "--database", str(database)], input="n\n")

    assert result.exit_code == 1
    assert run_id in result.stdout
    assert "VERIFIED" in result.stdout
    assert "2026-08-12" in result.stdout
    assert _read_report(database, run_id) is not None


def test_delete_confirmed_removes_the_run(tmp_path: Path) -> None:
    database, run_id = _persisted_fixture_report(tmp_path)

    result = runner.invoke(app, ["delete", run_id, "--database", str(database)], input="y\n")

    assert result.exit_code == 0
    assert _read_report(database, run_id) is None


def test_delete_yes_skips_confirmation(tmp_path: Path) -> None:
    database, run_id = _persisted_fixture_report(tmp_path)

    result = runner.invoke(app, ["delete", run_id, "--database", str(database), "--yes"])

    assert result.exit_code == 0
    assert _read_report(database, run_id) is None


def test_delete_missing_run_exits_1(tmp_path: Path) -> None:
    database, _run_id = _persisted_fixture_report(tmp_path)

    result = runner.invoke(app, ["delete", "syn_run_missing", "--database", str(database), "--yes"])

    assert result.exit_code == 1
    assert "syn_run_missing" in result.stderr
    assert "not found" in result.stderr


def test_delete_cascades_events_artifacts_and_explanations(tmp_path: Path) -> None:
    database = tmp_path / "reports.sqlite3"
    report = replay_fixture(Path("fixtures/clean-success"))
    artifact = EvidenceArtifact(
        artifact_id=f"{report.run_id}:execution",
        artifact_type=ArtifactType.EXECUTION,
        source="fixture",
        collected_at=datetime(2026, 8, 12, tzinfo=UTC),
        redacted=True,
        data={"transaction_hash": "syn_hash_cascade"},
    )
    events = (
        RunEvent(state=RunState.PREFLIGHT, occurred_at=datetime(2026, 8, 12, tzinfo=UTC)),
        RunEvent(state=RunState.COMPLETE, occurred_at=datetime(2026, 8, 12, tzinfo=UTC)),
    )
    record = ExplanationRecord(
        explanation=fallback_explanation(report, {artifact.artifact_id}),
        source=ExplanationSource.FALLBACK,
        tool_calls=0,
    )
    repository = SQLiteReportRepository(database)
    repository.save(report, events=events, artifacts=(artifact,), explanation=record)
    repository.close()

    result = runner.invoke(app, ["delete", report.run_id, "--database", str(database), "--yes"])
    assert result.exit_code == 0

    connection = sqlite3.connect(database)
    try:
        for table in ("reports", "run_events", "artifacts", "explanations"):
            row = connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
            assert row is not None and row[0] == 0, f"{table} still holds deleted run data"
    finally:
        connection.close()


@pytest.mark.parametrize("duration", ["later", "30", "1w", "0d", "0h", "-5d", "12H", "1.5d", ""])
def test_purge_rejects_invalid_durations_before_any_deletion(tmp_path: Path, duration: str) -> None:
    database, run_id = _persisted_fixture_report(tmp_path)

    result = runner.invoke(
        app, ["purge", "--database", str(database), "--older-than", duration, "--apply"]
    )

    assert result.exit_code == 2
    assert _read_report(database, run_id) is not None


def test_purge_dry_run_lists_runs_without_deleting(tmp_path: Path) -> None:
    database, run_id = _persisted_fixture_report(tmp_path)

    result = runner.invoke(app, ["purge", "--database", str(database), "--older-than", "1d"])

    assert result.exit_code == 0
    assert run_id in result.stdout
    assert "VERIFIED" in result.stdout
    assert _read_report(database, run_id) is not None


def test_purge_apply_deletes_only_runs_older_than_the_cutoff(tmp_path: Path) -> None:
    database, run_id = _persisted_fixture_report(tmp_path)
    recent = replay_fixture(Path("fixtures/clean-success")).model_copy(
        update={
            "run_id": "syn_run_recent",
            "intent": replay_fixture(Path("fixtures/clean-success")).intent.model_copy(
                update={"run_id": "syn_run_recent", "created_at": datetime.now(UTC)}
            ),
        }
    )
    repository = SQLiteReportRepository(database)
    repository.save(recent)
    repository.close()

    result = runner.invoke(
        app, ["purge", "--database", str(database), "--older-than", "1d", "--apply"]
    )

    assert result.exit_code == 0
    assert run_id in result.stdout
    assert "syn_run_recent" not in result.stdout
    assert _read_report(database, run_id) is None
    assert _read_report(database, "syn_run_recent") is not None


def test_purge_empty_result_is_a_clear_no_op(tmp_path: Path) -> None:
    database, _run_id = _persisted_fixture_report(tmp_path)

    result = runner.invoke(app, ["purge", "--database", str(database), "--older-than", "10000d"])

    assert result.exit_code == 0
    assert "no runs" in result.stdout.lower() or "nothing" in result.stdout.lower()


def test_export_and_verify_bundle_round_trip(tmp_path: Path) -> None:
    database = tmp_path / "reports.sqlite3"
    output = tmp_path / "run.bundle.json"
    report = replay_fixture(Path("fixtures/clean-success"))
    repository = SQLiteReportRepository(database)
    repository.save(report)
    repository.close()

    exported = runner.invoke(
        app,
        ["export", report.run_id, "--database", str(database), "--output", str(output)],
    )
    verified = runner.invoke(app, ["verify-bundle", str(output)])

    assert exported.exit_code == 0
    assert output.is_file()
    assert report.run_id in exported.stdout
    assert verified.exit_code == 0
    assert "VERIFIED" in verified.stdout
    assert report.run_id in verified.stdout


def test_export_refuses_to_overwrite_without_force(tmp_path: Path) -> None:
    database, run_id = _persisted_fixture_report(tmp_path)
    output = tmp_path / "run.bundle.json"
    output.write_text("existing")

    result = runner.invoke(
        app,
        ["export", run_id, "--database", str(database), "--output", str(output)],
    )

    assert result.exit_code == 2
    assert output.read_text() == "existing"


def test_export_missing_run_and_tampered_bundle_fail_cleanly(tmp_path: Path) -> None:
    database, run_id = _persisted_fixture_report(tmp_path)
    output = tmp_path / "run.bundle.json"
    missing = runner.invoke(
        app,
        ["export", "syn_missing", "--database", str(database), "--output", str(output)],
    )
    assert missing.exit_code == 1

    exported = runner.invoke(
        app,
        ["export", run_id, "--database", str(database), "--output", str(output)],
    )
    assert exported.exit_code == 0
    payload = json.loads(output.read_text())
    payload["integrity"] = "0" * 64
    output.write_text(json.dumps(payload))

    verified = runner.invoke(app, ["verify-bundle", str(output)])
    assert verified.exit_code == 2
    assert "integrity" in verified.stderr.lower()


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
