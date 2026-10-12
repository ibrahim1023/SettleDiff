# Observability and evals: current state

Assessment on 2026-10-12. SettleDiff needs observability and already implements the
foundation accepted in [ADR 0006](../decisions/0006-opentelemetry-with-private-defaults.md).
The next useful investment is connecting observed failures to runnable offline cases.

## What exists

`src/settlediff/telemetry/setup.py` provides JSON logs, OpenTelemetry spans, approved
counters and histograms, and optional OTLP export. PydanticAI content and binary
capture are disabled. Attribute allowlists, secret filtering, salted correlation
identifiers, and bounded metric labels protect exported diagnostics. In-memory tests
cover redaction, metric contracts, and exporter failures without an external backend.

The CLI investigation path configures telemetry and instruments application boundaries,
storage, and rendering. Local run records and source-attributed evidence timelines
provide context alongside reports and artifacts. Operational traces describe the
investigation; deterministic reports remain the authority for financial conclusions.

Agent unit tests exercise model wiring, grounding, limits, fallback, and usage with
offline models. `tests/evals/graders.py` checks verdict consistency, citation validity,
allowed tools, and required/forbidden trajectory steps.

## What is incomplete

`tests/evals/dataset.py` lists six scenario names. `test_regression.py` checks membership
and grader behavior, but does not execute an investigation for each named scenario.
The unit suite covers additional behavior; the dataset itself is not yet a complete
scenario runner or a record of experiments against agent versions.

There is no observed-failure-to-case promotion workflow in the inspected eval files.
Exported diagnostic spans intentionally omit input and tool payloads, so an exported
span alone is insufficient to replay a financial investigation. A replay case needs
sanitized evidence and fixed tool responses from the local evidence boundary.

## Recommended next increment

1. Turn the six scenario names into runnable cases with explicit required tools,
   forbidden actions, expected deterministic findings, and grounding expectations.
   Keep fixed evidence and tool responses; use offline models and intercept every write.
2. Record case identity, code revision, agent configuration, grader results, and
   trajectory summaries for comparison between runs. Treat this as eval metadata,
   separate from evidence and findings.
3. Add a reviewed promotion path from a local failed investigation to a sanitized
   fixture and agent case. Scrub identifiers and secrets, preserve source provenance,
   and have a human specify expected behavior. Never use the failing output as truth.
4. Run the whole offline dataset in the existing gate. For later opt-in live model
   trials, define repeated-run acceptance criteria before running them; any safety
   violation fails the trial. Live trials require separate authorization and budgets.

These are proposed follow-up changes, not implemented features. They extend
[ADR 0005](../decisions/0005-fixture-first-testing-and-evals.md) and the
[agent verification loop](verification-loops.md) without adding a hosted dependency.

## Backend decision

Keep local diagnostics as the default. Consider an OTLP backend when repeated live
investigations make cross-run latency, provider errors, or model usage difficult to
inspect locally. Evaluate backend compatibility and retention before enabling export.
The current configuration has one `SETTLEDIFF_OTLP_ENDPOINT`, passed directly to both
the HTTP trace and metric exporters. The [OTLP specification](https://opentelemetry.io/docs/specs/otlp/#otlphttp-request)
defines separate default paths, `/v1/traces` and `/v1/metrics`;
validate routing with an in-memory or local collector test before relying on both
signals. This assessment does not establish successful collector delivery.

LLM judges may eventually help assess subjective explanation quality after calibration
against human labels. Financial findings, authorization, settlement, and retry safety
remain deterministic. Observability delivery must never decide whether to repeat a
payment or change a verdict.
