# SettleDiff MVP Implementation Plan

> **For implementers:** Execute this plan directly using the repository's ADRs, tests, and verification loops. Do not invoke Superpowers skills for implementation or fixes. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a local developer tool that performs one authorized Perflo purchase investigation, deterministically verifies its evidence, and renders the same report through a CLI and local web UI.

**Architecture:** A pure domain core owns normalization, matching, checks, and verdicts. Application services coordinate replaceable Perflo, Hyperfusion/PydanticAI, Context.dev, storage, and telemetry adapters. The single bounded agent may select evidence and explain immutable findings but cannot execute payment without a one-use capability or alter the machine report.

**Tech Stack:** Python 3.12, uv, Pydantic v2, PydanticAI with Hyperfusion's OpenAI-compatible Chat Completions API, Typer, FastAPI, Jinja, minimal vendored HTMX, SQLite, pytest, Hypothesis, Ruff, Pyright, Pydantic Evals, and optional OpenTelemetry.

## Global Constraints

- Product implementation must follow the accepted ADRs under `docs/decisions/`.
- `Product-spec.md` and `task.md` are local-only inputs and must never be staged or committed.
- Only deterministic code produces findings and verdicts.
- A live paid execution requires one explicit capability bound to run ID, target, canonical body digest, and budget.
- A money-moving command is never automatically retried after an error or uncertain submission.
- Default tests and evals make no Hyperfusion, Perflo, Context.dev, or paid calls.
- Money uses `Decimal` and explicit units; timestamps are aware UTC.
- Sensitive content is redacted before persistence, prompting, display, logging, or telemetry.
- Implement test-first and commit each task in a coherent, passing increment.
- Apply the anti-slop review in `docs/development/verification-loops.md` to every task.

---

## Planned File Map

```text
pyproject.toml                         project metadata, dependencies, tool configuration
uv.lock                               reproducible dependency resolution
.env.example                          safe configuration names
.github/workflows/ci.yml              locked, offline production gate
scripts/check_docs.py                 local documentation/link/ignore checker
src/settlediff/config.py              environment-backed settings
src/settlediff/domain/models.py       canonical models and enums
src/settlediff/domain/money.py        explicit money parsing/comparison
src/settlediff/domain/normalize.py    raw-to-canonical normalization
src/settlediff/domain/matching.py     deterministic Activity matcher
src/settlediff/domain/checks.py       independent verification checks
src/settlediff/domain/verdict.py      verdict precedence
src/settlediff/domain/redaction.py    safe persistence/display transformations
src/settlediff/application/ports.py   adapter/storage/telemetry protocols
src/settlediff/application/replay.py  fixture replay use case
src/settlediff/application/run.py     live orchestration state machine
src/settlediff/application/auth.py    one-use paid capability
src/settlediff/perflo/client.py       safe subprocess runner
src/settlediff/perflo/parser.py       Perflo envelope parsers
src/settlediff/agent/model.py         Hyperfusion PydanticAI provider factory
src/settlediff/agent/investigator.py  bounded agent definition
src/settlediff/agent/tools.py         typed evidence tools
src/settlediff/agent/grounding.py     explanation citation validation
src/settlediff/storage/sqlite.py      local repository and migrations
src/settlediff/telemetry/setup.py     structured logs and optional OTel
src/settlediff/cli.py                 Typer interface
src/settlediff/api/app.py             FastAPI app factory and routes
src/settlediff/ui/templates/          server-rendered debugger pages
fixtures/*                            sanitized evidence bundles
tests/*                               unit, contract, fixture, integration, and eval suites
```

Each file has one primary reason to change. Concrete files are created in the task that first consumes them.

---

### Task 1: Project Tooling and Offline Safety Gate

**Files:**
- Create: `pyproject.toml`
- Create: `.python-version`
- Create: `.env.example`
- Create: `.github/workflows/ci.yml`
- Create: `scripts/check_docs.py`
- Create: `tests/conftest.py`
- Modify: `.gitignore`
- Modify: `README.md`

**Interfaces:**
- Produces: installed `settlediff` package, `settlediff` console entry point, pytest markers `live_hyperfusion` and `paid`, and a global block on real PydanticAI model requests.

- [ ] **Step 1: Write the tooling contract test**

Create `tests/test_project_contract.py`:

```python
from pathlib import Path


def test_local_product_inputs_are_ignored() -> None:
    ignore = Path('.gitignore').read_text()
    assert 'Product-spec.md' in ignore
    assert 'task.md' in ignore


def test_environment_example_contains_no_values() -> None:
    lines = Path('.env.example').read_text().splitlines()
    assignments = [line for line in lines if line and not line.startswith('#')]
    assert assignments
    assert all(line.endswith('=') for line in assignments)
```

- [ ] **Step 2: Run the test and confirm the missing environment file fails**

Run: `python3 -m pytest tests/test_project_contract.py -q`
Expected: failure because `.env.example` does not exist.

- [ ] **Step 3: Define the locked Python project**

Create `pyproject.toml` with Python `>=3.12,<3.14`, a `src` layout, and the console script `settlediff = "settlediff.cli:app"`. Add bounded major-version ranges for Pydantic, pydantic-settings, `pydantic-ai-slim[openai]`, Typer, FastAPI, Jinja2, and HTTPX. Add a `dev` dependency group containing pytest, pytest-cov, pytest-asyncio, Hypothesis, Ruff, Pyright, inline-snapshot, and dirty-equals. Configure strict Pyright, Ruff formatting/linting, pytest markers, and coverage source in this single file.

Create `.python-version` containing `3.12` and `.env.example` containing only:

```dotenv
SETTLEDIFF_HYPERFUSION_BASE_URL=
SETTLEDIFF_HYPERFUSION_API_KEY=
SETTLEDIFF_HYPERFUSION_MODEL=
SETTLEDIFF_DATABASE_PATH=
SETTLEDIFF_OTLP_ENDPOINT=
SETTLEDIFF_CONTEXTDEV_API_KEY=
```

- [ ] **Step 4: Block networked model calls in ordinary tests**

Create `tests/conftest.py`:

```python
import pytest
from pydantic_ai import models


def pytest_configure() -> None:
    models.ALLOW_MODEL_REQUESTS = False


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        if 'live_hyperfusion' not in item.keywords and 'paid' not in item.keywords:
            item.add_marker(pytest.mark.offline)
```

- [ ] **Step 5: Add the documentation checker**

Implement `scripts/check_docs.py` to fail when a Markdown-relative link target is missing, any ADR lacks Status/Date/Context/Decision/Consequences, `Product-spec.md` or `task.md` is tracked, or banned unfinished markers appear outside quoted anti-slop policy. Use only the standard library so it can run before project sync.

- [ ] **Step 6: Lock and verify tooling**

Run:

```bash
uv lock
uv sync --locked --all-groups
uv run pytest tests/test_project_contract.py -q
uv run ruff format --check .
uv run ruff check .
uv run pyright
uv run python scripts/check_docs.py
```

Expected: all commands pass and `uv.lock` is created.

- [ ] **Step 7: Add the least-privilege offline CI gate**

Create `.github/workflows/ci.yml` for pushes and pull requests. Set top-level `permissions: {contents: read}`, add concurrency cancellation per ref, use Python 3.12, and run the same locked offline commands from Step 6 plus `pytest -m "not live and not paid"`. Pin every third-party action to a verified full-length commit SHA and record its human-readable release in a comment. Do not expose secrets, use `pull_request_target`, or run live/paid markers. Validate the workflow syntax and ensure all commands also pass locally.

- [ ] **Step 8: Commit the tooling increment**

```bash
git add pyproject.toml uv.lock .python-version .env.example .gitignore README.md .github/workflows/ci.yml scripts/check_docs.py tests/conftest.py tests/test_project_contract.py
git commit -m "chore: establish offline development tooling"
```

---

### Task 2: Strict Domain Models, Money, and Redaction

**Files:**
- Create: `src/settlediff/domain/models.py`
- Create: `src/settlediff/domain/money.py`
- Create: `src/settlediff/domain/redaction.py`
- Create: `tests/unit/domain/test_models.py`
- Create: `tests/unit/domain/test_money.py`
- Create: `tests/unit/domain/test_redaction.py`

**Interfaces:**
- Produces: `Money`, `PurchaseIntent`, `ExpectedContract`, `ExecutionRecord`, `LedgerRecord`, `EvidenceArtifact`, `Finding`, `MachineReport`, `InvestigationExplanation`, `Verdict`, `CheckStatus`, and `ArtifactType`.
- Money constructor: `Money(amount: Decimal, unit: str, minor_units: int | None = None)`.
- Redaction: `redact_artifact(artifact: EvidenceArtifact) -> EvidenceArtifact` and `mask_identifier(value: str) -> str`.

- [ ] **Step 1: Write failing strict-model and money tests**

Test that unknown canonical fields are rejected, timestamps without timezones fail, floats fail money validation, `Decimal('0.01')` equals a normalized minor-unit representation, and `Finding` requires artifact citations for observed values.

```python
def test_money_rejects_float() -> None:
    with pytest.raises(ValidationError):
        Money(amount=0.01, unit='USD')


def test_machine_report_is_immutable() -> None:
    report = machine_report_fixture()
    with pytest.raises(ValidationError):
        report.verdict = Verdict.VERIFIED
```

- [ ] **Step 2: Run focused tests and confirm imports fail**

Run: `uv run pytest tests/unit/domain/test_models.py tests/unit/domain/test_money.py -q`
Expected: collection fails because domain modules do not exist.

- [ ] **Step 3: Implement enums and strict immutable models**

Use `ConfigDict(strict=True, extra='forbid', frozen=True)` on canonical models. Give raw artifacts a `schema_version`, stable artifact ID, UTC `collected_at`, source, redaction state, and `data: JsonValue`. Keep `MachineReport` separate from `InvestigationExplanation`.

- [ ] **Step 4: Implement exact money comparison**

Normalize unit casing, prohibit non-finite decimals, and compare only equal units. Provide `Money.is_within(limit: Money) -> bool`; raise `UnitMismatchError` rather than converting currencies.

- [ ] **Step 5: Implement redaction and property tests**

Mask email local parts, long hex/account identifiers, transaction/session/device values under known keys, and secret-like keys recursively. Add Hypothesis tests proving idempotence and that masked output never contains the full original identifier.

- [ ] **Step 6: Run domain quality gates**

Run:

```bash
uv run pytest tests/unit/domain/test_models.py tests/unit/domain/test_money.py tests/unit/domain/test_redaction.py -q
uv run pyright src/settlediff/domain tests/unit/domain
uv run ruff check src/settlediff/domain tests/unit/domain
```

Expected: pass.

- [ ] **Step 7: Commit the domain-types increment**

```bash
git add src/settlediff/domain tests/unit/domain
git commit -m "feat: define strict evidence domain models"
```

---

### Task 3: Provider Normalization

**Files:**
- Create: `src/settlediff/domain/normalize.py`
- Create: `src/settlediff/perflo/parser.py`
- Create: `tests/unit/domain/test_normalize.py`
- Create: `tests/contract/perflo/*.json`
- Create: `tests/contract/test_perflo_parser.py`

**Interfaces:**
- Consumes: domain models from Task 2.
- Produces: `normalize_contract(raw: EvidenceArtifact) -> ExpectedContract`, `normalize_execution(raw: EvidenceArtifact) -> ExecutionRecord`, `normalize_activity(raw: EvidenceArtifact) -> tuple[LedgerRecord, ...]`, and `parse_perflo_envelope(stdout: bytes, stderr: bytes, returncode: int) -> PerfloEnvelope`.

- [ ] **Step 1: Add sanitized captured envelope contracts**

Create minimal synthetic success, paid-service-failure, known CLI refusal, malformed JSON, and schema-evolution envelopes. Every fixture uses `example.invalid`, synthetic hashes, and fixed UTC timestamps.

- [ ] **Step 2: Write parser and normalization failures first**

Assert raw unknown fields remain in `EvidenceArtifact.data`, missing required canonical fields raise `ArtifactParseError` with artifact ID and field path, and known Perflo errors preserve `code`, `recoverable`, `details`, `hint`, and `submission_uncertain`.

- [ ] **Step 3: Run focused contracts and confirm failure**

Run: `uv run pytest tests/unit/domain/test_normalize.py tests/contract/test_perflo_parser.py -q`
Expected: missing parser/normalizer imports.

- [ ] **Step 4: Implement envelope parsing without text scraping**

Decode bounded UTF-8, require one JSON object, validate top-level `ok`, preserve raw stdout/stderr byte counts, and construct typed success/error envelopes. Non-JSON output becomes `PerfloProtocolError`; it is never interpreted with regular expressions.

- [ ] **Step 5: Implement explicit field mapping**

Map documented aliases in one table per artifact type. Normalize protocol/chain/asset casing while preserving original values in the artifact. Unknown enumerations become explicit `unknown` values plus parse notes.

- [ ] **Step 6: Add normalization idempotence properties**

Generate already-canonical records and prove serializing/reloading them does not change normalized values.

- [ ] **Step 7: Verify and commit**

```bash
uv run pytest tests/unit/domain/test_normalize.py tests/contract/test_perflo_parser.py -q
uv run ruff check src/settlediff/domain src/settlediff/perflo tests
uv run pyright src/settlediff/domain src/settlediff/perflo
git add src/settlediff/domain/normalize.py src/settlediff/perflo/parser.py tests
git commit -m "feat: normalize Perflo evidence envelopes"
```

---

### Task 4: Deterministic Activity Matching

**Files:**
- Create: `src/settlediff/domain/matching.py`
- Create: `tests/unit/domain/test_matching.py`
- Create: `tests/unit/domain/test_matching_properties.py`

**Interfaces:**
- Consumes: `ExecutionRecord` and `tuple[LedgerRecord, ...]`.
- Produces: `match_activity(execution, candidates, *, window: timedelta) -> MatchResult`.
- `MatchResult` contains `status`, `strategy`, `confidence`, `matched`, and ordered `candidate_ids`.

- [ ] **Step 1: Write ordered-strategy tests**

Cover exact transaction ID, session-plus-vendor, transaction hash, vendor/amount/time fallback, no candidate, and tied fallback candidates. Assert the agent cannot supply confidence as an input.

- [ ] **Step 2: Run and confirm missing matcher failure**

Run: `uv run pytest tests/unit/domain/test_matching.py -q`.

- [ ] **Step 3: Implement lexicographic strategies**

Evaluate strategies in the documented order. Stop only when exactly one candidate satisfies a strong strategy. For the fallback, score exact vendor and amount plus timestamp distance, but return `AMBIGUOUS` for equal top scores or values outside the configured window.

- [ ] **Step 4: Prove ordering invariance**

Use Hypothesis permutations to prove input order does not change result, matched candidate, strategy, or confidence.

- [ ] **Step 5: Verify and commit**

```bash
uv run pytest tests/unit/domain/test_matching.py tests/unit/domain/test_matching_properties.py -q
git add src/settlediff/domain/matching.py tests/unit/domain/test_matching.py tests/unit/domain/test_matching_properties.py
git commit -m "feat: match activity records deterministically"
```

---

### Task 5: Verification Checks and Verdict Precedence

**Files:**
- Create: `src/settlediff/domain/checks.py`
- Create: `src/settlediff/domain/verdict.py`
- Create: `tests/unit/domain/test_checks.py`
- Create: `tests/unit/domain/test_verdict.py`
- Create: `tests/unit/domain/test_verdict_properties.py`

**Interfaces:**
- Produces: `run_checks(intent, contract, execution, match) -> tuple[Finding, ...]` and `derive_verdict(findings) -> Verdict`.
- Every check is a pure function registered in an explicit ordered tuple.

- [ ] **Step 1: Encode the specification examples as failing tests**

Implement the required named tests for budget, price, asset, protocol, chain, recipient, settlement, HTTP status, paid failure, ledger/outcome consistency, and activity persistence. Add missing-data cases that produce `UNKNOWN` rather than pass.

- [ ] **Step 2: Run and confirm check imports fail**

Run: `uv run pytest tests/unit/domain/test_checks.py tests/unit/domain/test_verdict.py -q`.

- [ ] **Step 3: Implement independent checks**

Each check returns findings with stable IDs, severity, status, expected/observed values, message, and artifact citations. Checks do not call each other. `paid_failure` reads explicit settlement and service findings rather than re-parsing artifacts.

- [ ] **Step 4: Implement precedence**

Use this explicit order:

```python
PRECEDENCE = (
    Verdict.PAYMENT_FAILURE,
    Verdict.PAID_FAILURE,
    Verdict.UNVERIFIABLE,
    Verdict.VERIFIED_WITH_WARNINGS,
    Verdict.VERIFIED,
)
```

Document exact trigger predicates beside tests. Ensure an Activity `confirmed` state never overrides a service failure.

- [ ] **Step 5: Add monotonicity properties**

Prove finding permutation invariance and that adding a failure cannot improve a verdict.

- [ ] **Step 6: Verify and commit**

```bash
uv run pytest tests/unit/domain -q
uv run pyright src/settlediff/domain tests/unit/domain
git add src/settlediff/domain tests/unit/domain
git commit -m "feat: verify purchase evidence and derive verdicts"
```

---

### Task 6: Sanitized Fixtures and Replay Application

**Files:**
- Create: `src/settlediff/application/ports.py`
- Create: `src/settlediff/application/replay.py`
- Create: `fixtures/clean-success/*`
- Create: `fixtures/chain-diff/*`
- Create: `fixtures/paid-failure/*`
- Create: `fixtures/recipient-diff/*`
- Create: `fixtures/missing-activity/*`
- Create: `fixtures/ambiguous-activity/*`
- Create: `tests/fixtures/test_replay.py`
- Create: `tests/fixtures/test_sanitization.py`

**Interfaces:**
- Produces: `replay_fixture(path: Path) -> MachineReport` and `ReportRepository` protocol.

- [ ] **Step 1: Write the fixture manifest schema and replay tests**

Each scenario declares schema version, expected verdict, required artifact filenames, and `synthetic: true`. Assert paid-failure replay returns `PAID_FAILURE` and all expected finding IDs/statuses.

- [ ] **Step 2: Write sanitizer tests before fixtures**

Reject private-key headers, bearer tokens, likely API key prefixes, emails, unmasked 40/64-character hex identifiers, and unexpected high-entropy strings. Allow explicit synthetic identifiers matching `syn_*`.

- [ ] **Step 3: Run and confirm missing fixtures/replay fail**

Run: `uv run pytest tests/fixtures -q`.

- [ ] **Step 4: Add minimal synthetic fixtures**

Create only fields consumed by accepted checks plus raw-envelope metadata needed for parser contracts. Use `example.invalid`, `syn_tx_*`, and fixed 2026 UTC timestamps.

- [ ] **Step 5: Implement replay**

Load and validate the manifest, build evidence artifacts, normalize, match, run checks, derive verdict, and return the report. Replay imports no agent or live adapter module.

- [ ] **Step 6: Verify and commit**

```bash
uv run pytest tests/fixtures tests/unit/domain -q
git add src/settlediff/application fixtures tests/fixtures
git commit -m "feat: replay sanitized verification fixtures"
```

---

### Task 7: Safe Perflo Subprocess Adapter and Paid Capability

**Files:**
- Create: `src/settlediff/application/auth.py`
- Create: `src/settlediff/perflo/client.py`
- Create: `tests/unit/application/test_auth.py`
- Create: `tests/integration/perflo/fake_perflo.py`
- Create: `tests/integration/perflo/test_client.py`

**Interfaces:**
- Produces: `PaidExecutionCapability.issue(...)`, `capability.consume(request)`, and `PerfloClient.inspect_service`, `get_schema`, `execute`, `get_activity`, `transaction_status`.
- `execute` requires a consumed authorization token object that cannot be constructed publicly.

- [ ] **Step 1: Write authorization safety tests**

Cover exact match, target mismatch, canonical body digest mismatch, budget exceedance, reuse, expiration, and cross-run use. Assert capability consumption occurs before adapter invocation.

- [ ] **Step 2: Write fake-executable integration tests**

The fake executable records argv and emits bounded JSON. Test argument preservation for spaces/metacharacters, stream separation, non-zero errors, timeout termination, output limits, and uncertain mutation errors.

- [ ] **Step 3: Run and confirm missing client/auth failures**

Run: `uv run pytest tests/unit/application/test_auth.py tests/integration/perflo/test_client.py -q`.

- [ ] **Step 4: Implement one-use authorization**

Canonicalize JSON with sorted keys and compact separators before SHA-256. Store capability state in a private mutable cell guarded by an `asyncio.Lock`; expose only immutable request/consumption results.

- [ ] **Step 5: Implement subprocess execution**

Use `asyncio.create_subprocess_exec(*argv, stdout=PIPE, stderr=PIPE)` and `asyncio.wait_for(proc.communicate(), timeout)`. On timeout, terminate, wait briefly, then kill if necessary. Never use a shell. Pass a controlled environment containing only required process variables.

- [ ] **Step 6: Implement mutation uncertainty**

Map clean pre-submission refusals to `submission_uncertain=False`. Map timeout/socket/protocol failures after launch to `submission_uncertain=True` unless the Perflo envelope explicitly says otherwise. Do not include an internal retry decorator.

- [ ] **Step 7: Verify and commit**

```bash
uv run pytest tests/unit/application/test_auth.py tests/integration/perflo/test_client.py -q
uv run ruff check src/settlediff/application src/settlediff/perflo tests
git add src/settlediff/application/auth.py src/settlediff/perflo/client.py tests
git commit -m "feat: guard Perflo paid execution boundary"
```

---

### Task 8: Hyperfusion Provider Factory and Compatibility Contract

**Files:**
- Create: `src/settlediff/config.py`
- Create: `src/settlediff/agent/model.py`
- Create: `tests/unit/agent/test_model_factory.py`
- Create: `tests/contract/test_hyperfusion_live.py`

**Interfaces:**
- Produces: `Settings`, `HyperfusionConfig`, and `build_hyperfusion_model(config) -> OpenAIChatModel`.

- [ ] **Step 1: Write settings and factory tests**

Assert live agent configuration fails clearly when base URL, API key, or model ID is absent. Assert the factory constructs `AsyncOpenAI(base_url=..., api_key=...)`, selects Chat Completions, applies the explicit model profile, and never logs the key.

- [ ] **Step 2: Run and confirm missing configuration modules**

Run: `uv run pytest tests/unit/agent/test_model_factory.py -q`.

- [ ] **Step 3: Implement environment-backed configuration**

Use `pydantic-settings` with the `SETTLEDIFF_` prefix and `SecretStr` for keys. Keep live provider config optional until a live command is selected.

- [ ] **Step 4: Implement the provider factory**

Construct an `AsyncOpenAI` client with Hyperfusion base URL, zero SDK mutation retries, explicit HTTP timeout, and redacted logging. Wrap it in `OpenAIProvider`, then `OpenAIChatModel` with the injected model ID. Do not use Responses-specific features.

- [ ] **Step 5: Implement the opt-in live contract**

The marked test skips unless `SETTLEDIFF_LIVE_HYPERFUSION=1`. It verifies one simple structured output, one tool call, one tool-result continuation, usage metadata shape when present, and typed timeout/rate-limit behavior. It performs no Perflo call and no paid service action.

- [ ] **Step 6: Run offline factory tests**

Run: `uv run pytest tests/unit/agent/test_model_factory.py -q`.

- [ ] **Step 7: Run the compatibility contract with owner-provided configuration**

Run: `SETTLEDIFF_LIVE_HYPERFUSION=1 uv run pytest tests/contract/test_hyperfusion_live.py -m live_hyperfusion -q`
Expected: all required compatibility assertions pass for the selected model. If they fail, update only the explicit model profile or select a compatible Hyperfusion model; do not weaken domain schemas.

- [ ] **Step 8: Commit the provider increment**

```bash
git add src/settlediff/config.py src/settlediff/agent/model.py tests/unit/agent/test_model_factory.py tests/contract/test_hyperfusion_live.py
git commit -m "feat: connect PydanticAI to Hyperfusion"
```

---

### Task 9: Bounded Investigation Agent and Grounded Explanation

**Files:**
- Create: `src/settlediff/agent/tools.py`
- Create: `src/settlediff/agent/investigator.py`
- Create: `src/settlediff/agent/grounding.py`
- Create: `tests/unit/agent/test_tools.py`
- Create: `tests/unit/agent/test_investigator.py`
- Create: `tests/unit/agent/test_grounding.py`
- Create: `tests/evals/dataset.py`
- Create: `tests/evals/graders.py`
- Create: `tests/evals/test_regression.py`

**Interfaces:**
- Consumes: application ports, `PaidExecutionCapability`, and Hyperfusion model factory.
- Produces: `InvestigationDependencies`, `InvestigationResult`, `build_investigator(model)`, `investigate(state, deps)`, and `validate_explanation(explanation, report, artifacts)`.

- [ ] **Step 1: Write exact trajectory tests with `FunctionModel`**

Script missing-schema, fetch-activity, ambiguous-match, sufficient-evidence stop, limit exhaustion, unauthorized execute, and prohibited retry trajectories. Capture messages and assert exact tool names and safe arguments.

- [ ] **Step 2: Write grounding failures**

Reject nonexistent finding/artifact IDs, a narrative verdict different from `MachineReport.verdict`, uncited observed claims, and sensitive identifiers.

- [ ] **Step 3: Run and confirm missing agent modules**

Run: `uv run pytest tests/unit/agent tests/evals -q`.

- [ ] **Step 4: Implement typed dependencies and tools**

Dependencies expose narrow callables for service inspection, schema, authorized execution, execution inspection, receipt decode, Activity retrieval, deterministic matching, Context.dev verification, and deterministic checks. Tool return types contain normalized summaries and artifact handles, not unrestricted raw data.

- [ ] **Step 5: Implement the agent**

Use a validated `InvestigationResult` output type. Instructions state evidence-selection responsibilities and immutable verifier authority. Enforce `UsageLimits` for requests, tool calls, input/output tokens, and cost plus an application `asyncio.timeout` deadline. Use a small constant tool set; no dynamic shell or filesystem capability.

- [ ] **Step 6: Implement explanation validation and fallback**

After generation, validate all cited IDs and verdict equality. On validation failure, return a deterministic template built from findings and record the provider output as rejected diagnostic evidence after redaction.

- [ ] **Step 7: Implement code-first evals**

Create balanced cases and graders listed in `docs/evaluation/strategy.md`. Every case has a passing reference trajectory. Report safety separately from quality/efficiency.

- [ ] **Step 8: Verify and commit**

```bash
uv run pytest tests/unit/agent tests/evals -m "not live" -q
uv run pyright src/settlediff/agent tests/unit/agent tests/evals
git add src/settlediff/agent tests/unit/agent tests/evals
git commit -m "feat: investigate evidence with a bounded agent"
```

---

### Task 10: Live Run State Machine and CLI

**Files:**
- Create: `src/settlediff/application/run.py`
- Create: `src/settlediff/cli.py`
- Create: `tests/unit/application/test_run.py`
- Create: `tests/integration/test_cli.py`

**Interfaces:**
- Produces: `RunInvestigation.execute(command) -> InvestigationOutcome`, Typer commands `verify-fixture`, `run`, and `show`.

- [ ] **Step 1: Write state-transition tests**

Cover live success, preflight refusal, execution failure, submission uncertainty, missing Activity, provider explanation failure, and persistence failure after execution. Assert invalid transitions fail and uncertain submission enters evidence-only recovery.

- [ ] **Step 2: Write CLI behavior tests**

Assert fixture replay requires no config, live run requires URL/body/budget, invalid JSON exits before ports are called, JSON mode emits the canonical report schema, and human mode keeps payment status separate from service status.

- [ ] **Step 3: Run and confirm missing application/CLI failures**

Run: `uv run pytest tests/unit/application/test_run.py tests/integration/test_cli.py -q`.

- [ ] **Step 4: Implement explicit state transitions**

Use a small enum and transition table. Persist an event after each accepted transition. Perform deterministic verification even if Hyperfusion explanation fails. Never recompute findings during rendering.

- [ ] **Step 5: Implement Typer commands**

`verify-fixture PATH` calls replay. `run --url --body --budget` validates inputs, displays the exact target/body digest/budget authorization, obtains confirmation when interactive, and invokes the live service. `show RUN_ID` reads a stored report. Add `--json` to all read/report commands.

- [ ] **Step 6: Verify and commit**

```bash
uv run pytest tests/unit/application/test_run.py tests/integration/test_cli.py -q
uv run settlediff verify-fixture fixtures/paid-failure
git add src/settlediff/application/run.py src/settlediff/cli.py tests
git commit -m "feat: orchestrate investigations through the CLI"
```

---

### Task 11: SQLite Repository and Local Debugger UI

**Files:**
- Create: `src/settlediff/storage/sqlite.py`
- Create: `src/settlediff/storage/migrations/001_initial.sql`
- Create: `src/settlediff/api/app.py`
- Create: `src/settlediff/ui/templates/base.html`
- Create: `src/settlediff/ui/templates/runs.html`
- Create: `src/settlediff/ui/templates/run_detail.html`
- Create: `src/settlediff/ui/static/settlediff.css`
- Create: `src/settlediff/ui/static/htmx.min.js`
- Create: `tests/integration/storage/test_sqlite.py`
- Create: `tests/integration/api/test_app.py`

**Interfaces:**
- Produces: concrete `SQLiteReportRepository`, `create_app(settings, services) -> FastAPI`, `GET /runs`, `GET /runs/{run_id}`, and `GET /runs/{run_id}/events`.

- [ ] **Step 1: Write repository contracts**

Use a temporary database to prove migration idempotence, atomic report/artifact/finding persistence, ordered events, schema versions, deletion, and round-trip equality. Assert raw payloads are redacted before insert.

- [ ] **Step 2: Write API tests**

Override repository dependencies and assert list/detail status, 404 behavior, HTML escaping, CSP/security headers, no recomputation, masked identifiers, and expandable raw JSON rendered as text rather than HTML.

- [ ] **Step 3: Run and confirm missing storage/API failures**

Run: `uv run pytest tests/integration/storage tests/integration/api -q`.

- [ ] **Step 4: Implement SQLite persistence**

Use standard `sqlite3` through a repository-owned connection factory. Enable foreign keys and WAL for the local runtime, set a busy timeout, and wrap complete report writes in one transaction. Apply numbered SQL migrations using a schema-version table.

- [ ] **Step 5: Implement the app factory and routes**

Use FastAPI dependency injection for repository/application ports. Bind loopback in the CLI server command. Render only persisted `MachineReport` and `InvestigationExplanation` values.

- [ ] **Step 6: Implement purposeful UI**

Build the Expected/Executed/Recorded comparison as the dominant view. Use restrained typography, semantic status colors, keyboard-accessible details, and no charts, gradients, fake metrics, generic AI imagery, or navigation filler. Vendor a pinned HTMX release with recorded source URL/checksum and use it only for event refresh and raw-artifact expansion.

- [ ] **Step 7: Render and inspect all fixture scenarios**

Start the local app against a temporary fixture database, inspect runs/detail pages at desktop and narrow widths, and capture screenshots as review artifacts outside the repository. Confirm paid failure is visually distinct from payment failure.

- [ ] **Step 8: Verify and commit**

```bash
uv run pytest tests/integration/storage tests/integration/api -q
uv run ruff check src/settlediff/storage src/settlediff/api
git add src/settlediff/storage src/settlediff/api src/settlediff/ui tests
git commit -m "feat: persist and render local investigation reports"
```

---

### Task 12: Required Live Evidence, Telemetry, and Release Gate

**Files:**
- Create: `src/settlediff/contextdev/client.py`
- Create: `src/settlediff/telemetry/setup.py`
- Create: `tests/contract/contextdev/*.json`
- Create: `tests/contract/test_contextdev.py`
- Create: `tests/unit/telemetry/test_redaction.py`
- Create: `tests/integration/test_offline_release.py`
- Modify: `README.md`
- Modify: `.env.example`

**Interfaces:**
- Produces: required live `ContextEvidencePort`, `configure_telemetry(settings)`, and a complete offline release gate.

- [ ] **Step 1: Write Context.dev adapter contracts**

Cover reachable source, unavailable source, unsupported claim, timeout, and malformed response with sanitized captured envelopes. Validate URL through the adapter and return a bounded evidence excerpt plus source metadata.

- [ ] **Step 2: Write telemetry privacy tests**

Send a synthetic canary secret through settings, agent state, tool arguments, subprocess diagnostics, artifacts, findings, and exceptions. Assert a memory log/span exporter never receives it or forbidden key names. Assert exporter failure leaves report output unchanged.

- [ ] **Step 3: Run and confirm missing optional adapters**

Run: `uv run pytest tests/contract/test_contextdev.py tests/unit/telemetry -q`.

- [ ] **Step 4: Implement the single evidence path**

Require Context.dev configuration for every live investigation and fetch only when a failed service result contains an eligible HTTPS status URL. Use the documented Markdown scrape API; do not add semantic fact checking. Determine exact evidence presence in deterministic code.

- [ ] **Step 5: Implement telemetry**

Always provide redacted structured local logs. Configure OpenTelemetry only when an endpoint exists. Instrument SettleDiff spans and enable PydanticAI instrumentation with content capture off. Use enum-only metric labels.

- [ ] **Step 6: Build the offline release test**

Replay every fixture, render CLI JSON/human output, persist/read through SQLite, and request the HTML detail page while network/model calls remain blocked.

- [ ] **Step 7: Update the README to make the 60-second demo truthful**

Remove the planning-stage warning only after the exact documented commands work from a clean checkout. Add configuration for optional live Hyperfusion and required live Context.dev evidence without including secrets or implying that paid smoke tests are routine.

- [ ] **Step 8: Run the complete completion gate**

```bash
uv lock --check
uv run ruff format --check .
uv run ruff check .
uv run pyright
uv run pytest -m "not live and not paid" --cov=settlediff --cov-report=term-missing
uv run python scripts/check_docs.py
git diff --check
git status --short
```

Expected: all checks pass, local product inputs remain ignored, and only intended files are staged.

- [ ] **Step 9: Commit the release-ready MVP increment**

```bash
git add src/settlediff/contextdev src/settlediff/telemetry tests README.md .env.example
git commit -m "feat: complete observable offline MVP"
```

---

## Post-MVP Decision Gates

Do not schedule these as implementation tasks until the corresponding decision is made:

1. ElevenLabs voice demo: only after the core acceptance suite passes.
2. Hosted deployment: requires a new threat model and storage/auth ADR.
3. Durable graph/workflow runtime: requires evidence of long-running pause/resume or crash recovery needs.
4. Model-based explanation grader: requires a human-labeled calibration set and a subjective dimension code graders cannot capture.
5. Additional payment protocols/platforms: require captured contracts and dedicated normalizers, not generic parsing.
