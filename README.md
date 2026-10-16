# SettleDiff

**Independent assurance for purchases made by AI agents.**

SettleDiff investigates what an agent intended to buy, what a provider advertised,
what was charged, whether payment settled, and what the service returned:

```text
intent → advertised contract → execution → settlement → delivery → activity history
```

It keeps each source separate and produces deterministic findings and a verdict.
An investigation agent may select and explain evidence; it cannot change findings
or decide financial truth.

- A paid attempt requires interactive, one-use authorization for the exact request,
  terms, and budget. Changed terms block execution.
- Uncertain payments are never retried automatically.
- Missing or contradictory settlement evidence produces `UNVERIFIABLE`.

SettleDiff is a local developer tool: a CLI, a loopback UI, and SQLite evidence
records. Use it to investigate payment/delivery disagreements, inspect retry
safety, and share masked reports with supporting private evidence bundles.

**Release status:** `0.1.0rc1` is an MIT-licensed release candidate. Its recipient
handoff is prepared, but the GitHub release remains an unpublished draft. SettleDiff
is available from source; it is not on a package registry or offered as a hosted service.

Start with the [offline demo](#try-it-offline), or follow the
[recipient trial guide](docs/releases/0.1.0rc1-recipient.md) to verify a candidate
provided by a maintainer. [Live investigations](#before-a-live-investigation)
require additional setup and exact-request payment authorization.

## Try it offline

Requires Python 3.12 or 3.13 and [uv](https://docs.astral.sh/uv/). Install from source:

```bash
git clone https://github.com/ibrahim1023/SettleDiff.git
cd SettleDiff
uv sync --locked --all-groups
uv run settlediff --help
```

Installation may use the network. These synthetic demos make no provider, model,
telemetry, or payment calls and need no credentials:

```bash
uv run settlediff verify-fixture fixtures/x402-independent-confirmed --json
uv run settlediff verify-fixture fixtures/perflo-v8-provider-only-success --json
```

Selected fields from the first command's JSON output:

```json
{
  "run_id": "syn_x402_independent_confirmed",
  "execution": {
    "upstream_http_status": 200,
    "charge": {"amount": "0.001", "unit": "USDC", "minor_units": null}
  },
  "verdict": "VERIFIED",
  "independent_settlement": {
    "status": "CONFIRMED",
    "diagnostic": "EXACT_TRANSFER_CONFIRMED"
  },
  "settlement_comparison": {"status": "MATCH"}
}
```

The second command returns `UNVERIFIABLE`: Perflo's records agree, but there is no
independent customer-settlement observation. That verdict does not mean the
provider failed to charge or deliver; it describes the evidence available.

To save a replay and inspect it in the local UI:

```bash
mkdir -p .local
uv run settlediff verify-fixture fixtures/x402-independent-confirmed --database .local/settlediff.sqlite3
uv run settlediff serve --database .local/settlediff.sqlite3
```

Open the loopback URL printed by `serve`. The database stays local and ignored by
Git. See the [fixture matrix and testing guide](docs/testing/strategy.md) for more
scenarios, evidence bundles, retry analysis, and masked publication.

## Read the verdict

| Verdict | Meaning |
|---|---|
| `VERIFIED` | Authorized settlement is independently confirmed, provider evidence does not contradict it, and required checks pass |
| `VERIFIED_WITH_WARNINGS` | The essential outcome is verified with non-fatal differences |
| `PAID_FAILURE` | Independent evidence confirms settlement, but the service fails its HTTP or advertised response checks |
| `PAYMENT_FAILURE` | Settlement findings establish payment failure |
| `UNVERIFIABLE` | Evidence is missing, unresolved, ambiguous, or contradictory |

A transaction hash alone does not prove the relevant transfer. A successful HTTP
response alone does not prove the advertised delivery promise was met.

## Supported evidence

| Source | What SettleDiff can establish |
|---|---|
| Perflo v8 catalog, execution, Activity, and transaction status | Provider consistency and disagreements; these records share one provider trust domain |
| x402 v2 `exact` on Base Sepolia test USDC | Independent settlement when a read-only RPC validates the exact authorized chain, asset, amount, recipient, and payer |
| Signed x402 offers and receipts | Seller authenticity, signed terms, key continuity, and transaction correlation; signatures remain provider assertions |
| Signer-owned paid response | Delivery checks against the response contract advertised before payment |
| Context.dev | Supporting public evidence for an eligible failed-service status page |

**Perflo:** current v8 evidence does not expose the complete pre-payment customer
transfer profile needed for independent settlement verification. Provider-only
success remains `UNVERIFIABLE`. An optional Base RPC can corroborate a provider's
vendor-side transaction receipt, but cannot prove the customer's debit. See
[provider-chain corroboration](docs/decisions/0011-perflo-provider-chain-corroboration.md).

**x402:** authorization binds the signer-probed payer and exact settlement profile.
A second unpaid challenge must match before signer launch. After the one attempt,
the observer checks the receipt and exact ERC-20 transfer. Pending receipts,
unavailable RPC evidence, and contradictions remain explicit. See
[independent settlement observation](docs/decisions/0010-independent-settlement-observation.md).

A [public x402 weather purchase on 2026-10-15](docs/testing/public-x402-validation-2026-10-15.md)
returned `VERIFIED`, with an independently confirmed 0.001 test USDC transfer and
13 passing checks. This is a bounded testnet result.

For advertised JWS/Ed25519 offers, SettleDiff verifies the same-origin seller key,
binds the proof into authorization, and rechecks it before payment. It assesses
receipts after normal and uncertain results. Raw JWS strings remain ephemeral;
private reports and bundles retain assessments and digests. Bundle checks cannot
replay cryptographic verification without the raw proof. The
[signed-evidence guide](docs/development/signed-evidence.md) explains the supported
profile and limits. Live inspection verified a signed offer, but a successful live
signed-purchase trial remains outstanding; receipt verification is covered offline.
See the [signed-offer inspection](docs/testing/signed-x402-readiness-2026-10-13.md)
and [unresolved paid-trial record](docs/testing/signed-x402-attempt-2026-10-14.md).

Earlier disagreements and delivery-failure tests are documented in the
[real-world assurance record](docs/testing/assurance-real-world-validation-2026-09-22.md)
and [Perflo v8/x402 validation](docs/testing/pr2-live-validation-2026-09-28.md).
Those dated records retain their original report-schema semantics.

## Authenticate a shared private bundle

Checksums detect changes relative to a known digest. For issuer authentication,
optionally attach an Ed25519 assurance signature to a private bundle.

First follow the [issuer-assurance guide](docs/development/issuer-assurance.md) to
create an encrypted dedicated issuer key. Then replace `RUN_ID` below with a saved
investigation that retained its cited evidence, and export and sign it:

```bash
mkdir -p .local
uv run settlediff export RUN_ID --database .local/settlediff.sqlite3 --output .local/bundle.json
uv run settlediff sign-bundle .local/bundle.json \
  --issuer-key "$HOME/.settlediff-issuer/issuer.pem" --output .local/bundle.assurance.json
```

The quickstart's `verify-fixture --database` saves a report without its cited
artifacts, so it cannot supply this export. Use the
[synthetic recipient example](docs/releases/0.1.0rc1-recipient.md) to try
authentication without a saved investigation.

Share the bundle and detached signature privately. The recipient obtains the
issuer's public key through a separately trusted channel and runs:

```bash
uv run settlediff verify-assurance bundle.json \
  --signature bundle.assurance.json --issuer-public-key trusted-issuer.pub.pem
```

Verification is offline and needs no database, wallet, or private key.
`AUTHENTIC` authenticates issuance and exact artifact bytes; the
financial verdict remains separate. A signature does not prove payment, delivery,
or evidence truth, and is not a ZKP. Default `verify-bundle` stays integrity-only.

[CI tests a separate recipient environment](docs/development/issuer-assurance.md#automated-recipient-check).
One job creates synthetic signed evidence; a fresh job installs the built wheel
and verifies using a fingerprint passed separately from the evidence artifact.
It checks tampering, wrong/revoked issuers, missing signatures, and legacy bundles,
without production keys or live services. This demonstrates portability, not an
independently operated auditor.

Shared-history transparency is [designed but deferred](docs/development/evidence-transparency-design.md):
there is no committed external history verifier or independent witness arrangement.
No public log or SettleDiff ZK policy proof is implemented.

A [synthetic TLSNotary experiment](docs/testing/tlsnotary-synthetic-2026-10-16.md)
authenticated a new local HTTPS session and rejected ten negative cases. It remains
research only: test certificates and a co-hosted notary do not establish independent
production provenance. TLSNotary is not integrated with paid execution or verdicts.

## Before a live investigation

Use [.env.example](.env.example) to populate the ignored `.env` file. Keep
credentials and signer authority out of Git.

- **Every live run:** configure `SETTLEDIFF_CONTEXTDEV_API_KEY` and the Logfire
  endpoint/token described below.
- **Choose a rail:** complete the prerequisites in this table.
- **For live model explanations:** configure `SETTLEDIFF_GROQ_API_KEY` and `SETTLEDIFF_GROQ_MODEL`. Without a
  configured model, use the deterministic fallback. See
  the [model setup and compatibility guide](docs/development/model-providers.md).
- **Before payment:** select the exact request and budget, inspect the displayed
  terms, and approve the one-use authorization. A new attempt needs new approval.

| Rail | Prerequisites |
|---|---|
| Perflo | Compatible Perflo v8 CLI, connected account, and permitted spending |
| x402 | Independently owned signer command in `SETTLEDIFF_X402_SIGNER_COMMAND` (JSON array), Base Sepolia RPC in `SETTLEDIFF_X402_RPC_URL`, `SETTLEDIFF_X402_TESTNET_ENABLED=true`, and `--allow-testnet` on the paid command |

These readiness checks contact external dependencies but do not sign or pay:

```bash
uv run settlediff doctor --rail perflo
uv run settlediff doctor --rail x402
```

Paid investigations use `run --rail perflo` or `run --rail x402` with interactive
confirmation. Read the [paid-boundary verification loop](docs/development/verification-loops.md)
and [release/live setup checklist](docs/development/release-checklist.md) first.
Offline fixture replay, local report inspection, and evals remain credential-free.

## Observability and Logfire

**Authenticated telemetry export is required for live investigations.** Missing
endpoint or token blocks `run` before provider probing or payment. The configured
Logfire destination receives operational traces and bounded metrics: investigation
steps, latency, errors, usage, and check outcomes. Structured logs stay local.

Put your project's ingestion base URL and write token in the ignored `.env`:

```dotenv
SETTLEDIFF_OTLP_ENDPOINT=https://logfire-us.pydantic.dev
SETTLEDIFF_OTLP_AUTHORIZATION=your-project-write-token
```

Use `https://logfire-eu.pydantic.dev` for an EU project. The integration sends the
Authorization header and derives separate trace and metric routes. Prompt/response,
tool-content, binary content, raw evidence, and signatures are excluded from capture.

```bash
# Offline, even when export is configured:
uv run python scripts/observability_trial.py
# Send synthetic traces and metrics; no model request or payment:
uv run python scripts/observability_trial.py --export
```

Synthetic ingestion has received HTTP 200 for both signals; dashboard indexing was
not independently inspected. Live runs export automatically. Exporter outages never
change a financial verdict or trigger a payment retry. The
[observability guide](docs/development/observability.md) covers setup, privacy, and
verification. Offline tests and CI use in-memory or intercepted exporters.

## Agent evals and regression tests

Eight offline scenarios exercise the production investigator: success, missing
evidence, ambiguous Activity, limit exhaustion, authorization mismatch, uncertain
submission, contradictory output, and unknown citations. Graders check tools,
arguments, evidence/citations, limits, usage, fallback, immutable findings, and
payment safety. Writes are intercepted; real model requests are disabled.

```bash
uv run pytest tests/evals tests/unit/agent -q
uv run python scripts/run_evals.py --out .local/evals/before.json
# After changing the agent or its tools:
uv run python scripts/run_evals.py --out .local/evals/after.json --baseline .local/evals/before.json
```

Experiments record code/data digests, trajectories, usage, and grader results.
Changed cases are marked incomparable; any failing grader makes the runner exit
nonzero. Reviewed production failures can become synthetic regression cases without
copying private live payloads. See the [eval guide](docs/development/evals.md) for
criteria, baseline comparison, and promotion.

The live Groq gate now covers eight synthetic production scenarios three times
each. Its latest expanded run found fallback failures; see the
[model guide](docs/development/model-providers.md) for results and limits.

These scripted evals test enforcement and wiring. Live model quality requires
separate opt-in trials and calibrated graders.

## Architecture and project status

The application uses Python, strict Pydantic models, a bounded PydanticAI
investigator, Typer, FastAPI, SQLite,
and OpenTelemetry. Only deterministic code produces financial findings.

The supported x402 profile is narrow; RPC availability and operator trust remain
dependencies. No production facilitator is selected. SettleDiff does not issue
refunds, settle disputes, or automatically retry ambiguous payments.

## Documentation

- [Testing and fixture matrix](docs/testing/strategy.md)
- [Architecture overview](docs/architecture/overview.md) and [decisions](docs/decisions/README.md)
- [Security and data handling](docs/security/data-handling.md) and [issuer assurance](docs/development/issuer-assurance.md)
- [Eval guide](docs/development/evals.md) and [observability guide](docs/development/observability.md)
- [Audit results](docs/development/audit-2026-10-12.md)
- [Shared Codex/Devin skills](skills/matt-pocock/README.md)
- [Contributor instructions](AGENTS.md) and [verification loops](docs/development/verification-loops.md)
- [Release checklist](docs/development/release-checklist.md)
