"""Server-rendered local report debugger."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from html import escape
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from jinja2 import Environment, FileSystemLoader, select_autoescape

from settlediff.application.run import RunEvent, RunState
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
            "default-src 'self'; style-src 'self' 'unsafe-inline'"
        )
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    app.middleware("http")(security_headers)

    def root() -> RedirectResponse:
        return RedirectResponse("/runs")

    app.get("/", response_class=RedirectResponse)(root)

    def runs(
        q: str | None = None,
        verdict: Verdict | None = None,
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
        return templates.get_template("runs.html").render(
            items=items,
            query=query,
            selected_verdict=verdict.value if verdict is not None else "",
            selected_sort=sort,
            verdicts=tuple(Verdict),
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

    def run_artifacts(run_id: str) -> str:
        if repository.get(run_id) is None:
            raise HTTPException(status_code=404, detail="run not found")
        payloads = "".join(
            f'<details id="{escape(_artifact_anchor(artifact.artifact_id), quote=True)}">'
            f"<summary>{escape(artifact.artifact_id)}</summary>"
            f"<pre>{escape(artifact.model_dump_json())}</pre></details>"
            for artifact in repository.artifacts(run_id)
        )
        return f"<main><h1>Artifacts</h1>{payloads}</main>"

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
