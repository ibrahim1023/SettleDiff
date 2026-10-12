# Observability and evals: current state

Assessment on 2026-10-12. SettleDiff needs observability and already implements the
foundation accepted in [ADR 0006](../decisions/0006-opentelemetry-with-private-defaults.md).
The next useful investment is connecting observed failures to runnable offline cases.

## What exists

`src/settlediff/telemetry/setup.py` provides JSON logs, OpenTelemetry spans, approved
counters and histograms, and OTLP export required for live investigations. PydanticAI content and binary
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

## Implemented eval loop

The former six-name list is now eight runnable cases, including adversarial output
and citation cases. They run the production investigator with fixed evidence and
scripted models, observe actual tools, and exercise authorization with intercepted
writes. Separate graders cover outcome, findings, citations, trajectory, usage,
limits, fallback, evidence coverage, immutability, and payment safety.

Local experiment records include code/data digests and grader results; comparisons
flag changed cases. Reviewed promotion validates a private local evidence bundle
and a separate synthetic reconstruction, then copies only the sanitized fixture and
explicit criteria, retaining a source digest. Promoted cases enter the offline gate.
The [eval guide](evals.md) documents commands and replay limits under
[ADR 0005](../decisions/0005-fixture-first-testing-and-evals.md).

Exported diagnostic spans omit input and tool payloads. They alone cannot reproduce
a financial investigation; reconstruction still requires reviewed fixed evidence
and tool responses. Automated live sampling, live repeated model trials, and
calibrated subjective graders remain future work.

## Backend decision

Live investigations now require authenticated OTLP export; the configured backend
is Logfire. Missing endpoint or authorization stops live preflight before provider
probing or payment. [ADR 0013](../decisions/0013-required-live-observability.md)
supersedes the earlier optional-export decision. The
[observability guide](observability.md) describes configuration and a synthetic trial.
Fixture replay and the offline gate retain in-memory/local diagnostics and require
no hosted dependency.
`SETTLEDIFF_OTLP_ENDPOINT` now denotes the HTTP collector base URL. The telemetry
module derives separate `/v1/traces` and `/v1/metrics` routes, as defined in the
[OTLP specification](https://opentelemetry.io/docs/specs/otlp/#otlphttp-request).
Tests intercept HTTP requests from the real exporters and check both routes.
This establishes exporter routing without asserting successful delivery to a live collector.

LLM judges may eventually help assess subjective explanation quality after calibration
against human labels. Financial findings, authorization, settlement, and retry safety
remain deterministic. Observability delivery must never decide whether to repeat a
payment or change a verdict.
