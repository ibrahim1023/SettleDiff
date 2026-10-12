# Observability and optional Logfire export

Operational telemetry records how an investigation ran: steps, duration, tool/model
usage, errors, and check outcomes. Financial conclusions remain in the deterministic
machine report. The [eval guide](evals.md) explains how behavior is tested and how a
reviewed failure becomes a regression case.

## Local defaults

The investigation path emits redacted structured JSON logs and OpenTelemetry spans.
Export is off until `SETTLEDIFF_OTLP_ENDPOINT` is configured. Logfire is supported
through the existing OTLP HTTP exporters; no Logfire SDK or hosted account is needed
for local operation or CI. Structured logs remain local; traces and metrics are the
signals exported by this integration.

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

Other OTLP HTTP collectors can use the same endpoint setting and, when needed,
their Authorization header value. Remote endpoints require HTTPS; loopback HTTP
is supported for local collectors.

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

After the trial, configured live investigations use the same private-default export.
Remove the endpoint and authorization values to return to local-only diagnostics.
Do not add hosted export to default tests; the integration tests intercept HTTP
from the real exporters and check authentication, signal routes, and redaction.
