# Groq model setup

Groq supplies SettleDiff's bounded investigation and explanation model. Only
deterministic code produces findings and verdicts.
[ADR 0014](../decisions/0014-groq-explanation-provider.md) records the accepted setup.

## Configure

Put these settings in the ignored `.env` file:

```dotenv
SETTLEDIFF_GROQ_API_KEY=your-groq-api-key
SETTLEDIFF_GROQ_MODEL=openai/gpt-oss-120b
```

The endpoint is `https://api.groq.com/openai/v1`, using the
[Groq API](https://console.groq.com/docs/openai) through PydanticAI’s native Groq adapter and SDK. There is no provider selector or automatic switch.
`SETTLEDIFF_GROQ_TIMEOUT_SECONDS` can override the 30-second client timeout within
1–300 seconds. The application deadline and request/tool/token limits still apply.

Use a currently available tool-capable model from the
[Groq catalog](https://console.groq.com/docs/models). The accepted local setup is
`openai/gpt-oss-120b`, tool-based structured output, and temperature zero. The native adapter handles provider-specific tool error responses and token usage.
SDK retries are disabled. Model IDs remain configuration rather than source defaults.

With no model configured, SettleDiff uses its deterministic fallback. A configured
model with incomplete credentials causes refusal before provider probing or payment.
Runtime model failures retain the authoritative machine report and cannot authorize
another payment.

Keys use `SecretStr` and are scrubbed from diagnostics. Model metrics use the bounded
`groq` label. Prompt/tool/body capture remains disabled. Live runs and model probes
require authenticated telemetry; ordinary tests remain offline.

## Explicit live checks

```bash
SETTLEDIFF_LIVE_GROQ=1 uv run pytest tests/contract/test_groq_live.py -m live_groq -q
```

The suite contains one compatibility probe and eight production scenarios, each
repeated three times with an all-of-three pass requirement:

- Structured output and one local echo-tool continuation: at most three model
  requests, one tool call, 256 output tokens per request, and 768 aggregate output tokens.
- Provider-only evidence, independently confirmed settlement, missing Activity,
  ambiguous Activity, uncertain submission, paid service failure, recipient
  disagreement, and amount disagreement: preserve the explicitly expected verdict,
  validate citations, return a provider explanation, and leave the report unchanged.

Each production case uses the existing four-request, six-tool, 8,000-input-token,
1,000-output-token limits and 20-second deadline. All reports and tool data are
synthetic. Independent cases are spaced 20 seconds apart to reduce saturation of
the observed 8,000-token-per-minute provider bucket. No Perflo, Context.dev, signer, RPC, or payment tool is available.
Running the checks may consume model API credits; they require the explicit gate
and are excluded from ordinary CI.

On 2026-10-13, all three checks passed with the accepted setup. Earlier attempts
exposed output/tool failures with a smaller model and another output mode; those
attempts were not counted as passes. This is bounded validation, not a capability
benchmark. See the [eval guide](evals.md) for broader behavioral regression testing.


On 2026-10-14, the expanded live gate did **not** pass. An unpaced run produced six
passes and nineteen failures, including an exact-output compatibility mismatch and
investigator fallbacks. Those failures are not an accuracy estimate: the batch
also exceeded the pace supported by the observed provider token bucket. A paced
production-only, fail-fast run then passed four cases and failed on the first
uncertain-submission case, which used the deterministic fallback. Pacing alone
therefore does not resolve the quality gap. The complete all-of-three suite has
not passed. The accepted configuration remains bounded and preserves financial
truth; the earlier three-check result does not establish broader model reliability.

The quality assertions remain strict. Default CI stays offline; a live fallback
is a failure of this provider-quality gate even when the application safely
retains its report. No SDK retry, payment tool, or relaxed citation/verdict check
was added to get a green result.
