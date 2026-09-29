# SettleDiff

**Independent assurance for purchases made by AI agents.**

An agent purchase produces claims across several systems:

```text
intent → advertised contract → payment execution → settlement → service delivery → activity history
```

Those claims can agree, disagree, or leave important facts unresolved. SettleDiff
preserves each source separately, compares its claims deterministically, and reports
only what the evidence supports. An LLM may select and explain evidence; it cannot
create findings or decide financial truth.

- Provider records are not silently promoted into independent proof.
- Every paid attempt needs exact, interactive, one-use authorization.
- Uncertain money-moving operations are never retried automatically.
- Missing settlement evidence produces `UNVERIFIABLE`, not a guessed success.

[Run the offline demo](#60-second-offline-demo) to see the trust boundary without
credentials, a signer, or a payment.

## What SettleDiff establishes

| Evidence available | Supported conclusion |
|---|---|
| Provider contract, execution, Activity, and transaction status agree | The provider's records are internally consistent; customer settlement is **not** independently verified |
| An external observer validates the exact pre-authorized transfer | Settlement can be independently confirmed, subject to other findings |
| Independently confirmed settlement and objectively failed delivery | `PAID_FAILURE` |
| Provider and independent observations contradict each other | `UNVERIFIABLE`, with the disagreement preserved |
| Submission may have occurred, but its outcome is unknown | `UNVERIFIABLE`; no automatic retry |

SettleDiff does not replace a payment rail or its logs. For provider teams, it
correlates those logs, makes disagreements inspectable, and adds a separate
verification boundary when the rail exposes enough information before payment.

## Install from source

Python 3.12 or 3.13 and [uv](https://docs.astral.sh/uv/) are required. SettleDiff
is not published to a package registry; the supported evaluation path is the
repository source. Clone and install dependencies (these setup steps may use the
network; the fixture commands below do not contact external services):

```bash
git clone https://github.com/ibrahim1023/SettleDiff.git
cd SettleDiff
uv sync --locked --all-groups
uv run settlediff --help
```

## 60-second offline demo

After setup, these synthetic fixtures need no credentials, signer, or payment:

```bash
uv run settlediff verify-fixture fixtures/perflo-v8-provider-only-success --json
uv run settlediff verify-fixture fixtures/x402-independent-confirmed --json
```

| Scenario (synthetic fixture) | Verdict | Why |
|---|---|---|
| Perflo provider-only success (`perflo-v8-provider-only-success`) | `UNVERIFIABLE` | Provider claims agree, but no independent customer-settlement observation exists |
| Matching x402 exact transfer (`x402-independent-confirmed`) | `VERIFIED` | The observer matches network, asset, amount, recipient, and payer |
| Historical schema-2 x402 HTTP-500 case (`x402-paid-failure`) | `PAID_FAILURE` | The synthetic fixture includes independently settled evidence and failed service |
| Uncertain submission (`x402-uncertain-submission`) | `UNVERIFIABLE` | No authoritative outcome |
| Provider success versus reverted receipt (`x402-provider-success-independent-failure`) | `UNVERIFIABLE` | The contradiction is preserved |

In each JSON response, inspect `verdict`, `independent_settlement`, and
`settlement_comparison`. `UNVERIFIABLE` does **not** mean Perflo failed to charge
or deliver the service. It means the available evidence cannot independently
establish the customer's settlement.

For the local UI, retry analysis, and masked publication, see the
[testing strategy](docs/testing/strategy.md).

## Verdicts

| Verdict | Meaning |
|---|---|
| `VERIFIED` | The authorized settlement is independently confirmed, provider evidence does not contradict it, and required checks pass |
| `VERIFIED_WITH_WARNINGS` | The essential outcome is verified but non-fatal differences remain |
| `PAID_FAILURE` | Independent evidence confirms settlement, but the purchased service fails (HTTP status or an advertised response promise) |
| `PAYMENT_FAILURE` | The settlement finding establishes payment failure |
| `UNVERIFIABLE` | Evidence is missing, unresolved, ambiguous, or contradictory; SettleDiff refuses to guess |

A successful HTTP response alone is not proof of delivery, and a transaction
hash alone is not proof of the relevant transfer. A response promise must be
advertised before payment to support an objective delivery assessment.

## Trust model

| Source | Evidence role |
|---|---|
| Perflo `vendor`, `pay`, Activity, and `tx status` | Provider assertions within one Perflo trust domain; useful for correlation, not independent customer-settlement proof |
| x402 resource and facilitator responses | Provider assertions about terms and settlement |
| Signer-owned bounded paid response | Evidence of what the paid request returned, assessed against an advertised response contract |
| User-configured read-only RPC checking an exact authorized transfer | External settlement observation; configuring an RPC does not establish that its operator is organizationally neutral |
| Context.dev | Supporting public evidence; never financial proof |
| Investigation agent | Selects evidence and explains deterministic findings; cannot alter them |
| Deterministic verifier | Produces checks, delivery assessments, retry classifications, and verdicts |
| User authorization | Approves one exact paid attempt, not an implicit retry |

## Perflo evidence and independent verification

The Perflo v8 adapter captures catalog terms, execution, charges, task state,
Activity, and provider settlement. Those records can corroborate each other and
help investigate failures; being provider assertions does not make them useless.
They do not, however, independently prove a customer's payment.

A Perflo `settlement.txHash` may refer to Perflo's own payment to a vendor rather
than the customer's debit. With an opt-in Base mainnet read-only RPC
(`SETTLEDIFF_PERFLO_RPC_URL`), SettleDiff can check the referenced receipt: a
revert can contradict Perflo's `finalized` claim, but success proves neither the
vendor transfer nor the customer charge. The result stays separate from the
settlement verdict. The [provider-chain decision](docs/decisions/0011-perflo-provider-chain-corroboration.md)
explains this limited check.

Perflo v8 currently does not expose the complete **pre-payment customer transfer
profile** needed to verify payer, recipient, network, token, and exact amount.
Consequently, a current provider-only Perflo success is `UNVERIFIABLE`, not a
payment failure. A fuller integration needs those terms and a verifiable link
between the customer debit and any vendor-side transfer.

## x402 independent verification

For the currently supported x402 v2 exact/Base Sepolia/test-USDC path, SettleDiff
binds the signer-probed payer, recipient, network and chain ID, token contract
and decimals, atomic amount, HTTP resource and method, request-body digest,
timeout, and settlement-profile digest before signing. It observes the unpaid
challenge again before signer launch and refuses changed terms. After execution,
a bounded read-only RPC observer checks the chain receipt and exact transfer
log; provider claims and this observation remain separate.

This is conditional, **not** a claim that every x402 service can be verified:
the pre-payment terms, supported network and asset, signer identity, transaction
reference, and external RPC evidence must be usable. A pending or missing
receipt, malformed evidence, or an unavailable observer cannot establish
settlement. See [ADR 0010](docs/decisions/0010-independent-settlement-observation.md).

## Evidence from real tests

Controlled, explicitly authorized tests exposed both disagreements and successful
checks. Examples include a historical advertised-Base/executed-Tempo mismatch,
a failed broadcast recorded in Activity without proven settlement, a later
run with provider records aligned, an independently confirmed x402 transfer,
a credit-funded Perflo call whose vendor-side hash was not the customer debit,
and independently settled x402 delivery that violated its advertised media type.

The earlier Perflo `VERIFIED_WITH_WARNINGS` run used **historical report schema 2**:
provider Activity then filled the settlement role. It does not satisfy current
independent-settlement rules. The later live HTTP-500 x402 submission lacked a
transaction reference and remains `UNVERIFIABLE`; a **different, separately
authorized** response-contract mismatch proved `PAID_FAILURE`. Neither was
retried. See the dated records for terms, masked observations, and limitations:

- [Initial live paid test cycle](docs/testing/live-run-report-2026-08-21.md)
- [Controlled x402 live cycle](docs/testing/x402-live-cycle.md)
- [Public x402 endpoint validation](docs/testing/x402-public-endpoint-validation.md)
- [Assurance real-world validation](docs/testing/assurance-real-world-validation-2026-09-22.md)
- [Perflo v8 and x402 live validation](docs/testing/pr2-live-validation-2026-09-28.md)

Current-schema fixtures also include `perflo-v8-credit-authorization` (matched
provider Activity, unavailable independent settlement). Older fixtures retain
their historical report semantics; the synthetic HTTP-500 fixture above is not
the later live HTTP-500 run. See the [full fixture matrix](docs/testing/strategy.md).

## Offline, read-only, and paid workflows

**Offline:** `verify-fixture`, `show`, `inspect`, `retry-analysis`,
`verify-bundle`, and the loopback `serve` UI operate on local evidence and need
no credentials or payment. Default tests use offline fixtures; the comprehensive
assurance demo explicitly blocks external sockets. Publication creates masked,
allowlisted static files; raw signatures, payment payloads, and unmasked identifiers
are excluded.

**Read-only live:** `doctor --rail perflo` checks CLI compatibility;
`doctor --rail x402` also probes signer metadata and the configured RPC. Vendor
inspection, unpaid x402 challenge inspection, Activity retrieval, and supported
transaction lookup gather evidence without a paid submission, but may contact
external services. Do not confuse read-only network access with offline replay.

**Paid live:** `run --rail perflo` or `run --rail x402` requires live credentials,
external dependencies, an exact budget, and interactive confirmation for one
request. Authorization binds the resource and request, adapter/version, selected
terms, and budget; a second contract observation must match before payment.
The capability can be consumed only once. If submission is uncertain, SettleDiff
collects read-only recovery evidence and never silently executes again. The x402
path additionally requires explicit testnet gates. Read the
[paid-boundary loop](docs/development/verification-loops.md) before a live run;
no paid command is part of the quick start.

## Architecture at a glance

A local Python application coordinates a Perflo catalog adapter or x402 adapter,
strict canonical evidence models, deterministic matching/checks, and redacted
run records with source-attributed timelines in SQLite. A bounded PydanticAI
investigator may select and explain evidence without determining financial truth.
Typer provides the CLI, FastAPI serves a loopback-only UI, and optional
OpenTelemetry export omits sensitive
content. Reports and bundles preserve provider assertions separately from
qualifying external observations. Public reports use a masked allowlist, not
an unfiltered copy of local evidence.

Schema numbers belong to **different artifact families**. A report schema, a
bundle schema, and a fixture-manifest schema need not have the same version.
The [architecture overview](docs/architecture/overview.md) describes the
interfaces and [ADR index](docs/decisions/README.md) records the boundaries.

## Known limitations

- Perflo provider evidence alone cannot establish independent customer settlement; provider-side receipt corroboration is not proof of the customer's debit.
- Context.dev is supporting evidence only, and live investigations currently require its configuration.
- The x402 exact-transfer observer supports a narrow EVM testnet profile, not every network, asset, or x402 service. Its configured RPC is an availability and operator-trust dependency.
- Live compatibility evidence consists of bounded test cycles, not universal provider compatibility or production approval.
- SettleDiff does not issue refunds, settle disputes, or automatically retry ambiguous payments.

## Project status

SettleDiff is **0.1.0**, MIT-licensed, and available from source. It is not a
public package or hosted service; no production facilitator is selected. See
the [release checklist](docs/development/release-checklist.md) for builds and live setup.

## Documentation

**Start here:** [Testing strategy](docs/testing/strategy.md) ·
[Architecture overview](docs/architecture/overview.md) ·
[Security and data handling](docs/security/data-handling.md)

**Live evidence:** [Initial Perflo incident](docs/testing/live-run-report-2026-08-21.md) ·
[Controlled x402 cycle](docs/testing/x402-live-cycle.md) ·
[Assurance validation](docs/testing/assurance-real-world-validation-2026-09-22.md) ·
[Perflo v8 validation](docs/testing/pr2-live-validation-2026-09-28.md) ·
[Public x402 endpoint](docs/testing/x402-public-endpoint-validation.md)

**Decisions:** [Independent settlement observation](docs/decisions/0010-independent-settlement-observation.md) ·
[Provider-chain corroboration](docs/decisions/0011-perflo-provider-chain-corroboration.md) ·
[Foundation design](docs/superpowers/specs/2026-08-12-production-foundation-design.md) ·
[All ADRs](docs/decisions/README.md)

**Development:** [Repository structure](docs/development/repository-structure.md) ·
[Verification loops](docs/development/verification-loops.md) ·
[Release checklist](docs/development/release-checklist.md) ·
[Contributor instructions](AGENTS.md)

## Non-goals

SettleDiff is not a wallet, payment network, generic agent framework, or
replacement for provider logs. It does not infer a charge from an Activity
entry, a settlement from a success flag, or financial truth from an LLM.
