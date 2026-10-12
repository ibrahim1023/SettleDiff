# ADR 0013: Required Export for Live Investigations

**Status:** Accepted
**Date:** 2026-10-12

## Context

Logfire ingestion is configured and synthetic traces and metrics have been accepted.
The owner requires observability for live investigations rather than a silent
local-only fallback. ADR 0006 previously made export optional for every workflow.

## Decision

Supersede ADR 0006's optional-export decision for live investigations. Every
`settlediff run` requires an OTLP endpoint and authorization before provider or
signer probing, request confirmation, or payment. The existing composition exports
traces and metrics automatically using those settings. There is no live opt-out.
Logfire is the configured destination; an authenticated OTLP-compatible collector
can fulfill the same requirement.

Offline fixture replay, local evidence inspection, evals, and CI remain usable
without credentials or external telemetry. Tests inject in-memory exporters.
The synthetic trial remains offline by default and uses `--export` for delivery
verification. This test switch does not disable export in a live investigation.

Retain the existing privacy policy: no prompt, response, tool/body, raw evidence,
wallet identifier, or signature capture. Structured diagnostic logs remain local.
Exporter delivery failures after initialization remain best effort: they cannot
change financial findings, interrupt evidence recovery, or trigger a payment retry.
Required configuration does not guarantee backend availability or dashboard indexing.

## Consequences

- Missing export configuration stops a live run with an actionable error.
- Live investigation setup now includes authenticated telemetry configuration.
- No hosted service dependency is added to default tests or fixture replay.
- Logfire receives operational traces and metrics, not financial truth or raw evidence.

## Verification

Configuration tests cover absent endpoint/token and complete configuration. CLI
regressions prove both rails refuse missing export before probing providers.
Existing real-exporter tests intercept HTTP and verify routes, authentication, and
redaction. Synthetic live ingestion checks send no model requests or payments.
