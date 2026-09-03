"""Command-line interface for deterministic fixture verification and live preflight."""

import asyncio
import json
import re
import shutil
import sqlite3
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from pathlib import Path
from typing import cast
from urllib.parse import urlparse
from uuid import uuid4

import httpx
import typer
import uvicorn
from pydantic import JsonValue, ValidationError
from pydantic_ai.models import Model

from settlediff import __version__
from settlediff.agent.investigator import (
    INVESTIGATION_INPUT_TOKEN_LIMIT,
    INVESTIGATION_OUTPUT_TOKEN_LIMIT,
    INVESTIGATION_REQUEST_LIMIT,
    INVESTIGATION_TOOL_CALL_LIMIT,
    InvestigationState,
    investigate,
)
from settlediff.agent.model import build_hyperfusion_model
from settlediff.agent.tools import build_investigation_dependencies
from settlediff.api.app import create_app
from settlediff.application.auth import PaidExecutionCapability, PaidExecutionRequest
from settlediff.application.budget import InvestigationBudget, InvestigationBudgetState
from settlediff.application.bundle import (
    BundleError,
    export_bundle,
    load_bundle,
    serialize_bundle,
    verify_bundle,
)
from settlediff.application.payment_rails import PaymentRailAdapter, SubmissionUncertainError
from settlediff.application.replay import replay_fixture
from settlediff.application.run import (
    InvestigationOutcome,
    LiveEvidenceCollector,
    LiveRunCommand,
    RunEvent,
    RunFailure,
    RunInvestigation,
    RunProvenance,
    RunState,
    SubmissionRecovery,
)
from settlediff.config import Settings
from settlediff.contextdev.client import ContextDevClient
from settlediff.domain.models import (
    ExplanationRecord,
    ExplanationSource,
    MachineReport,
)
from settlediff.domain.money import Money
from settlediff.domain.redaction import mask_identifier
from settlediff.perflo.adapter import PerfloAdapter
from settlediff.perflo.client import PerfloClient, PerfloClientError
from settlediff.storage.sqlite import SQLiteReportRepository
from settlediff.telemetry.setup import TelemetryRuntime, configure_telemetry
from settlediff.x402.adapter import X402Adapter
from settlediff.x402.client import X402ClientError, X402ExternalClient, probe_x402_signer
from settlediff.x402.http import X402ResourceClient
from settlediff.x402.rpc import X402RpcClient
from settlediff.x402.urls import is_safe_x402_target


class PaymentRail(StrEnum):
    PERFLO = "perflo"
    X402 = "x402"


class HttpMethod(StrEnum):
    GET = "GET"
    POST = "POST"


AdapterCloser = Callable[[], Awaitable[None]]


app = typer.Typer(
    name="settlediff",
    help="Investigate agent purchases with deterministic verification.",
    no_args_is_help=True,
)
DATABASE_OPTION = typer.Option(..., "--database", exists=True, readable=True)
JSON_OPTION = typer.Option(False, "--json")
OPTIONAL_DATABASE_OPTION = typer.Option(None, "--database")
BUNDLE_OUTPUT_OPTION = typer.Option(..., "--output")
BUNDLE_FORCE_OPTION = typer.Option(False, "--force", help="Replace an existing output file.")
RAIL_OPTION = typer.Option(PaymentRail.PERFLO, "--rail")
METHOD_OPTION = typer.Option(HttpMethod.POST, "--method")
ALLOW_TESTNET_OPTION = typer.Option(False, "--allow-testnet")


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"settlediff {__version__}")
        raise typer.Exit


VERSION_OPTION = typer.Option(
    False,
    "--version",
    callback=_version_callback,
    is_eager=True,
    help="Show the installed SettleDiff version and exit.",
)


@app.callback()
def main(_version: bool = VERSION_OPTION) -> None:
    """Investigate agent purchases with deterministic verification."""


def _render(
    report: MachineReport,
    json_mode: bool,
    explanation: ExplanationRecord | None = None,
    recovery: SubmissionRecovery | None = None,
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
    if report.adapter_id is not None:
        typer.echo(f"Payment rail: {report.adapter_id}")
    typer.echo(report.verdict.value)
    for finding in report.findings:
        typer.echo(f"{finding.status}: {finding.message}")
    if explanation is not None:
        typer.echo(f"Explanation ({explanation.source.value}): {explanation.explanation.summary}")
        typer.echo(
            "Usage: "
            f"requests={explanation.model_requests}, "
            f"tool_calls={explanation.tool_calls}, "
            f"input_tokens={explanation.input_tokens}, "
            f"output_tokens={explanation.output_tokens}"
        )
    if recovery is not None:
        typer.echo(f"Submission: {recovery.state.value}")
        proof = "yes" if recovery.proof_of_non_submission else "no"
        typer.echo(f"proof of non-submission: {proof}")


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
        model_requests=result.model_requests,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        model_cost=result.model_cost,
        rejected_output=result.rejected_output,
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


def _record_live_failure(
    repository: SQLiteReportRepository | None,
    run_id: str,
    error: Exception,
    *,
    submission_uncertain: bool,
) -> None:
    if repository is None:
        return
    events = list(repository.events(run_id))
    stage = RunState.PREFLIGHT
    if events:
        stage = (
            events[-2].state
            if events[-1].state is RunState.FAILED and len(events) > 1
            else events[-1].state
        )
    if not events or events[-1].state is not RunState.FAILED:
        repository.append_event(
            run_id,
            RunEvent(state=RunState.FAILED, occurred_at=datetime.now(UTC)),
        )
    repository.record_failure(
        run_id,
        RunFailure(
            stage=stage,
            error_class=type(error).__name__,
            diagnostic=f"{stage.value} failed",
            submission_uncertain=submission_uncertain,
            occurred_at=datetime.now(UTC),
        ),
    )


async def _execute_live_run(
    request: PaidExecutionRequest,
    settings: Settings,
    contextdev: ContextDevClient,
    collector: LiveEvidenceCollector,
    budget: InvestigationBudgetState,
    telemetry: TelemetryRuntime,
    adapter_close: AdapterCloser | None = None,
    repository: SQLiteReportRepository | None = None,
) -> InvestigationOutcome:
    """Keep every async live boundary on one event loop."""

    async def persist_event(event: RunEvent) -> None:
        if repository is not None:
            repository.append_event(request.run_id, event)
            repository.save_artifacts(request.run_id, collector.artifacts)

    try:
        try:
            await collector.preflight(request)
            if repository is not None:
                repository.save_artifacts(request.run_id, collector.artifacts)
        except (PerfloClientError, X402ClientError, OSError, ValueError) as error:
            _record_live_failure(
                repository,
                request.run_id,
                error,
                submission_uncertain=False,
            )
            typer.echo(f"Live preflight failed: {error}", err=True)
            raise typer.Exit(code=2) from error

        payment_terms = collector.payment_terms
        capability = PaidExecutionCapability.issue(
            request,
            payment_terms=payment_terms,
            expires_at=datetime.now(UTC) + timedelta(minutes=5),
        )
        identity = payment_terms.asset
        asset_label = identity.symbol if identity is not None else payment_terms.asset_symbol
        recipient_label = payment_terms.recipient or "unknown"
        asset_reference = identity.reference if identity is not None else "unknown"
        timeout_label = (
            f"{payment_terms.max_timeout_seconds} seconds"
            if payment_terms.max_timeout_seconds is not None
            else "unknown"
        )
        typer.echo(
            f"Rail: {payment_terms.adapter_id}\n"
            f"Version: {payment_terms.protocol_version or 'unknown'}\n"
            f"Scheme: {payment_terms.scheme or 'unknown'}\n"
            f"Network: {payment_terms.network or payment_terms.chain or 'unknown'}\n"
            f"Target: {request.target}\n"
            f"Resource: {payment_terms.resource_url}\n"
            f"Method: {payment_terms.method}\n"
            f"Body digest: {capability.body_digest}\n"
            f"Payment terms digest: {capability.payment_terms_digest}\n"
            f"Quoted price: {payment_terms.quoted_price.amount} "
            f"{payment_terms.quoted_price.unit}\n"
            f"Budget: {request.budget.amount} {request.budget.unit}\n"
            f"Asset: {asset_label or 'unknown'}\n"
            f"Asset reference: {asset_reference}\n"
            f"Recipient: {recipient_label}\n"
            f"Maximum timeout: {timeout_label}\n"
            f"External signer: "
            f"{'configured' if payment_terms.adapter_id == 'x402' else 'not applicable'}"
        )
        typer.echo(
            "Investigation budget: Context.dev calls: 1, "
            f"model requests: {INVESTIGATION_REQUEST_LIMIT}, "
            f"tool calls: {INVESTIGATION_TOOL_CALL_LIMIT}, "
            f"input tokens: {INVESTIGATION_INPUT_TOKEN_LIMIT}, "
            f"output tokens: {INVESTIGATION_OUTPUT_TOKEN_LIMIT}"
        )
        if not typer.confirm("Authorize this exact paid request?"):
            if repository is not None:
                repository.append_event(
                    request.run_id,
                    RunEvent(state=RunState.REFUSED, occurred_at=datetime.now(UTC)),
                )
            typer.echo("Authorization declined; no paid request was sent.")
            raise typer.Exit(code=1)

        model = _build_model_if_configured(settings)
        return await RunInvestigation(
            collector.execute,
            lambda: collector.verify(request),
            persist_event=persist_event,
            telemetry=telemetry,
            explain=lambda report, artifact_ids: _explain_report(report, artifact_ids, model),
            artifact_ids=lambda: frozenset(
                artifact.artifact_id for artifact in collector.artifacts
            ),
            budget=budget,
            recover=collector.recover_submission,
            transaction_hash=lambda: collector.transaction_reference,
        ).execute(
            LiveRunCommand(
                request=request,
                capability=capability,
                payment_terms=payment_terms,
            )
        )
    finally:
        if adapter_close is not None:
            await adapter_close()
        await contextdev.aclose()


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


def _build_payment_adapter(
    rail: PaymentRail, settings: Settings
) -> tuple[PaymentRailAdapter, AdapterCloser | None]:
    if rail is PaymentRail.PERFLO:
        return PerfloAdapter(PerfloClient()), None
    config = settings.require_x402()
    resource_http = httpx.AsyncClient(follow_redirects=False)
    rpc_http = httpx.AsyncClient(base_url=config.rpc_url.get_secret_value(), follow_redirects=False)
    adapter = X402Adapter(
        X402ResourceClient(
            resource_http,
            timeout_seconds=config.resource_timeout_seconds,
        ),
        X402ExternalClient(
            command=config.signer_command,
            timeout_seconds=config.signer_timeout_seconds,
        ),
        X402RpcClient(
            rpc_http,
            timeout_seconds=config.rpc_timeout_seconds,
        ),
    )

    async def close() -> None:
        await resource_http.aclose()
        await rpc_http.aclose()

    return adapter, close


async def _doctor_x402(settings: Settings) -> tuple[str, str]:
    config = settings.require_x402()
    if not config.testnet_enabled:
        raise ValueError("x402 testnet configuration is disabled")
    if shutil.which(config.signer_command[0]) is None:
        raise ValueError("x402 signer launcher is unavailable")
    metadata = await probe_x402_signer(
        config.signer_command,
        timeout_seconds=config.signer_timeout_seconds,
    )
    rpc_http = httpx.AsyncClient(
        base_url=config.rpc_url.get_secret_value(),
        follow_redirects=False,
    )
    try:
        chain_id = await X402RpcClient(
            rpc_http,
            max_requests=1,
            timeout_seconds=config.rpc_timeout_seconds,
        ).call("eth_chainId", ())
    finally:
        await rpc_http.aclose()
    if chain_id != "0x14a34":
        raise ValueError("x402 RPC does not report Base Sepolia")
    return str(chain_id), mask_identifier(metadata.payer)


def _check_database(database: Path | None) -> None:
    if database is None:
        return
    repository = SQLiteReportRepository(database)
    try:
        repository.check_writable()
    finally:
        repository.close()


@app.command()
def doctor(
    rail: PaymentRail = RAIL_OPTION,
    database: Path | None = OPTIONAL_DATABASE_OPTION,
) -> None:
    """Check live dependencies without signing or paying."""
    try:
        settings = Settings()
        settings.require_contextdev()
        _check_database(database)
        typer.echo("Database: writable" if database is not None else "Database: not selected")
        typer.echo("Context.dev: configured")
        if rail is PaymentRail.PERFLO:
            if shutil.which("perflo") is None:
                raise ValueError("Perflo executable is unavailable")
            typer.echo("Perflo: executable available")
        else:
            chain_id, payer = asyncio.run(_doctor_x402(settings))
            typer.echo("Signer schema: 2")
            typer.echo(f"Signer payer: {payer}")
            typer.echo(f"RPC chain: {chain_id} (Base Sepolia)")
    except (OSError, X402ClientError, ValueError) as error:
        typer.echo(f"Doctor failed: {error}", err=True)
        raise typer.Exit(code=2) from error


@app.command()
def run(
    url: str = typer.Option(...),
    budget: str = typer.Option(...),
    body: str | None = typer.Option(None),
    rail: PaymentRail = RAIL_OPTION,
    method: HttpMethod = METHOD_OPTION,
    allow_testnet: bool = ALLOW_TESTNET_OPTION,
    database: Path | None = OPTIONAL_DATABASE_OPTION,
    json_mode: bool = JSON_OPTION,
) -> None:
    """Run one explicit paid request after interactive authorization."""
    try:
        parsed_body: JsonValue | None
        if method is HttpMethod.GET:
            if body is not None:
                raise ValueError("GET requests must omit --body")
            parsed_body = None
        else:
            if body is None:
                raise ValueError("POST requests require --body")
            parsed_body = cast(JsonValue, json.loads(body))
            if rail is PaymentRail.PERFLO and not isinstance(parsed_body, dict):
                raise ValueError("Perflo body must be a JSON object")
        if rail is PaymentRail.PERFLO and method is not HttpMethod.POST:
            raise ValueError("Perflo supports POST requests only")
        amount = Decimal(budget)
        parsed_url = urlparse(url)
        safe_url = (
            is_safe_x402_target(url)
            if rail is PaymentRail.X402
            else (
                parsed_url.scheme == "https"
                and bool(parsed_url.netloc)
                and parsed_url.username is None
                and parsed_url.password is None
                and not parsed_url.fragment
            )
        )
        if not safe_url:
            raise ValueError(
                "url requires HTTPS, except loopback HTTP for x402, without credentials or fragment"
            )
        if amount <= 0:
            raise ValueError("budget must be greater than zero")
    except (json.JSONDecodeError, InvalidOperation, ValueError) as error:
        typer.echo(f"Invalid live preflight: {error}", err=True)
        raise typer.Exit(code=2) from error
    try:
        settings = Settings()
        contextdev_config = settings.require_contextdev()
        if rail is PaymentRail.X402:
            x402_config = settings.require_x402()
            if not allow_testnet or not x402_config.testnet_enabled:
                raise ValueError(
                    "x402 testnet execution requires --allow-testnet and "
                    "SETTLEDIFF_X402_TESTNET_ENABLED=true"
                )
            if shutil.which(x402_config.signer_command[0]) is None:
                raise ValueError("x402 signer launcher is unavailable; run settlediff doctor")
        elif shutil.which("perflo") is None:
            raise ValueError("Perflo executable is unavailable; run settlediff doctor")
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
        method=method.value,
        body=parsed_body,
        budget=Money(amount=amount, unit="USDC"),
    )
    repository: SQLiteReportRepository | None = None
    if database is not None:
        try:
            repository = SQLiteReportRepository(database)
            provenance = (
                RunProvenance.CONTROLLED_LIVE
                if rail is PaymentRail.X402 and parsed_url.scheme == "http"
                else RunProvenance.EXTERNAL_LIVE
            )
            repository.begin_run(
                request.run_id,
                task=f"{rail.value} request to {parsed_url.hostname}",
                provenance=provenance,
                created_at=datetime.now(UTC),
            )
        except (OSError, sqlite3.Error, ValueError) as error:
            if repository is not None:
                repository.close()
            typer.echo("Live run database is not writable.", err=True)
            raise typer.Exit(code=2) from error
    budget_state = InvestigationBudgetState(
        InvestigationBudget.issue(
            request.run_id,
            contextdev_calls=1,
            model_requests=INVESTIGATION_REQUEST_LIMIT,
            tool_calls=INVESTIGATION_TOOL_CALL_LIMIT,
            input_tokens=INVESTIGATION_INPUT_TOKEN_LIMIT,
            output_tokens=INVESTIGATION_OUTPUT_TOKEN_LIMIT,
        )
    )
    adapter, adapter_close = _build_payment_adapter(rail, settings)
    collector = LiveEvidenceCollector(
        adapter,
        contextdev=contextdev,
        budget=budget_state,
        telemetry=telemetry,
    )
    try:
        try:
            outcome = asyncio.run(
                _execute_live_run(
                    request,
                    settings,
                    contextdev,
                    collector,
                    budget_state,
                    telemetry,
                    adapter_close,
                    repository,
                )
            )
        except (PerfloClientError, X402ClientError, OSError, ValueError) as error:
            _record_live_failure(
                repository,
                request.run_id,
                error,
                submission_uncertain=isinstance(error, SubmissionUncertainError),
            )
            message = "signer process could not start" if isinstance(error, OSError) else str(error)
            typer.echo(f"Live investigation failed: {message}", err=True)
            raise typer.Exit(code=2) from error
        persistence_failed = False
        if repository is not None:
            try:
                with telemetry.span("settlediff.storage.persist", {"component": "storage"}):
                    repository.save_artifacts(request.run_id, collector.artifacts)
                    repository.finalize_run(
                        outcome.report,
                        explanation=outcome.explanation,
                    )
            except (OSError, sqlite3.Error):
                persistence_failed = True
                typer.echo(
                    "Critical: report finalization failed; the durable run remains available.",
                    err=True,
                )
        with telemetry.span("settlediff.render", {"component": "rendering"}):
            _render(
                outcome.report,
                json_mode=json_mode,
                explanation=outcome.explanation,
                recovery=outcome.recovery,
            )
        if persistence_failed:
            raise typer.Exit(code=2)
    finally:
        if repository is not None:
            repository.close()
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


_DURATION = re.compile(r"([0-9]+)([dhm])")


def _parse_duration(value: str) -> timedelta:
    """Parse a strict retention duration like 30d, 12h, or 45m."""
    match = _DURATION.fullmatch(value)
    if match is None:
        raise typer.BadParameter(
            "duration must be a whole number followed by d, h, or m (e.g. 30d)"
        )
    amount = int(match.group(1))
    if amount == 0:
        raise typer.BadParameter("duration must be greater than zero")
    unit = match.group(2)
    if unit == "d":
        return timedelta(days=amount)
    if unit == "h":
        return timedelta(hours=amount)
    return timedelta(minutes=amount)


@app.command("delete")
def delete_run(
    run_id: str,
    database: Path = DATABASE_OPTION,
    yes: bool = typer.Option(False, "--yes", help="Skip the interactive confirmation."),
) -> None:
    """Delete one persisted run after an explicit confirmation."""
    repository = SQLiteReportRepository(database)
    try:
        report = repository.get(run_id)
        if report is None:
            typer.echo(f"Run {run_id} was not found.", err=True)
            raise typer.Exit(code=1)
        if not yes:
            typer.echo(f"Run: {report.run_id}")
            typer.echo(f"Verdict: {report.verdict.value}")
            typer.echo(f"Created: {report.intent.created_at.isoformat()}")
            if not typer.confirm(f"Delete run {report.run_id}?"):
                typer.echo("Deletion cancelled; nothing was deleted.")
                raise typer.Exit(code=1)
        repository.delete(report.run_id)
    finally:
        repository.close()
    typer.echo(f"Deleted run {run_id}.")


@app.command()
def purge(
    database: Path = DATABASE_OPTION,
    older_than: str = typer.Option(
        ..., "--older-than", help="Retention cutoff as a strict duration like 30d, 12h, or 45m."
    ),
    apply: bool = typer.Option(False, "--apply", help="Delete instead of only listing runs."),
) -> None:
    """Delete persisted runs older than a retention cutoff; dry-run by default."""
    cutoff = datetime.now(UTC) - _parse_duration(older_than)
    repository = SQLiteReportRepository(database)
    try:
        stale = sorted(
            (report for report in repository.list() if report.intent.created_at < cutoff),
            key=lambda report: (report.intent.created_at, report.run_id),
        )
        if apply:
            for report in stale:
                repository.delete(report.run_id)
    finally:
        repository.close()
    if not stale:
        typer.echo(f"No runs older than {older_than}; nothing to delete.")
        return
    for report in stale:
        typer.echo(f"{report.run_id} {report.verdict.value}")
    if not apply:
        typer.echo(f"Dry run: {len(stale)} run(s) would be deleted; pass --apply to delete them.")
    else:
        typer.echo(f"Purged {len(stale)} run(s).")


@app.command("export")
def export_run(
    run_id: str,
    database: Path = DATABASE_OPTION,
    output: Path = BUNDLE_OUTPUT_OPTION,
    force: bool = BUNDLE_FORCE_OPTION,
) -> None:
    """Export one persisted run as a versioned redacted evidence bundle."""
    if output.exists() and not force:
        typer.echo(f"Output {output} already exists; pass --force to replace it.", err=True)
        raise typer.Exit(code=2)
    repository = SQLiteReportRepository(database)
    try:
        try:
            bundle = export_bundle(repository, run_id)
        except BundleError as error:
            typer.echo(str(error), err=True)
            raise typer.Exit(code=1) from error
    finally:
        repository.close()
    try:
        output.write_bytes(serialize_bundle(bundle))
    except OSError as error:
        typer.echo(f"Could not write bundle: {error}", err=True)
        raise typer.Exit(code=2) from error
    typer.echo(f"Exported run {run_id} to {output}.")


@app.command("verify-bundle")
def verify_evidence_bundle(path: Path) -> None:
    """Verify one bundle's checksum, redaction, and internal consistency."""
    try:
        bundle = load_bundle(path.read_bytes())
        report = verify_bundle(bundle)
    except (BundleError, OSError) as error:
        typer.echo(f"Bundle verification failed: {error}", err=True)
        raise typer.Exit(code=2) from error
    typer.echo(f"Verified bundle {bundle.run_id}: {report.verdict.value}")
    typer.echo("Checksum and internal consistency verified; authenticity is not established.")


@app.command()
def serve(
    database: Path = DATABASE_OPTION,
    port: int = typer.Option(8765, min=1, max=65535),
) -> None:
    """Serve persisted reports on loopback only."""
    repository = SQLiteReportRepository(database)
    uvicorn.run(create_app(repository), host="127.0.0.1", port=port)
