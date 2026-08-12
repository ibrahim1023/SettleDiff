# ADR 0003: Single Python Application and Server-Rendered UI

**Status:** Accepted  
**Date:** 2026-08-12

## Context

The MVP must deliver a CLI and compact local debugger UI within a short build window. A separate frontend would add a build toolchain, API duplication, and client-side state without improving the primary Expected/Executed/Recorded view.

## Decision

Build one Python 3.12 application managed by `uv`. Use Typer for CLI, FastAPI for HTTP, Jinja for HTML, minimal HTMX for refresh/expansion, and SQLite for local persistence. Package code under `src/settlediff`.

## Consequences

- One lockfile, one domain model, and one application-service layer serve both interfaces.
- UI interaction remains intentionally limited.
- SQLite supports local history without operating a database service.
- A future hosted or highly interactive product may require a new frontend/storage decision.

## Rejected

- React/Vite SPA for the MVP.
- Multiple services or a `uv` workspace.
- Postgres, Redis, queues, and background workers.
