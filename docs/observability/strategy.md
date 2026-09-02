# Observability Strategy

## Goals

Make it possible to reconstruct what SettleDiff did, how long each boundary took, why a run became unverifiable, and whether the agent stayed within limits—without exporting sensitive evidence by default.

## Signals

### Structured logs

JSON logs include timestamp, level, event name, run correlation ID, component, safe status, duration where relevant, and an error class/code. Human CLI output is separate from diagnostic logs.

Never log API keys, environment values, prompts, request bodies, full URLs with query strings, receipts, wallet addresses, transaction/session/device IDs, response bodies, or unredacted tool arguments/results.

### Traces

Minimum span tree:

```text
settlediff.run
├── settlediff.authorize
├── settlediff.payment_rail.inspect
├── settlediff.payment_rail.schema (when required)
├── settlediff.payment_rail.execute
├── settlediff.payment_rail.transaction (when recovering by reference)
├── settlediff.payment_rail.activity
├── settlediff.match_activity
├── settlediff.contextdev.verify (when eligible)
├── settlediff.verify
└── settlediff.agent.explain

CLI post-run
├── settlediff.storage.persist (when a database is selected)
└── settlediff.render
```

PydanticAI emits its own OpenTelemetry GenAI spans when a model is configured. SettleDiff records only safe attributes and does not rename those spans as `settlediff.agent.investigate`.

### Metrics

Counters:

- `settlediff.runs` by mode and final verdict;
- `settlediff.checks` by check name and status;
- `settlediff.provider_errors` by provider and safe error class;
- `settlediff.parse_errors` by artifact type;
- `settlediff.ambiguous_matches` by matcher strategy;
- `settlediff.limit_exceeded` by limit type;
- `settlediff.prohibited_action_blocked` by action class.

Histograms:

- run and component duration;
- model request count;
- tool call count;
- input/output token usage when supplied;
- model cost estimate when supplied;
- activity candidate count.

Metric labels are bounded enums. Run IDs, users, URLs, vendors with unbounded names, wallet/transaction identifiers, error messages, prompts, and model output never become labels. x402 compatibility versions belong in local bundle/diagnostic metadata; resource URLs, payer/recipient addresses, transaction references, and challenge/payment payloads never become metric labels or exported span attributes.

## Correlation

A random run ID exists in local storage and logs. Exported telemetry uses a one-way session-scoped correlation hash so an external backend cannot enumerate local report IDs. Trace IDs, not business identifiers, connect spans.

Artifacts and findings store trace IDs as optional diagnostic links; traces do not store artifacts.

## Privacy defaults

- OTLP export disabled unless configured.
- PydanticAI content capture disabled.
- HTTP header/body capture disabled.
- subprocess stdout/stderr content excluded; only byte counts and parse status recorded.
- local structured logs use redacted summaries.
- debug content requires an explicit short-lived local-only switch and still excludes secrets.

## Error recording

Record typed error class, stable code, recoverability, submission certainty, safe component, and whether the run remained reportable. Exception stack traces are local diagnostics and pass through redaction filters before export.

Expected control flow such as `UNVERIFIABLE`, authorization refusal, or a PydanticAI tool deferral is not marked as a system error.

## Operational views

The first useful views are:

1. verdict distribution and paid-failure count;
2. provider/Perflo error rate and latency;
3. unverifiable reasons;
4. ambiguous match rate;
5. agent request/tool usage against limits;
6. blocked prohibited actions;
7. parse failures by artifact schema version.

No generic dashboard is created until a named operator question requires it.

## Local fallback

With no exporter, the SQLite event timeline and structured log file must still answer:

- which steps ran;
- which evidence artifacts were created;
- which matcher strategy was used;
- which checks produced the verdict;
- which limits were consumed;
- where the run stopped.

## Retention

Sanitized local reports remain until explicit per-run deletion or an owner-applied age purge; SettleDiff performs no background upload or automatic deletion. Raw x402 authorization material is never retained. Telemetry backend retention is outside SettleDiff and must be documented at deployment time.

## Verification

- unit-test redaction filters and safe attribute builders;
- snapshot representative spans with content capture off;
- assert forbidden keys/values never enter log or span exporters;
- test exporter failure does not change report outcome;
- validate metric attributes against explicit enums;
- include a synthetic canary secret in telemetry tests and fail if any exporter receives it.
