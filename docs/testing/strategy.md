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
expected-report.json
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

## Agent-loop tests

Use `FunctionModel` when the exact next tool matters and `TestModel` when tool schema/output wiring is the subject.

Required trajectories:

- missing schema causes `get_service_schema` before verification;
- live execution is followed by Activity retrieval;
- resolved checks stop further evidence collection;
- ambiguous matching may request receipt inspection but cannot choose a candidate itself;
- tool/request limits stop the loop with an explicit incomplete result;
- paid execution is rejected without a matching capability;
- the capability is consumed before calling the adapter;
- a paid failure or uncertain submission never causes another execution tool call;
- explanation references only existing finding and artifact IDs;
- an explanation contradicting the machine verdict is rejected or replaced by a deterministic fallback.

Set `pydantic_ai.models.ALLOW_MODEL_REQUESTS = False` in the default test configuration.

## Adapter contracts

Captured raw envelopes are sanitized and versioned. Contract tests prove:

- stdout JSON and stderr diagnostics remain distinct;
- non-zero exit with a valid Perflo error envelope preserves code, recoverability, details, hint, and submission certainty;
- timeouts terminate the child and preserve an uncertainty-safe error;
- output limits fail without loading unbounded data;
- unexpected fields do not disappear from raw evidence;
- required canonical fields missing from a new envelope become parse errors, not guessed values.

## Interface tests

- Typer `CliRunner` tests exit codes, JSON mode, human summaries, and refusal copy.
- FastAPI `TestClient` tests routes, dependency overrides, loopback-safe defaults, escaping, and raw-artifact expansion.
- CLI and HTTP render the same stored `Report` without recomputing findings.
- Snapshot tests are limited to stable human layouts; machine JSON uses explicit assertions.

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
