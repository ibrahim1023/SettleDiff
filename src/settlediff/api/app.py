"""Server-rendered local report debugger."""

from __future__ import annotations

import json
import secrets
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from urllib.parse import parse_qs

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from jinja2 import Environment, FileSystemLoader, select_autoescape

from settlediff import __version__
from settlediff.application.run import RunEvent, RunState
from settlediff.contextdev.client import CONTEXTDEV_API_PATH
from settlediff.domain.models import CheckStatus, EvidenceArtifact, MachineReport, Verdict
from settlediff.domain.money import Money
from settlediff.domain.redaction import mask_identifier
from settlediff.storage.sqlite import SQLiteReportRepository


@dataclass(frozen=True)
class EvidenceRow:
    label: str
    check_id: str
    status: CheckStatus
    expected: str | None
    executed: str | None
    recorded: str | None


_TERMINAL_STATES = frozenset({RunState.COMPLETE, RunState.REFUSED, RunState.FAILED})


def create_app(repository: SQLiteReportRepository) -> FastAPI:
    app = FastAPI(title="SettleDiff", docs_url=None, redoc_url=None)
    csrf_token = secrets.token_urlsafe(32)
    templates = Environment(
        loader=FileSystemLoader(Path(__file__).parents[1] / "ui" / "templates"),
        autoescape=select_autoescape(["html"]),
    )
    static_directory = Path(__file__).parents[1] / "ui" / "static"
    app.mount("/static", StaticFiles(directory=static_directory), name="static")

    async def security_headers(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        response = await call_next(request)
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'"
        )
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.set_cookie(
            "settlediff_csrf",
            csrf_token,
            httponly=True,
            samesite="strict",
            path="/",
        )
        return response

    app.middleware("http")(security_headers)

    def root() -> RedirectResponse:
        return RedirectResponse("/runs")

    app.get("/", response_class=RedirectResponse)(root)

    def diagnostics() -> str:
        return templates.get_template("diagnostics.html").render(
            version=__version__,
            report_schema=1,
            database_schema=3,
            bundle_schema=2,
            contextdev_api_path=CONTEXTDEV_API_PATH,
            hyperfusion_model="Not recorded",
            perflo_version="Not recorded",
        )

    app.get("/diagnostics", response_class=HTMLResponse)(diagnostics)

    def runs(
        q: str | None = None,
        verdict: Verdict | None = None,
        state: str | None = None,
        sort: Literal["newest", "oldest", "verdict"] = "newest",
    ) -> str:
        reports = list(repository.list())
        query = q.strip() if q is not None else ""
        if query:
            normalized_query = query.casefold()
            reports = [
                report
                for report in reports
                if normalized_query in report.run_id.casefold()
                or normalized_query in report.intent.task.casefold()
            ]
        if verdict is not None:
            reports = [report for report in reports if report.verdict is verdict]
        if sort == "verdict":
            reports.sort(key=lambda report: (report.verdict.value, report.run_id))
        else:
            reports.sort(
                key=lambda report: (report.intent.created_at, report.run_id),
                reverse=sort == "newest",
            )
        items = tuple(
            {
                "report": report,
                "latest_state": _latest_state(repository.events(report.run_id)),
            }
            for report in reports
        )
        selected_state = state or ""
        if selected_state:
            items = tuple(item for item in items if item["latest_state"] == selected_state)
        return templates.get_template("runs.html").render(
            items=items,
            query=query,
            selected_verdict=verdict.value if verdict is not None else "",
            selected_state=selected_state,
            selected_sort=sort,
            verdicts=tuple(Verdict),
            states=("no timeline", *(run_state.value for run_state in RunState)),
        )

    app.get("/runs", response_class=HTMLResponse)(runs)

    def run_detail(run_id: str, differences: bool = False) -> str:
        report = repository.get(run_id)
        if report is None:
            raise HTTPException(status_code=404, detail="run not found")
        rows = _evidence_rows(report)
        if differences:
            rows = tuple(row for row in rows if row.status is not CheckStatus.PASS)
        explanation = repository.explanation(run_id)
        evidence_anchors = {row.check_id: f"evidence-{row.check_id}" for row in rows}
        finding_anchors = {
            finding.finding_id: f"finding-{finding.check_id}" for finding in report.findings
        }
        artifact_links = (
            {
                artifact.artifact_id: (
                    f"/runs/{report.run_id}/artifacts#{_artifact_anchor(artifact.artifact_id)}"
                )
                for artifact in repository.artifacts(run_id)
            }
            if explanation is not None
            else {}
        )
        return templates.get_template("run_detail.html").render(
            report=report,
            rows=rows,
            differences=differences,
            explanation_record=explanation,
            evidence_anchors=evidence_anchors,
            finding_anchors=finding_anchors,
            artifact_links=artifact_links,
            recovery_artifact=_recovery_artifact(repository.artifacts(run_id)),
            context_artifact=_context_artifact(repository.artifacts(run_id)),
        )

    app.get("/runs/{run_id}", response_class=HTMLResponse)(run_detail)

    def run_events(run_id: str) -> list[dict[str, str]]:
        if repository.get(run_id) is None:
            raise HTTPException(status_code=404, detail="run not found")
        return [event.model_dump(mode="json") for event in repository.events(run_id)]

    app.get("/runs/{run_id}/events")(run_events)

    def event_fragment(run_id: str) -> str:
        if repository.get(run_id) is None:
            raise HTTPException(status_code=404, detail="run not found")
        events = repository.events(run_id)
        terminal = not events or events[-1].state in _TERMINAL_STATES
        return templates.get_template("events_fragment.html").render(
            run_id=run_id,
            rows=_event_rows(events),
            terminal=terminal,
        )

    app.get("/runs/{run_id}/events-fragment", response_class=HTMLResponse)(event_fragment)

    def confirm_delete(run_id: str) -> str:
        report = repository.get(run_id)
        if report is None:
            raise HTTPException(status_code=404, detail="run not found")
        return templates.get_template("delete_run.html").render(
            report=report,
            csrf_token=csrf_token,
        )

    app.get("/runs/{run_id}/delete", response_class=HTMLResponse)(confirm_delete)

    async def delete_run(run_id: str, request: Request) -> RedirectResponse:
        report = repository.get(run_id)
        if report is None:
            raise HTTPException(status_code=404, detail="run not found")
        body = (await request.body()).decode("utf-8", errors="strict")
        submitted = parse_qs(body).get("csrf_token", [""])[0]
        cookie = request.cookies.get("settlediff_csrf", "")
        if not (
            secrets.compare_digest(submitted, csrf_token)
            and secrets.compare_digest(cookie, csrf_token)
        ):
            raise HTTPException(status_code=403, detail="invalid CSRF token")
        repository.delete(run_id)
        return RedirectResponse("/runs", status_code=303)

    app.post("/runs/{run_id}/delete", response_class=RedirectResponse)(delete_run)

    def run_artifacts(run_id: str) -> str:
        report = repository.get(run_id)
        if report is None:
            raise HTTPException(status_code=404, detail="run not found")
        return templates.get_template("artifacts.html").render(
            report=report,
            groups=_artifact_groups(repository.artifacts(run_id)),
        )

    app.get("/runs/{run_id}/artifacts", response_class=HTMLResponse)(run_artifacts)

    return app


def _latest_state(events: tuple[RunEvent, ...]) -> str:
    return events[-1].state.value if events else "no timeline"


def _artifact_anchor(artifact_id: str) -> str:
    return artifact_id.replace(":", "-")


def _recovery_artifact(
    artifacts: tuple[EvidenceArtifact, ...],
) -> EvidenceArtifact | None:
    for artifact in artifacts:
        if artifact.artifact_id.endswith(":recovery"):
            return artifact
    return None


def _context_artifact(
    artifacts: tuple[EvidenceArtifact, ...],
) -> EvidenceArtifact | None:
    for artifact in artifacts:
        if artifact.source == "contextdev":
            return artifact
    return None


def _artifact_groups(
    artifacts: tuple[EvidenceArtifact, ...],
) -> tuple[dict[str, object], ...]:
    grouped: dict[str, list[EvidenceArtifact]] = {}
    for artifact in artifacts:
        grouped.setdefault(artifact.source, []).append(artifact)
    return tuple(
        {
            "source": source,
            "label": source.replace(".", " ").replace("_", " ").title(),
            "artifacts": tuple(
                {
                    "artifact": artifact,
                    "anchor": _artifact_anchor(artifact.artifact_id),
                    "payload": json.dumps(artifact.model_dump(mode="json"), indent=2),
                }
                for artifact in sorted(group, key=lambda item: item.artifact_id)
            ),
        }
        for source, group in sorted(grouped.items())
    )


def _evidence_rows(report: MachineReport) -> tuple[EvidenceRow, ...]:
    findings = {finding.check_id: finding for finding in report.findings}

    def row(
        label: str,
        check_id: str,
        expected: str | None,
        executed: str | None,
        recorded: str | None,
    ) -> EvidenceRow:
        finding = findings.get(check_id)
        return EvidenceRow(
            label=label,
            check_id=check_id,
            status=finding.status if finding is not None else CheckStatus.UNKNOWN,
            expected=expected,
            executed=executed,
            recorded=recorded,
        )

    return (
        row(
            "Price",
            "price",
            _display(report.contract.price if report.contract else None),
            _display(report.execution.charge if report.execution else None),
            _display(report.ledger.amount if report.ledger else None),
        ),
        row(
            "Protocol",
            "protocol",
            _display(_value(report.contract, "protocol")),
            _display(_value(report.execution, "protocol")),
            _display(_value(report.ledger, "protocol")),
        ),
        row(
            "Chain",
            "chain",
            _display(_value(report.contract, "chain")),
            _display(_value(report.execution, "chain")),
            _display(_value(report.ledger, "chain")),
        ),
        row(
            "Recipient",
            "recipient",
            None,
            _display(_value(report.execution, "recipient"), identifier=True),
            _display(_value(report.ledger, "recipient"), identifier=True),
        ),
        row(
            "Settlement",
            "settlement",
            None,
            _display(_value(report.execution, "settlement_status")),
            _display(_value(report.ledger, "status")),
        ),
    )


def _event_rows(events: tuple[RunEvent, ...]) -> tuple[dict[str, object], ...]:
    rows: list[dict[str, object]] = []
    for index, event in enumerate(events):
        next_event = events[index + 1] if index + 1 < len(events) else None
        duration = (
            _duration((next_event.occurred_at - event.occurred_at).total_seconds())
            if next_event is not None
            else None
        )
        rows.append(
            {
                "state": event.state.value,
                "occurred_at": event.occurred_at,
                "duration": duration,
                "current": index == len(events) - 1,
            }
        )
    return tuple(rows)


def _duration(seconds: float) -> str:
    return "<1s" if seconds < 1 else f"{seconds:.1f}s"


def _value(record: object | None, field: str) -> object | None:
    return getattr(record, field) if record is not None else None


def _display(value: object | None, *, identifier: bool = False) -> str | None:
    if value is None:
        return None
    if identifier and isinstance(value, str):
        return mask_identifier(value)
    if isinstance(value, Money):
        return f"{value.amount} {value.unit}"
    return str(value)
