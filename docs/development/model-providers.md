# Model providers

SettleDiff supports Groq and Hyperfusion for evidence selection and explanations.
Only deterministic code produces findings and verdicts. Provider selection does
not change payment authorization, evidence handling, or retry safety.
[ADR 0014](../decisions/0014-groq-explanation-provider.md) records the decision.

## Groq setup

Put these settings in the ignored `.env` file:

```dotenv
SETTLEDIFF_MODEL_PROVIDER=groq
SETTLEDIFF_GROQ_API_KEY=your-groq-api-key
SETTLEDIFF_GROQ_MODEL=openai/gpt-oss-20b
```

The endpoint is `https://api.groq.com/openai/v1`, using Groq's
[official OpenAI compatibility](https://console.groq.com/docs/openai) through the
existing client dependency. SDK retries are disabled. `SETTLEDIFF_GROQ_TIMEOUT_SECONDS`
can override the 30-second client timeout within the supported 1–300 second range.
The investigation's application deadline and request/tool/token limits still apply.

Select a currently available tool-capable model from the
[Groq model catalog](https://console.groq.com/docs/models). Availability can depend
on the account: the current account rejected `llama-3.3-70b-versatile`, while its
catalog included `openai/gpt-oss-20b`. Model IDs are configuration, not hardcoded
provider defaults.

## Hyperfusion compatibility

Existing installations default to Hyperfusion if no provider is selected. To use it
explicitly, configure:

```dotenv
SETTLEDIFF_MODEL_PROVIDER=hyperfusion
SETTLEDIFF_HYPERFUSION_BASE_URL=your-chat-completions-base-url
SETTLEDIFF_HYPERFUSION_API_KEY=your-hyperfusion-api-key
SETTLEDIFF_HYPERFUSION_MODEL=your-model-id
```

The factory keeps its old `build_hyperfusion_model` import as a compatibility alias.
There is no automatic switch to another provider on errors. The current Hyperfusion
credentials returned HTTP 401 on 2026-10-13; this does not establish that the
provider itself lacks compatibility.

## Missing configuration and privacy

If the selected provider has no model configured, SettleDiff uses its existing
fallback explanation without contacting either provider. If a model is selected
but required configuration is incomplete, the CLI refuses before provider probing
or payment. An authenticated model request may still fail at runtime; its error
cannot change the machine report or authorize another payment.

API keys use `SecretStr` and are scrubbed from diagnostics. Groq usage metrics carry
the bounded `groq` provider label. Content capture remains disabled. Live
investigations and the Groq probe require the existing authenticated telemetry setup.
Default tests and evals make no live model request.

## Explicit live compatibility probe

```bash
SETTLEDIFF_LIVE_GROQ=1 uv run pytest tests/contract/test_groq_live.py -m live_groq -q
```

For Hyperfusion, use its separate gate:

```bash
SETTLEDIFF_LIVE_HYPERFUSION=1 uv run pytest tests/contract/test_hyperfusion_live.py -m live_hyperfusion -q
```

The shared contract verifies exact structured output, one local echo tool call,
and continuation using its opaque returned token. It permits at most three model
requests, one tool call, and 256 output tokens per request (768 aggregate), with
bounded input-token usage. Prompts and tool data are synthetic. It makes no
Perflo, Context.dev, signer, RPC, or payment call. Running it may consume model API
credits; it is not part of ordinary CI.

On 2026-10-13, the Groq contract passed with `openai/gpt-oss-20b`. This verifies the
protocol features required by the investigator. It is a bounded compatibility
result, not a benchmark of explanation quality or a production-readiness claim.
See the [eval guide](evals.md) for behavioral grading and regression cases.
