"""Command-line interface for deterministic fixture verification and live preflight."""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import cast
from urllib.parse import urlparse
from uuid import uuid4

import typer
import uvicorn
from pydantic import JsonValue, ValidationError
from pydantic_ai.models import Model

from settlediff.agent.investigator import InvestigationState, investigate
from settlediff.agent.model import build_hyperfusion_model
from settlediff.agent.tools import build_investigation_dependencies
from settlediff.api.app import create_app
from settlediff.application.auth import PaidExecutionCapability, PaidExecutionRequest
from settlediff.application.replay import replay_fixture
from settlediff.application.run import (
    LiveEvidenceCollector,
    LiveRunCommand,
    RunInvestigation,
)
from settlediff.config import Settings
from settlediff.contextdev.client import ContextDevClient
from settlediff.domain.models import (
    ExplanationRecord,
    ExplanationSource,
    MachineReport,
)
from settlediff.domain.money import Money
from settlediff.perflo.client import PerfloClient, PerfloClientError
from settlediff.storage.sqlite import SQLiteReportRepository
from settlediff.telemetry.setup import configure_telemetry

app = typer.Typer(
    name="settlediff",
    help="Investigate agent purchases with deterministic verification.",
    no_args_is_help=True,
)
DATABASE_OPTION = typer.Option(..., "--database", exists=True, readable=True)
JSON_OPTION = typer.Option(False, "--json")
OPTIONAL_DATABASE_OPTION = typer.Option(None, "--database")


@app.callback()
def main() -> None:
    """Investigate agent purchases with deterministic verification."""


def _render(
    report: MachineReport, json_mode: bool, explanation: ExplanationRecord | None = None
) -> None:
    if json_mode:
        if explanation is None:
            typer.echo(report.model_dump_json())
            return
        payload = {
            "report": report.model_dump(mode="json"),
            "explanation": explanation.model_dump(mode="json"),
        }
        typer.echo(json.dumps(payload, separators=(",", ":")))
        return
    typer.echo(report.verdict.value)
    for finding in report.findings:
        typer.echo(f"{finding.status}: {finding.message}")
    if explanation is not None:
        typer.echo(f"Explanation ({explanation.source.value}): {explanation.explanation.summary}")


def _build_model_if_configured(settings: Settings) -> Model | None:
    """Build the live model only for an authorized live explanation attempt."""
    if not settings.hyperfusion_model or not settings.hyperfusion_model.strip():
        return None
    return build_hyperfusion_model(settings.require_hyperfusion())


async def _explain_report(
    report: MachineReport, artifacts: frozenset[str], model: Model | None
) -> ExplanationRecord:
    if model is None:
        return await _explain_without_model(report, artifacts)
    result = await investigate(
        InvestigationState(report=report, artifact_ids=artifacts),
        build_investigation_dependencies(report, artifacts=()),
        model,
    )
    return ExplanationRecord(
        explanation=result.explanation,
        source=ExplanationSource.FALLBACK if result.used_fallback else ExplanationSource.PROVIDER,
        tool_calls=result.tool_calls,
    )


async def _explain_without_model(
    report: MachineReport, artifacts: frozenset[str]
) -> ExplanationRecord:
    from settlediff.agent.grounding import fallback_explanation

    return ExplanationRecord(
        explanation=fallback_explanation(report, set(artifacts)),
        source=ExplanationSource.FALLBACK,
        tool_calls=0,
    )


@app.command("verify-fixture")
def verify_fixture(
    path: Path,
    json_mode: bool = JSON_OPTION,
    database: Path | None = OPTIONAL_DATABASE_OPTION,
) -> None:
    """Replay a sanitized fixture with no provider or payment call."""
    try:
        report = replay_fixture(path)
    except (OSError, ValueError, ValidationError) as error:
        raise typer.Exit(code=2) from typer.BadParameter(str(error))
    if database is not None:
        repository = SQLiteReportRepository(database)
        try:
            repository.save(report)
        finally:
            repository.close()
    _render(report, json_mode)


@app.command()
def run(
    url: str = typer.Option(...),
    body: str = typer.Option(...),
    budget: str = typer.Option(...),
    database: Path | None = OPTIONAL_DATABASE_OPTION,
    json_mode: bool = JSON_OPTION,
) -> None:
    """Run one explicit paid request after interactive authorization."""
    try:
        parsed_body = json.loads(body)
        if not isinstance(parsed_body, dict):
            raise ValueError("body must be a JSON object")
        amount = Decimal(budget)
        parsed_url = urlparse(url)
        if parsed_url.scheme != "https" or not parsed_url.netloc:
            raise ValueError("url must be an absolute HTTPS URL")
        if amount <= 0:
            raise ValueError("budget must be greater than zero")
    except (json.JSONDecodeError, InvalidOperation, ValueError) as error:
        typer.echo(f"Invalid live preflight: {error}", err=True)
        raise typer.Exit(code=2) from error
    try:
        settings = Settings()
        contextdev_config = settings.require_contextdev()
    except ValueError as error:
        typer.echo(f"Invalid live preflight: {error}", err=True)
        raise typer.Exit(code=2) from error
    telemetry = configure_telemetry(settings)
    contextdev = ContextDevClient(
        contextdev_config.base_url,
        contextdev_config.api_key,
        timeout_seconds=contextdev_config.timeout_seconds,
    )
    request = PaidExecutionRequest(
        run_id=f"live_{uuid4().hex}",
        target=url,
        body=cast(dict[str, JsonValue], parsed_body),
        budget=Money(amount=amount, unit="USDC"),
    )
    collector = LiveEvidenceCollector(PerfloClient(), contextdev=contextdev)
    try:
        try:
            asyncio.run(collector.preflight(request))
        except (PerfloClientError, ValueError) as error:
            typer.echo(f"Live preflight failed: {error}", err=True)
            raise typer.Exit(code=2) from error

        capability = PaidExecutionCapability.issue(
            request, expires_at=datetime.now(UTC) + timedelta(minutes=5)
        )
        typer.echo(
            "Target: "
            f"{request.target}\nBody digest: {capability.body_digest}\nBudget: {request.budget}"
        )
        if not typer.confirm("Authorize this exact paid request?"):
            typer.echo("Authorization declined; no paid request was sent.")
            raise typer.Exit(code=1)

        model = _build_model_if_configured(settings)
        try:
            outcome = asyncio.run(
                RunInvestigation(
                    collector.execute,
                    lambda: collector.verify(request),
                    telemetry=telemetry,
                    explain=lambda report, artifact_ids: _explain_report(
                        report, artifact_ids, model
                    ),
                    artifact_ids=lambda: frozenset(
                        artifact.artifact_id for artifact in collector.artifacts
                    ),
                ).execute(LiveRunCommand(request=request, capability=capability))
            )
        except (PerfloClientError, ValueError) as error:
            typer.echo(f"Live investigation failed: {error}", err=True)
            raise typer.Exit(code=2) from error
        if database is not None:
            repository = SQLiteReportRepository(database)
            try:
                repository.save(
                    outcome.report,
                    events=outcome.events,
                    artifacts=collector.artifacts,
                    explanation=outcome.explanation,
                )
            finally:
                repository.close()
        _render(outcome.report, json_mode=json_mode, explanation=outcome.explanation)
    finally:
        asyncio.run(contextdev.aclose())
        telemetry.shutdown()


@app.command()
def show(
    run_id: str,
    database: Path = DATABASE_OPTION,
    json_mode: bool = JSON_OPTION,
) -> None:
    """Render one persisted machine report without recomputing its findings."""
    repository = SQLiteReportRepository(database)
    try:
        report = repository.get(run_id)
        explanation = repository.explanation(run_id) if report is not None else None
    finally:
        repository.close()
    if report is None:
        typer.echo(f"Run {run_id} was not found.", err=True)
        raise typer.Exit(code=1)
    _render(report, json_mode, explanation)


@app.command()
def serve(
    database: Path = DATABASE_OPTION,
    port: int = typer.Option(8765, min=1, max=65535),
) -> None:
    """Serve persisted reports on loopback only."""
    repository = SQLiteReportRepository(database)
    uvicorn.run(create_app(repository), host="127.0.0.1", port=port)
