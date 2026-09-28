# SettleDiff

**Transaction forensics for agent purchases.**

AI agents can spend real money, but the payment layer, vendor, execution path, and
activity ledger can disagree about what actually happened.

SettleDiff independently reconstructs a paid agent purchase across:

```text
intent → advertised contract → execution → settlement → service result → activity record
```

It then runs deterministic consistency checks and returns one of a small set of
evidence-backed verdicts.

The LLM may gather and explain evidence. It cannot decide financial truth.

## Original live incident: advertised Base, executed Tempo, vendor rejected payment

During the first live paid test cycle against a real Perflo/MPP vendor, SettleDiff
observed:

| Layer | Observed evidence | Result |
|---|---|---|
| Advertised chain | `base` | expected |
| Executed chain | `tempo` | `DIFF` |
| Vendor response | HTTP `402 Payment Required` after credential submission | `FAIL` |
| Activity record | `broadcast_failed` | matched |
| Charge | none confirmed | `UNKNOWN` |
| Transaction hash | absent | `UNKNOWN` |
| Settlement | could not be established | `UNVERIFIABLE` |

SettleDiff did not infer a successful payment from the presence of an Activity record.
A failed Activity record proved that an attempt was recorded, but not that money settled.

**Final verdict: `UNVERIFIABLE`**

> **Historical incident.** This discrepancy was reproduced again on 2026-09-07. By
> 2026-09-08, Perflo's curated contract for the same endpoint advertised `tempo`,
> matching the observed execution path. A fresh live run subsequently completed
> successfully with contract, execution, and Activity aligned on Tempo.

The incident is reproduced offline as a sanitized regression fixture:

```text
$ uv run settlediff verify-fixture fixtures/failed-broadcast
UNVERIFIABLE
UNKNOWN: No execution or matched Activity charge is available.
UNKNOWN: Quoted price or actual charge is unavailable.
PASS: Asset values agree across available evidence.
PASS: Protocol values agree across available evidence.
DIFF: Chain values differ across available evidence.
PASS: Recipient values match.
UNKNOWN: Financial settlement evidence is unavailable.
FAIL: Purchased service returned a non-success HTTP response.
UNKNOWN: Settlement or service outcome is unavailable.
PASS: Persisted Activity and service outcome require no additional consistency warning.
PASS: A deterministic Activity record match was found.
```

Full cycle write-up: [live paid test cycle — 2026-08-21](docs/testing/live-run-report-2026-08-21.md).
Regression fixture: [`fixtures/failed-broadcast/`](fixtures/failed-broadcast/). The raw live
evidence bundle stays local and is never committed.

## Later live validation: contract and execution aligned on Tempo

On 2026-09-08, the same endpoint was inspected again:

```text
POST https://parallelmpp.dev/api/search
```

Perflo's read-only contract advertised `tempo`, USDC, and `$0.01`. SettleDiff bound those
exact terms into a one-use authorization and executed one paid request.

| Layer | Evidence | Result |
|---|---|---|
| Advertised chain | `tempo` | `PASS` |
| Executed chain | `tempo` | `PASS` |
| Activity chain | `tempo` | `PASS` |
| Activity amount | `$0.01` | `PASS` |
| Activity status | `confirmed` | `PASS` |
| Transaction hash | present | `PASS` |
| Budget | `$0.01` authorized / `$0.01` recorded | `PASS` |
| Price | `$0.01` quoted / `$0.01` recorded | `PASS` |
| Service response | successful HTTP response | `PASS` |
| Settlement | established | `PASS` |
| Activity correlation | deterministic match | `PASS` |
| Recipient | representation difference | `WARN` |

**Final verdict: `VERIFIED_WITH_WARNINGS`**

The remaining warning was a recipient representation difference. No chain, price, or budget
disagreement, settlement uncertainty, or Activity-correlation failure remained. The original
incident remains historical evidence; its root cause is not assigned to Perflo, the vendor,
MPP routing, metadata, or another boundary.

## Why this matters

A payment system can report that a request was submitted.
A vendor can report that authorization failed.
An activity ledger can record a failed broadcast.
A chain or protocol field can differ from the advertised contract.

None of those sources alone establish the full truth.

SettleDiff compares them independently and preserves uncertainty instead of collapsing
conflicting evidence into a guessed success/failure state.

## Example verdicts

### `VERIFIED`

The independent settlement observation confirms the authorized transfer, provider evidence does not contradict it, and the required service checks pass.

### `PAID_FAILURE`

Independent evidence confirms settlement, but the purchased operation failed.

### `UNVERIFIABLE`

The available evidence is incomplete or contradictory enough that settlement or execution
truth cannot be established safely. This is an intentional product behavior — refusing to
guess is the correct outcome when evidence cannot carry the conclusion.

## What SettleDiff detects

- quoted price or budget disagreements;
- asset, protocol, chain, and recipient inconsistencies;
- missing or ambiguously matched activity records;
- successful financial settlement paired with a failed paid service;
- contradictions between provider settlement claims and the independent observation;
- explanations that contradict deterministic findings;
- insufficient evidence that makes a run unverifiable.

## Trust model

SettleDiff treats every external source as evidence, not truth.

| Component | Allowed to do | Not allowed to do |
|---|---|---|
| Perflo adapter | capture contract, execution, Activity, transaction evidence as provider assertions | establish independent settlement or decide final truth |
| EVM settlement observer | validate a pre-authorized exact transfer through bounded read-only RPC | infer missing profile fields or accept provider claims as ledger truth |
| Context.dev | retrieve supporting public evidence | alter financial findings |
| Investigation Agent | select evidence, request bounded tools, explain findings | change checks or verdict |
| Deterministic verifier | compare canonical evidence and assign findings | perform paid actions |
| User authorization | approve one exact paid request | authorize retries implicitly |

## Payment-rail boundary

[Perflo](https://perflo.ai) is SettleDiff's first supported paid-execution adapter.

SettleDiff's core verifier is not Perflo-specific. The domain model operates on canonical
evidence:

- contract;
- execution;
- settlement/receipt;
- service outcome;
- activity record.

A payment integration translates rail-specific evidence into canonical forms and labels it
as a `provider_assertion` or `independent_observation`. Perflo `vendor`, `pay`, Activity,
and `tx status` evidence all remain provider assertions. Perflo v8 does not expose the exact
pre-payment settlement profile required by the independent observer, so current schema-4
Perflo reports record `SETTLEMENT_PROFILE_UNAVAILABLE`; a provider-only Perflo success is
`UNVERIFIABLE`.

For x402, the CLI probes signer metadata before authorization and binds its mandatory payer
with the selected requirement into a `SettlementProfile`: CAIP-2 network, chain ID, asset
reference and decimals, atomic amount, recipient, and payer. `PaymentTerms` schema 4 binds
the payer and canonical profile digest. The adapter rebuilds that profile from the second
unsigned challenge before signer launch and refuses drift. The rail-neutral observer in
`observers/evm_rpc.py` and `observers/evm_transfer.py` then validates the chain, receipt,
and exact ERC-20 transfer through bounded read-only calls. It records `CONFIRMED`, `FAILED`,
`INDETERMINATE`, or `UNAVAILABLE`; provider comparison is separately `MATCH`,
`CONTRADICTED`, or `NOT_COMPARABLE`.

Historical authorized x402 cycles remain bounded compatibility evidence; see the
[x402 live-cycle report](docs/testing/x402-live-cycle.md), [public endpoint validation](docs/testing/x402-public-endpoint-validation.md),
and [assurance real-world validation](docs/testing/assurance-real-world-validation-2026-09-22.md).
Submission recovery remains read-only: confirmed and reverted receipts prove transmission,
while missing, pending, malformed, or unavailable evidence remains unresolved. Only
explicit pre-transmission proof can establish non-submission. Direct MPP clients and other
payment rails remain architectural extension points.

## Offline demo scenarios

Every scenario replays deterministically with no credentials, external requests, or spending:

| Fixture | Key condition | Expected verdict |
|---|---|---|
| `clean-success` | all evidence agrees | `VERIFIED` |
| `confirmed-activity-charge` | high-confidence confirmed Activity supplies missing execution charge | `VERIFIED_WITH_WARNINGS` |
| `chain-diff` | advertised vs executed chain differs | `VERIFIED_WITH_WARNINGS` |
| `paid-failure` | settlement proven, service failed | `PAID_FAILURE` |
| `failed-broadcast` | failed 402 replay, no proven charge | `UNVERIFIABLE` |
| `recipient-diff` | recipient mismatch | `VERIFIED_WITH_WARNINGS` |
| `missing-activity` | no reliable Activity match | `UNVERIFIABLE` |
| `ambiguous-activity` | multiple plausible Activity matches | `UNVERIFIABLE` |
| `x402-clean-success` | provider and independent Base Sepolia evidence agree | `VERIFIED` |
| `x402-paid-failure` | x402 settlement confirmed, service returned HTTP 500 | `PAID_FAILURE` |
| `x402-uncertain-submission` | possible transmission, no independent outcome | `UNVERIFIABLE` |
| `x402-provider-success-independent-failure` | provider success contradicts reverted transaction | `UNVERIFIABLE` |
| `x402-provider-failure-independent-confirmation` | provider failure contradicts confirmed transfer | `UNVERIFIABLE` |
| `x402-wrong-{recipient,amount,asset,network}` | one canonical term differs | `VERIFIED_WITH_WARNINGS` |
| `perflo-v8-provider-only-success` | provider reports settlement; independent settlement unavailable | `UNVERIFIABLE` |
| `x402-independent-confirmed` | provider settlement matches an exact independent transfer | `VERIFIED` |

The schema-4 pair makes the trust boundary explicit without an adapter-specific verdict branch:

| Evidence state | Fixture | Expected verdict |
|---|---|---|
| Provider-only settlement claim | `perflo-v8-provider-only-success` | `UNVERIFIABLE` |
| Matching provider claim and independent exact transfer | `x402-independent-confirmed` | `VERIFIED` |

```bash
uv run settlediff verify-fixture fixtures/perflo-v8-provider-only-success --json
uv run settlediff verify-fixture fixtures/x402-independent-confirmed --json
```

Older fixtures intentionally replay with their historical schema-2 meaning. Their persisted
`ledger` fields retain the semantics of that schema; replay does not silently reinterpret or
upgrade them to schema 4.

These commands are offline fixture replay. They do not configure or invoke Perflo, a signer, RPC, Context.dev, Hyperfusion, or a paid resource.

## 60-second fixture demo

```bash
uv sync --locked --all-groups
uv run settlediff verify-fixture fixtures/x402-independent-confirmed --database /tmp/settlediff-demo.sqlite3
uv run settlediff verify-fixture fixtures/perflo-v8-provider-only-success --database /tmp/settlediff-demo.sqlite3
uv run settlediff retry-analysis syn_perflo_v8_provider_only --database /tmp/settlediff-demo.sqlite3
uv run settlediff investigate-purchase syn_x402_independent_confirmed --database /tmp/settlediff-demo.sqlite3
uv run settlediff publish syn_x402_independent_confirmed --database /tmp/settlediff-demo.sqlite3 --output /tmp/settlediff-public
uv run settlediff serve --database /tmp/settlediff-demo.sqlite3
```

The fixture commands print `VERIFIED` and `UNVERIFIABLE` respectively: the x402 report has
a matching exact independent transfer, while the Perflo report has provider settlement only.
`retry-analysis` conservatively classifies
already-persisted evidence and never sends or retries a request. `investigate-purchase`
reconstructs the purchase recap from persisted evidence only and explicitly reports
unavailable sections or bundle when the database lacks full persisted evidence — the bare
`verify-fixture` seed stores the report without every cited artifact. `publish` emits exactly
three masked allowlisted files (`index.html`, `report.json`, `public-manifest.json`). Public
schema 2 includes only settlement/comparison statuses and diagnostics, verification
dimensions, and payer policy; it excludes the observer source, transaction reference,
profile, ledgers, provider Activity, and evidence IDs.

Then open `http://127.0.0.1:8765/runs` to inspect persisted Expected, Executed, and
Independently observed schema-4 evidence plus the Purchase assurance, settlement provenance,
and Evidence timeline panels. Historical reports retain their Recorded column. The
list refreshes from the shared SQLite ledger, distinguishes fixture, controlled-live, and
external-live provenance, and retains active or failed runs before a final report exists.
This demo never contacts a model, Perflo, or a paid service.

The cohesive `test_cross_feature_assurance_demo_remains_offline` in
`tests/integration/test_offline_release.py` — not these seed commands — persists a complete
synthetic evidence set and proves, with all sockets blocked, byte-stable schema-3 bundle
export and verification, deliberate tamper rejection, delivery, timeline, retry, then/now
contract drift, embedded Bazaar comparison, publication masking, and exact function/CLI JSON
agreement. Facilitator comparison is intentionally absent: ADR 0009 defers it because
per-run provenance is not recorded.

For a live call, Perflo remains the default rail and addresses vendors by catalog slug:

```bash
uv run settlediff run --rail perflo --slug synthetic-weather \
  --input '{"city":"Exampleville"}' --query '{"units":"metric"}' \
  --budget 0.05
```

`--input` and `--query` are JSON objects passed to the vendor; `--sub-account` optionally
selects a Perflo sub-account. `--budget` is a major-unit USD amount forwarded to `perflo pay`
as its exact `--max-charge`. The advertised vendor `price`, the vendor's required minimum
`maxChargePerCall`, and the user-authorized maximum charge are three distinct values shown
before confirmation. Immediately after confirmation — and before any paid process launches —
the vendor declaration is read a second time and compared to the authorized contract digest;
drift or malformed evidence stops the run before payment.

Select x402 explicitly with `--rail x402 --allow-testnet`; configure
`SETTLEDIFF_X402_SIGNER_COMMAND` as a JSON argument array,
`SETTLEDIFF_X402_RPC_URL`, and `SETTLEDIFF_X402_TESTNET_ENABLED=true`. SettleDiff has no
private-key setting: wallet authority belongs to the separately installed signer. GET uses
`--method GET` with no body; POST requires `--body` and preserves the JSON value exactly.
Remote targets require HTTPS; x402 alone permits HTTP on an IP/hostname proven to be loopback for the controlled local reference cycle.

Both rails display the exact resource, adapter, protocol version, quote, payment-terms
digest, and budget before mandatory interactive authorization: catalog terms show the slug,
resource digest, vendor contract digest, and all three charge values; x402 HTTP terms also
show the signer-probed payer and settlement-profile digest alongside URL, method, canonical
body digest, scheme, network, public asset reference, recipient, and timeout. Perflo's
`vendor`, `pay`, agent Activity, and `tx status` surfaces remain one
provider trust domain: agreement between them is consistency, not independent ledger
verification, and credit-funded results expose no canonical on-chain transaction reference.
Persisted and ordinary report views remain masked. Environment flags never
bypass confirmation, and live/paid calls are never part of the default test suite.

Before authorization, validate the selected database and live dependencies without signing or paying:

```bash
uv run settlediff doctor --rail perflo --database /path/to/reports.sqlite3
uv run settlediff doctor --rail x402 --database /path/to/reports.sqlite3
```

The x402 signer command must support `--version` and return bounded JSON containing `schema_version: 3` and its public `payer` address. `doctor` also verifies the configured read-only RPC reports Base Sepolia. Signer installation and wallet authority remain independently owned; SettleDiff stores neither the launcher package nor its key.

Inspect durable state or classify already-persisted recovery evidence without external calls:

```bash
uv run settlediff inspect RUN_ID --database /path/to/reports.sqlite3
uv run settlediff recover RUN_ID --database /path/to/reports.sqlite3
uv run settlediff retry-analysis RUN_ID --database /path/to/reports.sqlite3
```

`recover` may classify or collect bounded read-only recovery evidence where the rail supports it. `retry-analysis` only interprets already-persisted evidence and can never invoke an adapter, signer, wallet, provider, RPC, capability, or paid request. Neither can turn missing evidence into proof of non-submission.

## Live findings become offline regression tests

SettleDiff does not rely on live vendors for its default test suite. When a real paid run
exposes a new failure mode:

1. preserve the local evidence bundle;
2. sanitize and reduce the scenario;
3. convert it into a deterministic fixture;
4. add regression assertions;
5. keep all default CI offline.

`fixtures/failed-broadcast/` is the first example. Distilled from the 402-replay incident,
it permanently checks that:

- the failed Activity record can match its transaction;
- a failed Activity record is not treated as a confirmed charge;
- chain drift is reported;
- price and budget remain `UNKNOWN` when settlement cannot be proven;
- the overall verdict remains `UNVERIFIABLE`.

## Status

The current package version is **0.1.0** and the project is available under the
[MIT License](LICENSE).

The MVP and x402 second-rail milestone are implemented. They include strict versioned evidence models, exact money
semantics, recursive redaction, bounded provider parsing, deterministic Activity matching,
independent settlement observation and provider comparison, schema-4 report provenance,
verdict precedence, fully offline fixture replay, a safe Perflo subprocess boundary, an
explicitly authorized live-run state machine, SQLite report
storage, and a loopback-only debugger UI. Required live Context.dev evidence and
private-by-default OpenTelemetry are also available. Hyperfusion's opt-in compatibility
probe was revalidated on 2026-08-19 with `openai/gpt-oss-120b`: structured output, tool
calling, and tool-result continuation are compatible with the configured profile.

- LLM provider: Hyperfusion, through its OpenAI-compatible Chat Completions API.
- Agent SDK: PydanticAI, one bounded investigator.
- Trust boundary: the model selects and explains evidence but cannot change findings or verdicts.
- Payment adapters: Perflo CLI and x402 v2 exact/Base-Sepolia/test-USDC through one canonical evidence boundary.
- Default development path: sanitized fixture replay with no paid calls and no live model calls.

The local product specification is intentionally excluded from Git. The approved foundation
is captured in [the production design](docs/superpowers/specs/2026-08-12-production-foundation-design.md).

## Install from a local wheel

After building locally, install the versioned wheel without publishing it:

```bash
uv build
uv tool install --force dist/settlediff-0.1.0-py3-none-any.whl
settlediff --version
```

The version command prints `settlediff 0.1.0`. Build and inspect release artifacts locally
before selecting any public distribution channel; see the
[release checklist](docs/development/release-checklist.md).

## Live configuration and telemetry

Live model use requires `SETTLEDIFF_HYPERFUSION_BASE_URL`,
`SETTLEDIFF_HYPERFUSION_API_KEY`, and `SETTLEDIFF_HYPERFUSION_MODEL`. Default tests never read
these credentials or send model requests.

Every live investigation requires `SETTLEDIFF_CONTEXTDEV_API_KEY`; SettleDiff uses Context.dev's
documented `https://api.context.dev/v1/web/scrape/markdown` endpoint. The request runs when a failed
purchased service returns an HTTPS `status_url`. SettleDiff deterministically records source
reachability and exact evidence presence; Context.dev cannot change findings or verdicts.

The live Context.dev contract is skipped by default. An owner must configure a valid
`SETTLEDIFF_CONTEXTDEV_API_KEY`, supply a safe public `SETTLEDIFF_LIVE_CONTEXTDEV_URL` and an exact
claim known to be present as `SETTLEDIFF_LIVE_CONTEXTDEV_CLAIM`, then explicitly open the gate:

```bash
SETTLEDIFF_LIVE_CONTEXTDEV=1 uv run pytest tests/contract/test_contextdev_live.py -m live_contextdev -q
```

That test makes exactly one `ContextDevClient.verify` call and **consumes one Context.dev credit**.
Do not run it as part of offline verification or without the owner's authorization and inputs. The
[2026-09-02 live compatibility record](docs/testing/contextdev-live-compatibility.md) documents the
observed positive response shape without retaining credentials or raw provider output.

Set `SETTLEDIFF_OTLP_ENDPOINT` to export OpenTelemetry spans. Export is disabled by default.
Prompts, request bodies, tool content, credentials, provider payloads, and local run IDs are not
exported; PydanticAI content capture remains off. Exporter failure cannot change a report.

## Architecture

SettleDiff is agentic where evidence selection benefits from judgment and deterministic
where financial truth requires repeatability. It is a single Python application with a
functional domain core and adapters around external systems:

- `domain`: strict models, normalization, matching, checks, verdicts, and redaction;
- `application`: live-run and fixture-replay use cases;
- `perflo`: first paid-execution adapter — safe subprocess boundary and envelope parsing;
- `x402`: strict v2 parsing, signer contract, bounded RPC, settlement and recovery evidence;
- `agent`: PydanticAI investigator with typed, guarded tools;
- `storage`: local SQLite reports and event timeline;
- `api` and `ui`: FastAPI with server-rendered Jinja/HTMX;
- `telemetry`: optional OpenTelemetry export with sensitive content disabled.

See [Architecture](docs/architecture/overview.md), [ADRs](docs/decisions/README.md), the
[repository map](docs/development/repository-structure.md), and
[local data backup and migration operations](docs/development/local-data.md).

## Development policy

- Product behavior is test-driven and fixture-first.
- Default tests cannot contact Hyperfusion, Perflo, Context.dev, or any paid service.
- Live compatibility and paid smoke tests are explicit opt-in commands.
- Financial values use `Decimal`, never binary floating point.
- Money-moving failures are never retried until submission certainty is resolved.
- Changes are committed in independently reviewable, passing increments.
- Generated-looking filler, unnecessary abstractions, placeholder copy, and other AI slop are rejected.
- Superpowers is not used to execute implementation or fixes; the tracked plan and repository
  verification loops are authoritative.

Repository instructions are in [AGENTS.md](AGENTS.md). Verification gates are in
[Testing](docs/testing/strategy.md), [Evaluation](docs/evaluation/strategy.md),
[Observability](docs/observability/strategy.md), and
[Verification loops](docs/development/verification-loops.md).

## Current priorities

1. Preserve deterministic verification semantics.
2. Expand regression coverage from real incidents.
3. Improve evidence inspection and report readability.
4. Harden the payment-adapter boundary.
5. Validate demand with users building paid agents.
6. Add a third payment rail only when a concrete use case justifies it.

## Documentation

- [Production foundation design](docs/superpowers/specs/2026-08-12-production-foundation-design.md)
- [Architecture](docs/architecture/overview.md)
- [Architecture decisions](docs/decisions/README.md)
- [Repository structure](docs/development/repository-structure.md)
- [Testing strategy](docs/testing/strategy.md)
- [Live paid test cycle — 2026-08-21](docs/testing/live-run-report-2026-08-21.md)
- [Controlled x402 live cycle](docs/testing/x402-live-cycle.md)
- [Durable controlled live cycle](docs/testing/durable-live-cycle-2026-09-03.md)
- [Public x402 endpoint validation](docs/testing/x402-public-endpoint-validation.md)
- [Context.dev live compatibility](docs/testing/contextdev-live-compatibility.md)
- [Release checklist](docs/development/release-checklist.md)
- [Evaluation strategy](docs/evaluation/strategy.md)
- [Observability strategy](docs/observability/strategy.md)
- [Security and data handling](docs/security/data-handling.md)
- [Research sources and practice assessment](docs/research/sources.md)
- [Decisions requiring owner input](docs/development/open-decisions.md)

## Non-goals

SettleDiff does not:

- retry ambiguous money-moving operations automatically;
- infer settlement from a vendor success flag alone;
- treat Activity presence as proof of charge;
- ask an LLM to decide financial truth;
- issue refunds or dispute payments;
- act as a wallet or payment network;
- replace the underlying payment rail;
- serve as a generic agent framework, observability platform, multi-tenant SaaS, or
  fraud-detection system.
