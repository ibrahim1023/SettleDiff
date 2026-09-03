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

## Real incident: advertised Base, executed Tempo, vendor rejected payment

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

The contract, execution, settlement, service result, and activity record agree.

### `PAID_FAILURE`

Settlement is proven, but the purchased operation failed.

### `UNVERIFIABLE`

The available evidence is incomplete or contradictory enough that settlement or execution
truth cannot be established safely. This is an intentional product behavior — refusing to
guess is the correct outcome when evidence cannot carry the conclusion.

## What SettleDiff detects

- quoted price or budget disagreements;
- asset, protocol, chain, and recipient inconsistencies;
- missing or ambiguously matched activity records;
- successful financial settlement paired with a failed paid service;
- contradictory settlement outcomes between execution and the persisted activity record;
- explanations that contradict deterministic findings;
- insufficient evidence that makes a run unverifiable.

## Trust model

SettleDiff treats every external source as evidence, not truth.

| Component | Allowed to do | Not allowed to do |
|---|---|---|
| Perflo adapter | capture contract, execution, Activity, transaction evidence | decide final truth |
| Context.dev | retrieve supporting public evidence | alter financial findings |
| Investigation Agent | select evidence, request bounded tools, explain findings | change checks or verdict |
| Deterministic verifier | compare canonical evidence and assign findings | perform paid actions |
| User authorization | approve one exact paid request | authorize retries implicitly |

## Payment-rail boundary

Perflo is SettleDiff's first supported paid-execution adapter.

SettleDiff's core verifier is not Perflo-specific. The domain model operates on canonical
evidence:

- contract;
- execution;
- settlement/receipt;
- service outcome;
- activity record.

A payment integration is responsible for translating rail-specific evidence into those
canonical forms. The application now exposes a rail-neutral adapter contract, and Perflo
implements it as the first adapter. Canonical x402 v2 evidence fields are versioned and
supported by the verifier, and the bounded offline parser/normalizer handles the captured
v2 challenge and specified settlement-response shapes. A versioned external-signer
contract and bounded one-shot subprocess client are implemented; the independently
installed signer owns key loading. The x402 adapter and explicit CLI composition are
implemented and tested offline: two unsigned challenges bracket authorization, the
signer-returned challenge is checked against the selected terms, provider receipt stays
separate from bounded read-only Base Sepolia verification, and the exact USDC transfer
log—not receipt existence—establishes settlement. One authorized `0.001 USDC` controlled
Base Sepolia cycle completed with 12 passing checks and verdict `VERIFIED`; see the
[x402 live-cycle report](docs/testing/x402-live-cycle.md). A separately authorized
GoPlausible public endpoint cycle also produced `VERIFIED` and established compatibility
with a supported EVM primary requirement followed by bounded unsupported alternatives;
see [public endpoint validation](docs/testing/x402-public-endpoint-validation.md). Submission recovery is
read-only: confirmed and reverted receipts both prove transmission, while missing,
pending, malformed, or unavailable evidence remains unresolved. Only explicit
pre-transmission proof can establish non-submission. Direct MPP clients and other payment rails remain
architectural extension points.

## Offline demo scenarios

Every scenario replays deterministically with no credentials, external requests, or spending:

| Fixture | Key condition | Expected verdict |
|---|---|---|
| `clean-success` | all evidence agrees | `VERIFIED` |
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

The paired fixtures demonstrate that equivalent economic evidence produces the same canonical outcome without an adapter-specific verdict branch:

| Outcome | Perflo fixture | x402 fixture |
|---|---|---|
| Clean settlement and service success | `clean-success` → `VERIFIED` | `x402-clean-success` → `VERIFIED` |
| Settlement proven and service failure | `paid-failure` → `PAID_FAILURE` | `x402-paid-failure` → `PAID_FAILURE` |

```bash
uv run settlediff verify-fixture fixtures/clean-success --json
uv run settlediff verify-fixture fixtures/x402-clean-success --json
uv run settlediff verify-fixture fixtures/paid-failure --json
uv run settlediff verify-fixture fixtures/x402-paid-failure --json
```

These commands are offline fixture replay. They do not configure or invoke Perflo, a signer, RPC, Context.dev, Hyperfusion, or a paid resource.

## 60-second fixture demo

```bash
uv sync --locked --all-groups
uv run settlediff verify-fixture fixtures/paid-failure --database /tmp/settlediff-demo.sqlite3
uv run settlediff verify-fixture fixtures/failed-broadcast --database /tmp/settlediff-demo.sqlite3
uv run settlediff show syn_run_failed_broadcast --database /tmp/settlediff-demo.sqlite3
uv run settlediff serve --database /tmp/settlediff-demo.sqlite3
```

The first command prints `PAID_FAILURE`: settlement proven, service failed. The second
prints `UNVERIFIABLE` and is the more interesting case: it was distilled from the real paid
run above, where the advertised chain differed from execution and the vendor replayed a 402
challenge after credential submission. The failed Activity record is matched to its
transaction but is not treated as proof of settlement.

Then open `http://127.0.0.1:8765/runs` to inspect the persisted Expected, Executed, and
Recorded evidence. This demo never contacts a model, Perflo, or a paid service.

For a live call, `settlediff run --url URL --body JSON --budget AMOUNT` retains Perflo as
the temporary default. Select x402 explicitly with `--rail x402 --allow-testnet`; configure
`SETTLEDIFF_X402_SIGNER_COMMAND` as a JSON argument array,
`SETTLEDIFF_X402_RPC_URL`, and `SETTLEDIFF_X402_TESTNET_ENABLED=true`. SettleDiff has no
private-key setting: wallet authority belongs to the separately installed signer. GET uses
`--method GET` with no body; POST requires `--body` and preserves the JSON value exactly.
Remote targets require HTTPS; x402 alone permits HTTP on an IP/hostname proven to be loopback for the controlled local reference cycle. Both rails show the exact target/resource, method, canonical body digest, adapter,
protocol version, scheme, network, full public asset reference and recipient, timeout, quote,
payment-terms digest, and budget before mandatory interactive authorization. Persisted and
ordinary report views remain masked. Environment flags never
bypass confirmation, and live/paid calls are never part of the default test suite.

Before authorization, validate the selected database and live dependencies without signing or paying:

```bash
uv run settlediff doctor --rail perflo --database /path/to/reports.sqlite3
uv run settlediff doctor --rail x402 --database /path/to/reports.sqlite3
```

The x402 signer command must support `--version` and return bounded JSON containing `schema_version: 2` and its public `payer` address. `doctor` also verifies the configured read-only RPC reports Base Sepolia. Signer installation and wallet authority remain independently owned; SettleDiff stores neither the launcher package nor its key.

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
independent verification checks, verdict precedence, fully offline fixture replay, a safe
Perflo subprocess boundary, an explicitly authorized live-run state machine, SQLite report
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
