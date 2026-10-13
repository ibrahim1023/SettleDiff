# ADR 0014: Groq as an Explicit Explanation Provider

**Status:** Accepted
**Date:** 2026-10-13

## Context

The live Hyperfusion provider rejected the configured credentials. The owner
requested Groq support and supplied a Groq key. ADR 0002 already places the model
behind a Chat Completions factory and reserves financial truth for deterministic code.

## Decision

Extend ADR 0002 with explicit `SETTLEDIFF_MODEL_PROVIDER=groq|hyperfusion` selection.
Preserve Hyperfusion as the configuration default for existing installations; the
project's current ignored local configuration selects Groq. Never silently switch
providers after an authentication error or an exhausted budget.

Use Groq's [official OpenAI-compatible endpoint](https://console.groq.com/docs/openai)
through the existing OpenAI client dependency. Add no Groq SDK. Both providers use
one validated Chat Completions configuration and factory, SDK retries disabled,
typed tools, prompted structured output, existing request/token limits, citation
validation, and deterministic fallback. Keep the old Hyperfusion factory import
as a compatibility alias.

A selected model requires complete credentials before live provider probing or
payment. If the selected provider has no model configured, use the existing
fallback explanation without contacting another provider. Credentials remain
`SecretStr` values and are scrubbed by the telemetry privacy policy.

Model metrics carry the selected provider's bounded label; Groq usage must not be
attributed to Hyperfusion. Financial models, verdicts, bundle schemas, and payment
authorization remain unchanged. Existing bundle compatibility metadata retains its
legacy Hyperfusion field without inventing a Groq value in that field.

## Consequences

- Groq setup needs an explicit model ID and key; model availability is account-specific.
- `openai/gpt-oss-20b` passed the bounded live compatibility probe on 2026-10-13.
- Compatibility proves structured output and synthetic tool continuation, not broad explanation quality.
- Live probes require explicit opt-in flags and remain excluded from default CI.
- Model errors never authorize a payment retry or change deterministic findings.

## Verification

Offline tests cover complete/incomplete configuration, secret-safe representations,
provider selection, fallback isolation, early refusal, SDK retry policy, and correct
telemetry labels. A shared live contract caps the two synthetic checks at three
model requests, one local echo tool, and 256 output tokens per request, with a
768-token aggregate output limit. It uses no external paid tool or payment rail.
