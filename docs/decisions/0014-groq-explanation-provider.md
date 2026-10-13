# ADR 0014: Groq Explanation Provider

**Status:** Accepted
**Date:** 2026-10-13

## Context

The owner selected Groq and requested removal of the retired provider after live
validation. The factory remains outside deterministic financial checks.

## Decision

Groq is the sole supported explanation provider. Use its
[official OpenAI-compatible endpoint](https://console.groq.com/docs/openai)
through PydanticAI’s native Groq adapter and SDK.

Require `SETTLEDIFF_GROQ_API_KEY` and an explicit `SETTLEDIFF_GROQ_MODEL` when a model
is configured. Validate configuration before provider probing or payment. With no
model configured, use the deterministic fallback. There is no provider selector or
automatic provider switch. API keys remain secret settings and are scrubbed from
operational telemetry.

Use tool-based structured output and temperature zero. Live production-path probes
exposed variable output failures with the smaller model and prompted output; the
accepted setup uses `openai/gpt-oss-120b`. SDK retries remain disabled. Existing
request/tool/token/deadline limits and citation validation remain authoritative.
The model cannot create or alter financial findings.

Remove retired configuration, factory aliases, live tests, metric labels, and
provider-specific documentation. Preserve Git history and existing financial
records. New bundles omit the unused model metadata field. Historical bundles may
retain at most one null metadata entry ending in `_model`, solely to preserve
checksum representation. Other unknown or non-null metadata is rejected; the
retired metadata has no effect on findings, verdicts, or authorization.

## Consequences

- Groq credentials and model availability are required for model explanations.
- The live compatibility probe and two synthetic production-investigator cases pass with the accepted setup.
- These bounded checks do not establish universal explanation quality.
- Default tests and evals block external model requests; live checks require explicit opt-in.
- Model errors cannot authorize a payment retry or change financial truth.

## Verification

Offline tests cover secret handling, incomplete setup, early refusal, fallback,
output mode, retry policy, metrics, and historical bundle integrity. Live checks
cover structured output, tool continuation, grounded explanations of provider-only
and independently confirmed evidence, and report immutability. No payment occurs
in these synthetic model checks.
