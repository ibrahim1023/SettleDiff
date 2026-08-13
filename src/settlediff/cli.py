"""Command-line interface for deterministic fixture verification and live preflight."""

import json
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import cast

import typer
from pydantic import JsonValue, ValidationError

from settlediff.application.auth import PaidExecutionCapability, PaidExecutionRequest
from settlediff.application.replay import replay_fixture
from settlediff.domain.models import MachineReport
from settlediff.domain.money import Money

app = typer.Typer(
    name="settlediff",
    help="Investigate agent purchases with deterministic verification.",
    no_args_is_help=True,
)


@app.callback()
def main() -> None:
    """Investigate agent purchases with deterministic verification."""


def _render(report: MachineReport, json_mode: bool) -> None:
    if json_mode:
        typer.echo(report.model_dump_json())
        return
    typer.echo(report.verdict.value)
    for finding in report.findings:
        typer.echo(f"{finding.status}: {finding.message}")


@app.command("verify-fixture")
def verify_fixture(path: Path, json_mode: bool = typer.Option(False, "--json")) -> None:
    """Replay a sanitized fixture with no provider or payment call."""
    try:
        _render(replay_fixture(path), json_mode)
    except (OSError, ValueError, ValidationError) as error:
        raise typer.Exit(code=2) from typer.BadParameter(str(error))


@app.command()
def run(
    url: str = typer.Option(...),
    body: str = typer.Option(...),
    budget: str = typer.Option(...),
) -> None:
    """Validate and display an exact live authorization request before any adapter exists."""
    try:
        parsed_body = json.loads(body)
        if not isinstance(parsed_body, dict):
            raise ValueError("body must be a JSON object")
        amount = Decimal(budget)
    except (json.JSONDecodeError, InvalidOperation, ValueError) as error:
        typer.echo(f"Invalid live preflight: {error}", err=True)
        raise typer.Exit(code=2) from error
    request = PaidExecutionRequest(
        run_id="interactive-preflight",
        target=url,
        body=cast(dict[str, JsonValue], parsed_body),
        budget=Money(amount=amount, unit="USDC"),
    )
    capability = PaidExecutionCapability.issue(request, expires_at=datetime.now(UTC))
    typer.echo(
        f"Target: {request.target}\nBody digest: {capability.body_digest}\nBudget: {request.budget}"
    )
    typer.echo("Live execution wiring is not available yet; no external call was made.", err=True)
    raise typer.Exit(code=2)


@app.command()
def show(run_id: str) -> None:
    """Report that durable report lookup arrives with the local repository."""
    typer.echo(f"Run {run_id} is not persisted yet; SQLite storage arrives in Phase 11.", err=True)
    raise typer.Exit(code=2)
