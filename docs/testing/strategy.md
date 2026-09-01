# Testing Strategy

## Objective

Prove financial semantics, evidence matching, safety gates, and interface contracts without spending money or requiring external services. The normal suite must be deterministic enough to serve as a release gate.

## Test layers

| Layer | Purpose | External calls |
|---|---|---|
| Unit | Models, normalization, checks, verdicts, redaction, authorization | None |
| Property | Financial/check invariants and parser stability | None |
| Fixture replay | End-to-end deterministic reports from sanitized artifacts | None |
| Agent loop | Exact tool trajectories, limits, and grounded output | None; `FunctionModel`/`TestModel` |
| Adapter contract | Parse captured Hyperfusion/Perflo envelopes and Context.dev responses | None |
| Interface integration | Typer and FastAPI against in-memory/fake ports | None |
| Live compatibility | Hyperfusion tool/structured-output behavior | Hyperfusion only; explicit opt-in |
| Paid smoke | One tightly budgeted Perflo scenario | Paid; manual explicit opt-in |

## Deterministic core matrix

Every check has pass, fail/diff, and missing/unknown cases where meaningful:

- budget: below, equal, above, missing authorization, currency mismatch;
- price: exact minor units, normalized decimal equality, mismatch, missing quote;
- asset: normalized match, mismatch, unknown asset;
- protocol and chain: two-layer match, three-layer match, disagreement, missing layer;
- recipient: exact match, case-normalized match if valid, representation difference warning, missing value;
- settlement: settled, failed, pending, unknown;
- service: 2xx pass, 4xx/5xx fail, missing status unknown;
- paid failure: settled plus service fail; ensure failed/pending payment does not trigger it;
- activity persistence: strong match, weak match, ambiguous tie, no candidate;
- verdict precedence: payment failure, paid failure, unverifiable, warnings, verified.

## Required properties

Hypothesis should exercise semantic invariants rather than reproduce example tests:

1. Normalizing canonical data twice yields the same result.
2. Equivalent money representations compare equally after normalization.
3. Increasing actual charge cannot improve budget compliance.
4. Adding a new failure cannot improve overall verdict severity.
5. Reordering independent findings cannot change the verdict.
6. Redaction is idempotent and never reveals a longer identifier than its input mask policy permits.
7. Activity candidates with equal top scores never produce a deterministic match.
8. A consumed paid capability can never become usable again.

## Fixture contract

Each fixture directory contains:

```text
manifest.json       schema version, scenario, synthetic marker
intent.json
contract.json
execution.json
activity.json
# optional receipt.json declared by manifest
```

Optional artifacts such as `receipt.json` or `context-evidence.json` are declared by the manifest. Fixtures must use synthetic identifiers, fixed UTC timestamps, and no secrets. The sanitizer test rejects emails, likely API keys, unmasked addresses, private keys, and unexpected high-entropy strings.

Initial scenarios:

- clean success;
- catalog/execution chain difference;
- settled payment with HTTP 400 (`PAID_FAILURE`);
- recipient representation warning;
- missing Activity;
- ambiguous Activity candidates;
- payment failure;
- malformed provider envelope preserved as unverifiable evidence.

The x402 corpus adds complete schema-v2 canonical reports for clean success, confirmed
settlement with service failure, uncertain submission, both directions of
provider/independent settlement contradiction, and recipient/amount/asset/network
differences. These are explicitly synthetic reports modeled from captured protocol
shapes and controlled local outcomes; challenge-only wire payloads remain contract-test
fixtures and are never padded with fabricated execution or ledger evidence.

## Cross-rail contract tests

Semantic-equivalence tests compare canonical economic relations, evidence states, selected financial findings, verdicts, and uncertainty—not provider payloads, references, or nominal test amounts. Clean success, settled-payment/service-failure, and insufficient-settlement cases must remain equivalent across Perflo and x402. AST-based architecture tests prevent domain/application imports of adapter implementations and provider-specific branch terms. Well-formed provider settlement without independent evidence stays unknown, while malformed provider evidence is rejected before financial checks.

## Agent-loop tests

Use `FunctionModel` when the exact next tool matters and `TestModel` when tool schema/output wiring is the subject.

Required trajectories:

- missing schema causes `get_service_schema` before verification;
- live execution is followed by Activity retrieval;
- resolved checks stop further evidence collection;
- ambiguous matching may request receipt inspection but cannot choose a candidate itself;
- tool/request limits stop the loop with an explicit incomplete result;
- paid execution is rejected without a matching capability;
- adapter/version, scheme, network/chain, asset, recipient, quote, timeout, resource, method, body digest, budget, run, and target drift are rejected without consuming the exact capability;
- the capability is consumed with the selected payment terms before calling the adapter;
- a paid failure or uncertain submission never causes another execution tool call;
- explanation references only existing finding and artifact IDs;
- an explanation contradicting the machine verdict is rejected or replaced by a deterministic fallback.

Set `pydantic_ai.models.ALLOW_MODEL_REQUESTS = False` in the default test configuration.

## Adapter contracts

Captured raw envelopes are sanitized and versioned. Application contract tests also
prove that a non-Perflo adapter can provide typed evidence without exposing a provider
envelope, optional schema/transaction capabilities are not forced onto every adapter,
and mislabeled adapter/artifact evidence fails closed. Provider contract tests prove:

- stdout JSON and stderr diagnostics remain distinct;
- non-zero exit with a valid Perflo error envelope preserves code, recoverability, details, hint, and submission certainty;
- timeouts terminate the child and preserve an uncertainty-safe error;
- output limits fail without loading unbounded data;
- unexpected fields do not disappear from raw evidence;
- required canonical fields missing from a new envelope become parse errors, not guessed values;
- x402 v2 headers enforce encoded/decoded/depth limits and strict `exact`, Base Sepolia,
  address, amount, resource URL, and settlement-outcome contracts;
- signed x402 authorization headers and nested payment payloads are redacted before any
  persistence boundary;
- the external signer client uses one shell-free invocation, bounded stdin/stdout/stderr,
  a controlled environment without wallet keys, and uncertainty-safe timeout, malformed,
  secret-bearing, oversized, and post-launch failure behavior;
- x402 independent settlement requires the expected Base Sepolia chain ID and exactly
  one matching USDC transfer log; receipt existence, facilitator transaction sender,
  wrong token/payer/recipient/amount, malformed logs, and missing/pending receipts are
  covered explicitly;
- the RPC client permits only `eth_chainId` and `eth_getTransactionReceipt`, enforces
  request/response limits, and does not retry or poll;
- x402 recovery covers not submitted, proven not submitted, submitted confirmed,
  submission uncertain with and without a transaction reference, mined reverts,
  missing receipts, invalid evidence, and RPC failure;
- confirmed and reverted receipts classify as submitted; only explicit non-submission
  proof classifies as not submitted, and all ambiguous trajectories remain unresolved;
- every post-launch signer failure is one-shot and cannot be launched again.

## Interface tests

- Typer `CliRunner` tests exit codes, JSON mode, human summaries, and refusal copy.
- FastAPI `TestClient` tests routes, dependency overrides, loopback-safe defaults, escaping, and raw-artifact expansion.
- CLI and HTTP render the same stored `Report` without recomputing findings.
- Snapshot tests are limited to stable human layouts; machine JSON uses explicit assertions.

## Live compatibility evidence

Live tests are opt-in, and money-moving operations never run automatically in CI.
The first live paid cycle is recorded in
[live-run-report-2026-08-21](live-run-report-2026-08-21.md); every materially new
failure mode it exposed was distilled into a sanitized offline fixture (for example
`fixtures/failed-broadcast/`). Raw live evidence bundles remain local and untracked.

## Lessons from the first live cycle

Live providers exposed behavior not represented by initial fixtures:

- response-envelope aliases;
- missing timestamps;
- minor-unit budget requirements;
- failed Activity records that must not count as charges;
- transaction hash aliases;
- chain disagreement between advertised and executed evidence;
- upstream 402 replay after credential submission;
- longer Context.dev cold-scrape latency.

The testing policy is therefore:

1. never trust provider shape stability;
2. preserve raw payloads before normalization;
3. treat missing data as unknown rather than synthesizing values;
4. require confirmed charge evidence before financial conclusions;
5. convert every materially new live failure mode into an offline regression fixture.

## When a new paid test is justified

Do not add live paid tests merely to collect failures. A new paid test is justified
only if it answers a specific question:

- does a new Perflo version change the contract;
- does a new payment rail map correctly into canonical evidence;
- can a successful settlement be independently correlated;
- can a known `PAID_FAILURE` be reproduced against a real vendor;
- does a fix require live compatibility verification.

## Planned commands

```bash
# Fast focused loop
uv run pytest tests/unit/path/test_file.py::test_case -q

# Offline quality gate
uv run ruff format --check .
uv run ruff check .
uv run pyright
uv run pytest -m "not live and not paid" --cov=settlediff --cov-report=term-missing

# Fixture replay gate
uv run settlediff verify-fixture fixtures/paid-failure --json

# Explicit unpaid provider compatibility
SETTLEDIFF_LIVE_HYPERFUSION=1 uv run pytest -m live_hyperfusion

# Manual paid smoke; never CI
SETTLEDIFF_ALLOW_PAID_TEST=1 uv run pytest -m paid --maxfail=1
```

The paid marker additionally requires a test-specific maximum budget and interactive confirmation; the environment flag alone is insufficient.

## Coverage policy

Coverage is a diagnostic, not the target. The release gate requires complete branch coverage for verdict precedence, paid authorization, submission uncertainty, and redaction modules. Other modules should not regress below an agreed baseline established after Phase 2; meaningless assertions added only to raise coverage are AI slop.
