# Observability and required live export

Operational telemetry records how an investigation ran: steps, duration, tool/model
usage, errors, and check outcomes. Financial conclusions remain in the deterministic
machine report. The [eval guide](evals.md) explains how behavior is tested and how a
reviewed failure becomes a regression case.

## Live requirement and offline workflows

The investigation path emits redacted structured JSON logs and OpenTelemetry spans.
Every live `settlediff run` requires `SETTLEDIFF_OTLP_ENDPOINT` and
`SETTLEDIFF_OTLP_AUTHORIZATION` before provider/signer probing or payment. Configured
live runs export automatically; there is no live opt-out. Logfire uses the existing
OTLP HTTP exporters, without its SDK. Fixture replay, local report inspection, evals,
and CI remain offline and need no hosted account. See
[ADR 0013](../decisions/0013-required-live-observability.md), which supersedes the
optional-export policy in ADR 0006 for live investigations. Structured logs remain
local; traces and metrics are the signals exported by this integration.

PydanticAI prompt, response, tool, and binary-content capture are disabled. Custom
diagnostic attributes are allowlisted; configured secrets are scrubbed, local run
IDs are replaced with salted correlation handles, and metrics use bounded labels.
Raw evidence and financial identifiers remain in their explicit local evidence
workflow rather than becoming debugging payloads.

## Configure Logfire

Create a project and obtain a project write token or an API key with **Send telemetry**
permission. Match the ingestion URL to the project's data region. Logfire documents
standard OTLP ingestion and authorization in its
[alternative-client guide](https://pydantic.dev/docs/logfire/guides/alternative-clients/)
and [API key guide](https://pydantic.dev/docs/logfire/manage/use-api-keys/).

Put these values in the ignored `.env` file:

```dotenv
# US project; for EU use https://logfire-eu.pydantic.dev
SETTLEDIFF_OTLP_ENDPOINT=https://logfire-us.pydantic.dev
SETTLEDIFF_OTLP_AUTHORIZATION=your-project-write-token
```

`SETTLEDIFF_OTLP_AUTHORIZATION` is the exact Authorization header value. For a
Logfire write token, supply the token without adding a Bearer prefix. It is stored
as a secret setting, requires an explicit endpoint, and is excluded from diagnostics.
The telemetry module appends `/v1/traces` and `/v1/metrics`. Use an ingestion base
URL rather than the dashboard URL or public management API URL.

Other authenticated OTLP HTTP collectors can use the same endpoint and
Authorization header settings. Live runs require both. Remote endpoints require
HTTPS; loopback HTTP is supported for local collectors.

## Test before live investigation

First run the synthetic trial entirely in memory:

```bash
uv run python scripts/observability_trial.py
```

The trial replays an existing synthetic fixture, emits run/verification spans and
check metrics, and reports `export_requested: false`. It stays offline even when
the environment already contains an export destination.

Then explicitly send only the synthetic trial to your configured project:

```bash
uv run python scripts/observability_trial.py --export
```

This command makes telemetry HTTP requests but no model, Perflo, Context.dev, signer,
RPC, or payment call. In Logfire, filter the service to `settlediff` and inspect
`settlediff.run`, `settlediff.verify`, `settlediff.runs`, and `settlediff.checks`.
The CLI reports export requested; exporter flushing alone does not confirm that
the destination accepted or indexed the data. Confirm it in the project UI.

After the trial, live investigations use the same privacy-preserving export
automatically. Removing either setting blocks live investigations. Offline workflows
continue to work. Exporter delivery failures after initialization remain best effort
so they cannot interrupt evidence recovery or affect financial findings; required
configuration does not guarantee destination availability or dashboard indexing.
Do not add hosted export to default tests; the integration tests intercept HTTP
from the real exporters and check authentication, signal routes, and redaction.

## Validation record

On 2026-10-12, an explicitly authorized synthetic trial against the configured US
Logfire ingestion endpoint received HTTP 200 for both `/v1/traces` and `/v1/metrics`.
No model, Perflo, Context.dev, signer, RPC, or payment call occurred. This confirms
ingestion acceptance; dashboard indexing was not independently inspected. No token,
project identifier, or raw financial evidence is retained in this record.
