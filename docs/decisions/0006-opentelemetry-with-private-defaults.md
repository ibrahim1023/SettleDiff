# ADR 0006: Optional OpenTelemetry with Private Defaults

**Status:** Accepted  
**Date:** 2026-08-12

## Context

Investigations cross model calls, subprocesses, matching, verification, and rendering. Correlation is valuable, but prompts, tool arguments, receipts, wallets, and response bodies may contain sensitive financial or personal data.

## Decision

Emit structured local logs and OpenTelemetry-compatible spans behind a telemetry port. Export is optional. Content capture is off by default, identifiers are hashed or omitted, and metrics use low-cardinality attributes only. Stored local event summaries remain useful with no telemetry backend.

PydanticAI instrumentation uses current OpenTelemetry GenAI conventions where stable enough; SettleDiff-specific financial spans use the `settlediff.*` namespace.

## Consequences

- Operators can choose any OTLP-compatible backend.
- Local development has no hosted observability dependency.
- Some traces contain handles rather than raw debugging content; artifacts remain available through explicit local report access.
- Semantic convention changes may require versioned instrumentation updates.

## Rejected

- Mandatory Logfire, LangSmith, or another hosted platform.
- Prompt/tool-body capture by default.
- Wallet, URL, transaction, run, or session identifiers as metric labels.
