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

The suite contains three checks:

- Structured output and one local echo-tool continuation: at most three model
  requests, one tool call, 256 output tokens per request, and 768 aggregate output tokens.
- Production investigator on provider-only evidence: preserves `UNVERIFIABLE`,
  returns a grounded explanation, and leaves the report unchanged.
- Production investigator on independently confirmed evidence: preserves `VERIFIED`,
  returns a grounded explanation, and leaves the report unchanged.

Each production case uses the existing four-request, six-tool, 8,000-input-token,
1,000-output-token limits and 20-second deadline. All reports and tool data are
synthetic. No Perflo, Context.dev, signer, RPC, or payment tool is available.
Running the checks may consume model API credits; they require the explicit gate
and are excluded from ordinary CI.

On 2026-10-13, all three checks passed with the accepted setup. Earlier attempts
exposed output/tool failures with a smaller model and another output mode; those
attempts were not counted as passes. This is bounded validation, not a capability
benchmark. See the [eval guide](evals.md) for broader behavioral regression testing.
