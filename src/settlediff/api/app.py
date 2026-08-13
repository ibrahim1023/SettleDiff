"""Server-rendered local report debugger."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from html import escape
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from jinja2 import Environment, FileSystemLoader, select_autoescape

from settlediff.storage.sqlite import SQLiteReportRepository


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

    def runs() -> str:
        return templates.get_template("runs.html").render(reports=repository.list())

    app.get("/runs", response_class=HTMLResponse)(runs)

    def run_detail(run_id: str) -> str:
        report = repository.get(run_id)
        if report is None:
            raise HTTPException(status_code=404, detail="run not found")
        return templates.get_template("run_detail.html").render(report=report)

    app.get("/runs/{run_id}", response_class=HTMLResponse)(run_detail)

    def run_events(run_id: str) -> list[dict[str, str]]:
        if repository.get(run_id) is None:
            raise HTTPException(status_code=404, detail="run not found")
        return [event.model_dump(mode="json") for event in repository.events(run_id)]

    app.get("/runs/{run_id}/events")(run_events)

    def run_artifacts(run_id: str) -> str:
        if repository.get(run_id) is None:
            raise HTTPException(status_code=404, detail="run not found")
        payloads = "".join(
            f"<details><summary>{escape(artifact.artifact_id)}</summary>"
            f"<pre>{escape(artifact.model_dump_json())}</pre></details>"
            for artifact in repository.artifacts(run_id)
        )
        return f"<main><h1>Artifacts</h1>{payloads}</main>"

    app.get("/runs/{run_id}/artifacts", response_class=HTMLResponse)(run_artifacts)

    return app
