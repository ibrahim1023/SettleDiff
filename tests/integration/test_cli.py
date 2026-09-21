from __future__ import annotations

import asyncio
import json
import sqlite3
import sys
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import cast

import pytest
from fastapi import FastAPI
from pydantic import JsonValue, SecretStr
from typer.testing import CliRunner

from settlediff import __version__
from settlediff.agent.grounding import fallback_explanation
from settlediff.application.auth import PaidExecutionCapability, PaidExecutionRequest
from settlediff.application.payment_rails import AdapterEvidence
from settlediff.application.replay import replay_fixture
from settlediff.application.run import (
    InvestigationOutcome,
    RunEvent,
    RunFailure,
    RunProvenance,
    RunState,
)
from settlediff.cli import (
    PaymentRail,
    _build_payment_adapter,  # pyright: ignore[reportPrivateUsage]
    app,
)
from settlediff.config import Settings
from settlediff.contextdev.client import ContextDevClient
from settlediff.domain.models import (
    ArtifactType,
    EvidenceArtifact,
    ExplanationRecord,
    ExplanationSource,
)
from settlediff.domain.money import Money
from settlediff.perflo.adapter import PerfloAdapter, PerfloClientPort
from settlediff.perflo.parser import PerfloSuccessEnvelope
from settlediff.storage.sqlite import SQLiteReportRepository
from settlediff.x402.adapter import X402Adapter

runner = CliRunner()


def isolated_settings() -> Settings:
    """Settings without the developer's local .env values."""
    return Settings(_env_file=None)  # pyright: ignore[reportCallIssue]


def available_executable(_command: str) -> str:
    return "/usr/bin/synthetic-executable"


def live_settings() -> Settings:
    return Settings(
        _env_file=None,  # pyright: ignore[reportCallIssue]
        contextdev_api_key=SecretStr("syn-contextdev-key"),
    )


def x402_live_settings(*, enabled: bool = True) -> Settings:
    return Settings(
        _env_file=None,  # pyright: ignore[reportCallIssue]
        contextdev_api_key=SecretStr("syn-contextdev-key"),
        x402_signer_command=(sys.executable,),
        x402_rpc_url="https://rpc.example.invalid",
        x402_testnet_enabled=enabled,
    )


class DeclineX402Adapter:
    adapter_id = "x402"

    def __init__(self, requests: list[PaidExecutionRequest]) -> None:
        self.requests = requests

    async def inspect(self, request: PaidExecutionRequest) -> AdapterEvidence:
        self.requests.append(request)
        return AdapterEvidence(
            adapter_id=self.adapter_id,
            protocol_version="2",
            operation="inspect",
            source="x402.synthetic.challenge",
            artifact_type=ArtifactType.SERVICE_CONTRACT,
            data={
                "schema_version": 2,
                "vendor_slug": None,
                "url": request.target,
                "price": {"amount": "0.001", "unit": "USDC"},
                "asset": "USDC",
                "protocol": "x402",
                "chain": None,
                "request_schema": {"type": "null" if request.body is None else "object"},
                "scheme": "exact",
                "network": "eip155:84532",
                "asset_identity": {
                    "schema_version": 1,
                    "symbol": "USDC",
                    "network": "eip155:84532",
                    "reference": "syn_usdc_base_sepolia",
                    "decimals": 6,
                },
                "recipient": "syn_x402_recipient",
                "max_timeout_seconds": 300,
                "normalization_notes": [],
            },
        )

    async def execute_once(self, *_args: object) -> AdapterEvidence:
        raise AssertionError("declined x402 authorization must not execute")

    async def collect_activity(self) -> AdapterEvidence:
        raise AssertionError("declined x402 authorization must not collect activity")


def test_version_option_reports_package_version_without_running_a_command() -> None:
    result = runner.invoke(app, ["--version"])

    assert result.exit_code == 0
    assert result.stdout == f"settlediff {__version__}\n"


def test_doctor_checks_perflo_and_database_without_paid_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("settlediff.cli.Settings", live_settings)
    monkeypatch.setattr("settlediff.cli.shutil.which", available_executable)

    result = runner.invoke(
        app,
        ["doctor", "--rail", "perflo", "--database", str(tmp_path / "reports.sqlite3")],
    )

    assert result.exit_code == 0
    assert "Database: writable" in result.stdout
    assert "Context.dev: configured" in result.stdout
    assert "Perflo: executable available" in result.stdout


def test_live_run_rejects_missing_perflo_executable_before_preflight(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakePerflo:
        def __getattr__(self, name: str) -> object:
            raise AssertionError(f"Perflo must not be called without its executable: {name}")

    def missing_executable(_command: str) -> None:
        return None

    monkeypatch.setattr("settlediff.cli.Settings", live_settings)
    monkeypatch.setattr("settlediff.cli.PerfloClient", FakePerflo)
    monkeypatch.setattr("settlediff.cli.shutil.which", missing_executable)

    result = runner.invoke(
        app,
        ["run", "--url", "https://example.invalid", "--body", "{}", "--budget", "1"],
    )

    assert result.exit_code == 2
    assert "Perflo executable is unavailable" in result.stderr


def test_doctor_reports_x402_schema_payer_and_chain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def doctor_x402(_settings: Settings) -> tuple[str, str]:
        return "0x14a34", "0x3333…3333"

    monkeypatch.setattr("settlediff.cli.Settings", x402_live_settings)
    monkeypatch.setattr("settlediff.cli._doctor_x402", doctor_x402)

    result = runner.invoke(
        app,
        ["doctor", "--rail", "x402", "--database", str(tmp_path / "reports.sqlite3")],
    )

    assert result.exit_code == 0
    assert "Signer schema: 3" in result.stdout
    assert "Signer payer: 0x3333…3333" in result.stdout
    assert "RPC chain: 0x14a34 (Base Sepolia)" in result.stdout


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


def test_show_renders_x402_adapter_and_separate_settlement_evidence(tmp_path: Path) -> None:
    database = tmp_path / "reports.sqlite3"
    report = replay_fixture(Path("fixtures/x402-clean-success")).model_copy(
        update={"adapter_id": "x402"}
    )
    repository = SQLiteReportRepository(database)
    repository.save(report)
    repository.close()

    json_result = runner.invoke(app, ["show", report.run_id, "--database", str(database), "--json"])
    human_result = runner.invoke(app, ["show", report.run_id, "--database", str(database)])

    assert json_result.exit_code == 0
    payload = json.loads(json_result.stdout)
    assert payload["adapter_id"] == "x402"
    assert payload["receipt"]["settlement_status"] == "settled"
    assert payload["ledger"]["status"] == "confirmed"
    assert human_result.exit_code == 0
    assert "Payment rail: x402" in human_result.stdout


def test_perflo_default_still_rejects_loopback_http() -> None:
    result = runner.invoke(
        app,
        [
            "run",
            "--url",
            "http://127.0.0.1:4021/weather",
            "--body",
            "{}",
            "--budget",
            "1",
        ],
    )

    assert result.exit_code == 2
    assert "except loopback HTTP for x402" in result.stderr


def test_live_run_rejects_invalid_json_before_any_adapter_call() -> None:
    result = runner.invoke(
        app, ["run", "--url", "https://example.invalid", "--body", "no", "--budget", "1"]
    )
    assert result.exit_code == 2
    assert "Invalid live preflight" in result.stderr


def test_x402_composition_builds_adapter_without_contacting_external_systems() -> None:
    adapter, close = _build_payment_adapter(PaymentRail.X402, x402_live_settings())

    assert isinstance(adapter, X402Adapter)
    assert close is not None

    async def close_adapter() -> None:
        await close()

    asyncio.run(close_adapter())


def test_live_run_rejects_unknown_payment_rail() -> None:
    result = runner.invoke(
        app,
        [
            "run",
            "--rail",
            "unknown",
            "--url",
            "https://example.invalid",
            "--body",
            "{}",
            "--budget",
            "1",
        ],
    )

    assert result.exit_code == 2
    assert "Invalid value" in result.stderr


def test_x402_run_requires_complete_non_secret_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("settlediff.cli.Settings", live_settings)

    result = runner.invoke(
        app,
        [
            "run",
            "--rail",
            "x402",
            "--allow-testnet",
            "--url",
            "https://example.invalid/paid",
            "--body",
            "{}",
            "--budget",
            "0.01",
        ],
    )

    assert result.exit_code == 2
    assert "x402 configuration is incomplete" in result.stderr


@pytest.mark.parametrize(("enabled", "allow_flag"), [(False, True), (True, False)])
def test_x402_run_requires_environment_and_cli_testnet_gates(
    monkeypatch: pytest.MonkeyPatch, enabled: bool, allow_flag: bool
) -> None:
    monkeypatch.setattr("settlediff.cli.Settings", lambda: x402_live_settings(enabled=enabled))
    arguments = [
        "run",
        "--rail",
        "x402",
        "--url",
        "https://example.invalid/paid",
        "--body",
        "{}",
        "--budget",
        "0.01",
    ]
    if allow_flag:
        arguments.append("--allow-testnet")

    result = runner.invoke(app, arguments)

    assert result.exit_code == 2
    assert "x402 testnet execution requires" in result.stderr


def test_x402_get_decline_preserves_method_and_absent_body_without_execution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[PaidExecutionRequest] = []
    adapter = DeclineX402Adapter(requests)
    monkeypatch.setattr("settlediff.cli.Settings", x402_live_settings)

    def build_adapter(_rail: PaymentRail, _settings: Settings) -> tuple[DeclineX402Adapter, None]:
        return adapter, None

    monkeypatch.setattr("settlediff.cli._build_payment_adapter", build_adapter)

    result = runner.invoke(
        app,
        [
            "run",
            "--rail",
            "x402",
            "--allow-testnet",
            "--method",
            "GET",
            "--url",
            "http://127.0.0.1:4021/weather",
            "--budget",
            "0.01",
        ],
        input="n\n",
    )

    assert result.exit_code == 1
    assert len(requests) == 1
    assert requests[0].method == "GET"
    assert requests[0].body is None
    assert "Rail: x402" in result.stdout
    assert "Version: 2" in result.stdout
    assert "Asset reference: syn_usdc_base_sepolia" in result.stdout
    assert "Recipient: syn_x402_recipient" in result.stdout
    assert "Maximum timeout: 300 seconds" in result.stdout
    assert "External signer: configured" in result.stdout
    assert "Authorization declined" in result.stdout


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
    monkeypatch.setattr("settlediff.cli.shutil.which", available_executable)
    result = runner.invoke(
        app,
        ["run", "--url", "https://example.invalid/search", "--body", "{}", "--budget", "0.01"],
        input="n\n",
    )
    assert result.exit_code == 1
    assert calls == ["check"]
    assert "Rail: perflo" in result.stdout
    assert "Version: unknown" in result.stdout
    assert "Scheme: unknown" in result.stdout
    assert "Network: tempo" in result.stdout
    assert "Method: POST" in result.stdout
    assert "Body digest:" in result.stdout
    assert "Payment terms digest:" in result.stdout
    assert "Quoted price: 0.01 USDC" in result.stdout
    assert "Investigation budget:" in result.stdout
    assert "Context.dev calls: 1" in result.stdout
    assert "model requests: 4" in result.stdout


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["transaction_hash", "transactionHash", "txHash"])
async def test_perflo_adapter_preserves_transaction_reference_alias(field: str) -> None:
    request = PaidExecutionRequest(
        run_id="syn_run",
        target="https://example.invalid",
        body={},
        budget=Money(amount=Decimal("0.01"), unit="USDC"),
    )
    authorization = await PaidExecutionCapability.issue(
        request, expires_at=datetime.now(UTC) + timedelta(minutes=1)
    ).consume(request)

    class FakePerflo:
        async def execute(self, *_args: object) -> PerfloSuccessEnvelope:
            return _envelope({field: "syn_hash_recovered"})

    adapter = PerfloAdapter(cast(PerfloClientPort, FakePerflo()))

    evidence = await adapter.execute_once(
        authorization, request, Money(amount=Decimal("0.01"), unit="USDC")
    )

    assert evidence.transaction_reference == "syn_hash_recovered"


@pytest.mark.asyncio
async def test_perflo_adapter_rejects_conflicting_transaction_references() -> None:
    request = PaidExecutionRequest(
        run_id="syn_run",
        target="https://example.invalid",
        body={},
        budget=Money(amount=Decimal("0.01"), unit="USDC"),
    )
    authorization = await PaidExecutionCapability.issue(
        request, expires_at=datetime.now(UTC) + timedelta(minutes=1)
    ).consume(request)

    class FakePerflo:
        async def execute(self, *_args: object) -> PerfloSuccessEnvelope:
            return _envelope({"transaction_hash": "syn_hash_one", "txHash": "syn_hash_two"})

    adapter = PerfloAdapter(cast(PerfloClientPort, FakePerflo()))

    with pytest.raises(ValueError, match="conflicting"):
        await adapter.execute_once(
            authorization, request, Money(amount=Decimal("0.01"), unit="USDC")
        )


def test_run_reports_unresolved_activity_recovery(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
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
    monkeypatch.setattr("settlediff.cli.shutil.which", available_executable)
    result = runner.invoke(
        app,
        [
            "run",
            "--url",
            "https://example.invalid/search",
            "--body",
            "{}",
            "--budget",
            "0.01",
            "--database",
            str(tmp_path / "reports.sqlite3"),
        ],
        input="y\n",
    )

    assert result.exit_code == 0
    assert calls == ["check", "fetch", "activity"]
    assert "UNVERIFIABLE" in result.stdout
    assert "Submission: unresolved" in result.stdout
    assert "proof of non-submission: no" in result.stdout
    repository = SQLiteReportRepository(tmp_path / "reports.sqlite3")
    records = repository.records()
    assert len(records) == 1
    assert records[0].report is not None
    assert records[0].latest_state is RunState.COMPLETE
    assert records[0].provenance.value == "external_live"
    assert repository.artifacts(records[0].run_id)
    repository.close()


def test_live_signer_launch_failure_remains_visible_without_traceback(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    requests: list[PaidExecutionRequest] = []

    class BrokenSignerAdapter(DeclineX402Adapter):
        async def execute_once(self, *_args: object) -> AdapterEvidence:
            raise PermissionError("syn-sensitive-launch-path")

    adapter = BrokenSignerAdapter(requests)
    monkeypatch.setattr("settlediff.cli.Settings", x402_live_settings)

    def build_adapter(_rail: PaymentRail, _settings: Settings) -> tuple[BrokenSignerAdapter, None]:
        return adapter, None

    monkeypatch.setattr("settlediff.cli._build_payment_adapter", build_adapter)
    database = tmp_path / "reports.sqlite3"

    result = runner.invoke(
        app,
        [
            "run",
            "--rail",
            "x402",
            "--allow-testnet",
            "--method",
            "GET",
            "--url",
            "https://example.invalid/paid",
            "--budget",
            "0.001",
            "--database",
            str(database),
        ],
        input="y\n",
    )

    assert result.exit_code == 2
    assert "signer process could not start" in result.stderr
    assert "Traceback" not in result.output
    assert "syn-sensitive-launch-path" not in result.output
    repository = SQLiteReportRepository(database)
    records = repository.records()
    assert len(records) == 1
    assert records[0].latest_state is RunState.FAILED
    assert records[0].report is None
    assert records[0].failure is not None
    assert records[0].failure.error_class == "PermissionError"
    assert records[0].failure.submission_uncertain is False
    assert repository.artifacts(records[0].run_id)
    repository.close()


@pytest.mark.parametrize("failure_kind", ["sqlite", "timeline"])
def test_live_run_renders_in_memory_report_when_persistence_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure_kind: str
) -> None:
    report = replay_fixture(Path("fixtures/clean-success"))
    explanation = ExplanationRecord(
        explanation=fallback_explanation(report, set()),
        source=ExplanationSource.FALLBACK,
        tool_calls=0,
    )

    async def completed_run(*args: object, **kwargs: object) -> InvestigationOutcome:
        del kwargs
        contextdev = cast(ContextDevClient, args[2])
        await contextdev.aclose()
        return InvestigationOutcome(
            report=report,
            explanation=explanation,
            recovery=None,
            events=(),
            submission_uncertain=False,
        )

    class FailingRepository:
        def __init__(self, path: Path) -> None:
            assert path == tmp_path / "reports.sqlite3"

        def begin_run(self, *_args: object, **_kwargs: object) -> None:
            pass

        def save_artifacts(self, *_args: object, **_kwargs: object) -> None:
            pass

        def finalize_run(self, *_args: object, **_kwargs: object) -> None:
            if failure_kind == "sqlite":
                raise sqlite3.OperationalError("syn-sensitive-storage-detail")

        def close(self) -> None:
            pass

    monkeypatch.setattr("settlediff.cli.Settings", live_settings)
    monkeypatch.setattr("settlediff.cli.shutil.which", available_executable)
    monkeypatch.setattr("settlediff.cli._execute_live_run", completed_run)
    monkeypatch.setattr("settlediff.cli.SQLiteReportRepository", FailingRepository)
    if failure_kind == "timeline":

        def unbounded_source(*_args: object, **_kwargs: object) -> object:
            raise ValueError("syn-sensitive-timeline-detail")

        monkeypatch.setattr("settlediff.cli.build_evidence_timeline", unbounded_source)

    result = runner.invoke(
        app,
        [
            "run",
            "--url",
            "https://example.invalid/search",
            "--body",
            "{}",
            "--budget",
            "0.01",
            "--database",
            str(tmp_path / "reports.sqlite3"),
            "--json",
        ],
    )

    assert result.exit_code == 2
    assert '"verdict":"VERIFIED"' in result.stdout
    assert "durable run remains available" in result.stderr
    assert "syn-sensitive-storage-detail" not in result.stderr
    assert "syn-sensitive-timeline-detail" not in result.stderr
    assert "Traceback" not in result.output


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


def test_inspect_and_recover_use_persisted_evidence_only(tmp_path: Path) -> None:
    database = tmp_path / "reports.sqlite3"
    report = replay_fixture(Path("fixtures/x402-provider-success-independent-failure"))
    recovery = EvidenceArtifact(
        artifact_id=f"{report.run_id}:recovery",
        artifact_type=ArtifactType.PAYMENT_RECEIPT,
        source="x402.transaction",
        collected_at=datetime(2026, 9, 3, tzinfo=UTC),
        redacted=True,
        data={"status": "failed"},
    )
    repository = SQLiteReportRepository(database)
    repository.save(report, artifacts=(recovery,))
    repository.close()

    inspected = runner.invoke(
        app, ["inspect", report.run_id, "--database", str(database), "--json"]
    )
    recovered = runner.invoke(
        app, ["recover", report.run_id, "--database", str(database), "--json"]
    )

    assert inspected.exit_code == 0
    inspected_payload = json.loads(inspected.stdout)
    assert inspected_payload["state"] == "complete"
    assert inspected_payload["provenance"] == "fixture"
    assert inspected_payload["artifact_ids"] == [recovery.artifact_id]
    assert recovered.exit_code == 0
    recovered_payload = json.loads(recovered.stdout)
    assert recovered_payload == {
        "run_id": report.run_id,
        "recovery_state": "submitted",
        "proof_of_non_submission": False,
        "evidence_ids": [recovery.artifact_id],
        "external_calls": 0,
        "paid_calls": 0,
    }


def _run_snapshot(
    database: Path, run_id: str
) -> tuple[object, tuple[object, ...], tuple[object, ...], tuple[object, ...]]:
    repository = SQLiteReportRepository(database)
    try:
        return (
            repository.record(run_id),
            repository.artifacts(run_id),
            repository.events(run_id),
            repository.timeline(run_id),
        )
    finally:
        repository.close()


def _forbid_live_composition(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("retry-analysis must not build live composition")

    monkeypatch.setattr("settlediff.cli.Settings", forbidden)
    monkeypatch.setattr("settlediff.cli._build_payment_adapter", forbidden)


def test_retry_analysis_uses_persisted_evidence_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = tmp_path / "reports.sqlite3"
    report = replay_fixture(Path("fixtures/x402-provider-success-independent-failure"))
    recovery = EvidenceArtifact(
        artifact_id=f"{report.run_id}:recovery",
        artifact_type=ArtifactType.PAYMENT_RECEIPT,
        source="x402.transaction",
        collected_at=datetime(2026, 9, 3, tzinfo=UTC),
        redacted=True,
        data={"status": "failed"},
    )
    repository = SQLiteReportRepository(database)
    repository.save(report, artifacts=(recovery,))
    repository.close()
    _forbid_live_composition(monkeypatch)
    before = _run_snapshot(database, report.run_id)

    result = runner.invoke(
        app, ["retry-analysis", report.run_id, "--database", str(database), "--json"]
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload == {
        "run_id": report.run_id,
        "safety": "DO_NOT_RETRY",
        "reason_codes": ["REVERTED_RECEIPT", "PROVIDER_PAYMENT_ATTEMPT"],
        "evidence_ids": sorted([recovery.artifact_id, f"{report.run_id}:report"]),
        "external_calls": 0,
        "paid_calls": 0,
    }
    assert _run_snapshot(database, report.run_id) == before


def test_retry_analysis_text_output_for_refused_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = tmp_path / "reports.sqlite3"
    repository = SQLiteReportRepository(database)
    repository.begin_run(
        "syn_refused",
        task="refused synthetic run",
        provenance=RunProvenance.EXTERNAL_LIVE,
        created_at=datetime(2026, 9, 3, tzinfo=UTC),
    )
    repository.append_event(
        "syn_refused",
        RunEvent(state=RunState.REFUSED, occurred_at=datetime(2026, 9, 3, tzinfo=UTC)),
    )
    repository.close()
    _forbid_live_composition(monkeypatch)

    result = runner.invoke(app, ["retry-analysis", "syn_refused", "--database", str(database)])

    assert result.exit_code == 0
    assert "Run: syn_refused" in result.stdout
    assert "Retry safety: SAFE_TO_RETRY" in result.stdout
    assert "Reasons: RUN_REFUSED" in result.stdout
    assert "Evidence: syn_refused:run_state" in result.stdout
    assert "External calls: 0" in result.stdout
    assert "Paid calls: 0" in result.stdout


def test_retry_analysis_failed_uncertain_run_requires_human(tmp_path: Path) -> None:
    database = tmp_path / "reports.sqlite3"
    repository = SQLiteReportRepository(database)
    repository.begin_run(
        "syn_uncertain",
        task="uncertain synthetic run",
        provenance=RunProvenance.EXTERNAL_LIVE,
        created_at=datetime(2026, 9, 3, tzinfo=UTC),
    )
    repository.append_event(
        "syn_uncertain",
        RunEvent(state=RunState.EXECUTING, occurred_at=datetime(2026, 9, 3, tzinfo=UTC)),
    )
    repository.record_failure(
        "syn_uncertain",
        RunFailure(
            stage=RunState.EXECUTING,
            error_class="SubmissionUncertainError",
            diagnostic="executing failed",
            submission_uncertain=True,
            occurred_at=datetime(2026, 9, 3, tzinfo=UTC),
        ),
    )
    repository.close()

    result = runner.invoke(
        app, ["retry-analysis", "syn_uncertain", "--database", str(database), "--json"]
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["safety"] == "REQUIRES_HUMAN_DECISION"
    assert payload["reason_codes"] == ["SUBMISSION_UNCERTAIN"]
    assert payload["evidence_ids"] == ["syn_uncertain:run_state"]
    assert payload["external_calls"] == 0
    assert payload["paid_calls"] == 0


def test_retry_analysis_missing_run_exits_1(tmp_path: Path) -> None:
    database = tmp_path / "reports.sqlite3"
    SQLiteReportRepository(database).close()

    result = runner.invoke(app, ["retry-analysis", "syn_missing", "--database", str(database)])

    assert result.exit_code == 1
    assert "was not found" in result.stderr


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
    assert "authenticity is not established" in verified.stdout


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


def test_snapshot_persists_contract_without_signing_or_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    contract: dict[str, JsonValue] = {
        "vendor_slug": "synthetic-search",
        "url": "https://example.invalid/search",
        "price": {"amount": "0.01", "unit": "USDC"},
        "asset": "USDC",
        "protocol": "mpp",
        "chain": "tempo",
    }

    class FakePerflo:
        async def inspect_service(self, target: str) -> PerfloSuccessEnvelope:
            assert target == "https://example.invalid/search"
            return _envelope(contract)

        async def execute(self, *_args: object) -> PerfloSuccessEnvelope:
            raise AssertionError("snapshot must not execute")

    def forbidden_settings(*_args: object, **_kwargs: object) -> Settings:
        raise AssertionError("snapshot must not build live settings")

    monkeypatch.setattr("settlediff.cli.PerfloClient", FakePerflo)
    monkeypatch.setattr("settlediff.cli.Settings", forbidden_settings)
    database = tmp_path / "reports.sqlite3"

    result = runner.invoke(
        app,
        [
            "snapshot",
            "https://example.invalid/search",
            "--database",
            str(database),
            "--json",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["external_calls"] == 1
    assert payload["paid_calls"] == 0
    assert len(payload["snapshot_digest"]) == 64
    repository = SQLiteReportRepository(database)
    try:
        stored = repository.contract_snapshots("https://example.invalid/search", "perflo")
    finally:
        repository.close()
    assert [item.snapshot_digest for item in stored] == [payload["snapshot_digest"]]


def test_drift_reports_unavailable_then_match_then_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    current_contract: dict[str, JsonValue] = {
        "vendor_slug": "synthetic-search",
        "url": "https://example.invalid/search",
        "price": {"amount": "0.01", "unit": "USDC"},
        "asset": "USDC",
        "protocol": "mpp",
        "chain": "tempo",
    }
    calls: list[dict[str, JsonValue]] = [current_contract]

    class FakePerflo:
        async def inspect_service(self, _target: str) -> PerfloSuccessEnvelope:
            return _envelope(calls[0])

    monkeypatch.setattr("settlediff.cli.PerfloClient", FakePerflo)
    database = tmp_path / "reports.sqlite3"
    argv = [
        "drift",
        "https://example.invalid/search",
        "--database",
        str(database),
        "--json",
    ]

    first = json.loads(runner.invoke(app, argv).stdout)
    assert first["status"] == "UNAVAILABLE"
    assert first["previous_snapshot_digest"] is None
    assert first["external_calls"] == 1 and first["paid_calls"] == 0

    second = json.loads(runner.invoke(app, argv).stdout)
    assert second["status"] == "MATCH"
    assert second["previous_snapshot_digest"] == first["current_snapshot_digest"]

    calls[0] = dict(current_contract, price={"amount": "0.02", "unit": "USDC"})
    third = json.loads(runner.invoke(app, argv).stdout)
    assert third["status"] == "DIFF"
    assert "PRICE_CHANGED" in third["change_codes"]
    assert third["previous_snapshot_digest"] == second["current_snapshot_digest"]


def test_x402_snapshot_uses_inspection_only_adapter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import base64 as _base64
    import json as _json

    target = "https://example.invalid/paid"
    payload = _json.loads(Path("tests/contract/x402/fixtures/payment-required-v2.json").read_text())
    payload["resource"]["url"] = target
    header = _base64.b64encode(_json.dumps(payload, separators=(",", ":")).encode()).decode()

    class FakeResourceClient:
        def __init__(self, _client: object) -> None:
            self.closed = False

        async def challenge(self, _request: object) -> object:
            from settlediff.x402.http import X402ResourceResponse

            return X402ResourceResponse(
                status_code=402,
                payment_required=header,
                body=None,
                observed_at=datetime(2026, 9, 10, tzinfo=UTC),
            )

    def forbidden_settings(*_args: object, **_kwargs: object) -> Settings:
        raise AssertionError("x402 snapshot must not build live settings")

    monkeypatch.setattr("settlediff.cli.X402ResourceClient", FakeResourceClient)
    monkeypatch.setattr("settlediff.cli.Settings", forbidden_settings)
    database = tmp_path / "reports.sqlite3"

    result = runner.invoke(
        app, ["snapshot", target, "--rail", "x402", "--database", str(database), "--json"]
    )

    assert result.exit_code == 0
    out = json.loads(result.stdout)
    assert out["paid_calls"] == 0 and out["external_calls"] == 1
    repository = SQLiteReportRepository(database)
    try:
        stored = repository.contract_snapshots(target, "x402")
    finally:
        repository.close()
    assert len(stored) == 1
    assert stored[0].rail == "x402"


def _bazaar_header(payload: dict[str, JsonValue]) -> str:
    import base64 as _base64

    return _base64.b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode()


def _bazaar_payload(**edits: object) -> dict[str, JsonValue]:
    payload: dict[str, JsonValue] = json.loads(
        Path("tests/contract/x402/fixtures/payment-required-bazaar-v2.json").read_text()
    )
    for key, value in edits.items():
        payload[key] = cast(JsonValue, value)
    return payload


def test_bazaar_check_observes_challenge_without_paid_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    header = _bazaar_header(_bazaar_payload())

    class FakeResourceClient:
        def __init__(self, _client: object) -> None:
            self.calls: list[object] = []

        async def challenge(self, request: object) -> object:
            self.calls.append(request)
            from settlediff.x402.http import X402ResourceResponse

            return X402ResourceResponse(
                status_code=402,
                payment_required=header,
                body=None,
                observed_at=datetime(2026, 9, 10, tzinfo=UTC),
            )

    def forbidden_settings(*_args: object, **_kwargs: object) -> Settings:
        raise AssertionError("bazaar-check must not build live settings")

    def forbidden_adapter(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("bazaar-check must not build adapters")

    monkeypatch.setattr("settlediff.cli.X402ResourceClient", FakeResourceClient)
    monkeypatch.setattr("settlediff.cli.Settings", forbidden_settings)
    monkeypatch.setattr("settlediff.cli.X402Adapter", forbidden_adapter)
    database = tmp_path / "reports.sqlite3"
    SQLiteReportRepository(database).close()
    before = database.read_bytes()

    result = runner.invoke(
        app,
        [
            "bazaar-check",
            "https://example.invalid/weather",
            "--database",
            str(database),
            "--json",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "MATCH"
    assert payload["external_calls"] == 1
    assert payload["paid_calls"] == 0
    assert payload["run_id"] is None
    checks = {check["check_id"]: check["status"] for check in payload["checks"]}
    assert checks["BAZAAR_EXTENSION"] == "MATCH"
    assert checks["PAID_EVIDENCE"] == "UNAVAILABLE"
    assert database.read_bytes() == before


def test_bazaar_check_live_mainnet_shape_reports_declaration_match(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = json.loads(
        Path(
            "tests/contract/x402/fixtures/payment-required-bazaar-live-mainnet-2026-09-21.json"
        ).read_text()
    )
    header = _bazaar_header(payload)

    class FakeResourceClient:
        def __init__(self, _client: object) -> None:
            pass

        async def challenge(self, _request: object) -> object:
            from settlediff.x402.http import X402ResourceResponse

            return X402ResourceResponse(
                status_code=402,
                payment_required=header,
                body=None,
                observed_at=datetime(2026, 9, 21, tzinfo=UTC),
            )

    monkeypatch.setattr("settlediff.cli.X402ResourceClient", FakeResourceClient)
    database = tmp_path / "reports.sqlite3"
    SQLiteReportRepository(database).close()

    result = runner.invoke(
        app,
        [
            "bazaar-check",
            "https://x402-paid-endpoint.selfradiance.workers.dev/artifact/vq00.json",
            "--database",
            str(database),
            "--json",
        ],
    )
    assert result.exit_code == 0
    output = json.loads(result.stdout)
    assert output["status"] == "UNSUPPORTED"
    checks = {check["check_id"]: check["status"] for check in output["checks"]}
    assert checks["DECLARATION_SCHEMA"] == "MATCH"
    assert checks["INPUT_METHOD"] == "MATCH"
    assert checks["MEDIA_TYPE"] == "MATCH"
    assert checks["PRIMARY_REQUIREMENT"] == "UNSUPPORTED"


def test_bazaar_check_flags_unsupported_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    header = _bazaar_header(_bazaar_payload(x402Version=7))

    class FakeResourceClient:
        def __init__(self, _client: object) -> None:
            pass

        async def challenge(self, _request: object) -> object:
            from settlediff.x402.http import X402ResourceResponse

            return X402ResourceResponse(
                status_code=402,
                payment_required=header,
                body=None,
                observed_at=datetime(2026, 9, 10, tzinfo=UTC),
            )

    monkeypatch.setattr("settlediff.cli.X402ResourceClient", FakeResourceClient)
    database = tmp_path / "reports.sqlite3"
    SQLiteReportRepository(database).close()

    result = runner.invoke(
        app,
        [
            "bazaar-check",
            "https://example.invalid/weather",
            "--database",
            str(database),
            "--json",
        ],
    )
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "UNSUPPORTED"
    assert [check["check_id"] for check in payload["checks"]] == ["VERSION"]


def test_bazaar_check_missing_run_never_observes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[object] = []

    class FakeResourceClient:
        def __init__(self, _client: object) -> None:
            pass

        async def challenge(self, request: object) -> object:
            calls.append(request)
            raise AssertionError("missing run must not reach the network")

    monkeypatch.setattr("settlediff.cli.X402ResourceClient", FakeResourceClient)
    database = tmp_path / "reports.sqlite3"
    SQLiteReportRepository(database).close()

    result = runner.invoke(
        app,
        [
            "bazaar-check",
            "https://example.invalid/weather",
            "--database",
            str(database),
            "--run-id",
            "run_missing",
        ],
    )
    assert result.exit_code == 1
    assert calls == []


def test_bazaar_check_compares_persisted_paid_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from settlediff.x402.models import PaymentRequired
    from settlediff.x402.normalize import normalize_payment_required

    paid_contract = normalize_payment_required(
        PaymentRequired.model_validate(_bazaar_payload()),
        request_schema={"method": "GET", "body": None},
    )
    report = replay_fixture(Path("fixtures/x402-clean-success")).model_copy(
        update={"adapter_id": "x402", "contract": paid_contract}
    )
    database = tmp_path / "reports.sqlite3"
    repository = SQLiteReportRepository(database)
    repository.save(report)
    repository.close()
    before = database.read_bytes()

    header = _bazaar_header(_bazaar_payload())

    class FakeResourceClient:
        def __init__(self, _client: object) -> None:
            pass

        async def challenge(self, _request: object) -> object:
            from settlediff.x402.http import X402ResourceResponse

            return X402ResourceResponse(
                status_code=402,
                payment_required=header,
                body=None,
                observed_at=datetime(2026, 9, 10, tzinfo=UTC),
            )

    monkeypatch.setattr("settlediff.cli.X402ResourceClient", FakeResourceClient)

    result = runner.invoke(
        app,
        [
            "bazaar-check",
            "https://example.invalid/weather",
            "--database",
            str(database),
            "--run-id",
            report.run_id,
            "--json",
        ],
    )
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["run_id"] == report.run_id
    checks = {check["check_id"]: check["status"] for check in payload["checks"]}
    assert checks["RESOURCE"] == "MATCH"
    assert checks["RECIPIENT"] == "UNAVAILABLE"
    assert checks["ASSET"] == "UNAVAILABLE"
    assert checks["PRICE"] in {"MATCH", "DIFF", "UNAVAILABLE"}
    assert "PAID_EVIDENCE" not in checks
    assert database.read_bytes() == before
    assert "PAID_EVIDENCE" not in checks
