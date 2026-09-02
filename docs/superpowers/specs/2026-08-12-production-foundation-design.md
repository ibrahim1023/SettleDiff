# SettleDiff Production Foundation Design

**Status:** Historical approved foundation; current behavior is documented in the architecture overview and ADRs
**Date:** 2026-08-12  
**Source specification:** `Product-spec.md`

## Purpose

SettleDiff verifies autonomous purchases without confusing payment settlement with task success. The product has two intentionally separate forms of intelligence:

1. A bounded Investigation Agent selects and gathers relevant evidence.
2. A deterministic verification engine computes every financial finding and the final verdict.

The initial release remains small enough for a focused MVP while establishing boundaries that can survive production use.

## Chosen Approach

Use one Python application with a functional core and an imperative shell:

- Python 3.12, managed and locked with `uv`.
- Pydantic v2 models for all domain boundaries.
- PydanticAI for one Investigation Agent.
- Hyperfusion through an OpenAI-compatible Chat Completions client.
- FastAPI for the local HTTP interface.
- Jinja templates plus minimal HTMX for the local debugger UI.
- Typer for the CLI.
- SQLite for local run, artifact, finding, and trace-index persistence.
- Pytest for unit, contract, integration, and fixture-replay tests.
- Deterministic pytest graders for evidence-only investigation behavior.
- OpenTelemetry-compatible spans and structured logs, disabled for sensitive content by default.

This is a production-shaped MVP, not a distributed production platform. It keeps replaceable adapters around Hyperfusion, Perflo, Context.dev, storage, and telemetry without introducing services or brokers.

## Alternatives Considered

### OpenAI Agents SDK

The SDK is free and MIT-licensed, supports non-OpenAI providers, and has strong tool guardrails. It was not selected because PydanticAI aligns more directly with the existing Pydantic/FastAPI stack, offers provider-neutral OpenAI-compatible model configuration, model-free test doubles, usage limits, eval primitives, and native OpenTelemetry instrumentation in one coherent package.

### Hand-written model/tool loop

A custom loop would minimize dependencies but would recreate structured output validation, tool schema generation, request/tool limits, model test doubles, and instrumentation. That work does not differentiate SettleDiff.

### LangGraph or another graph runtime

Graph runtimes are valuable for long-running, resumable, branching workflows with human interruptions. SettleDiff's MVP is a short bounded investigation. A framework-level graph would add checkpoints and state machinery without a current recovery requirement.

### CrewAI or multi-agent orchestration

The product needs one investigator and one deterministic verifier, not collaborating agent roles. Multiple agents would add cost, latency, context duplication, and more stochastic behavior without improving the verdict.

## System Boundaries

### Domain core

The domain core owns canonical models, normalization, matching, checks, verdict precedence, and redaction rules. It has no dependency on PydanticAI, FastAPI, Typer, SQLite, or subprocess execution.

Key inputs are `PurchaseIntent`, `ExpectedContract`, `ExecutionRecord`, `LedgerRecord`, and `EvidenceArtifact`. The key output is `Report`, containing immutable findings and a machine verdict.

Money is represented with `Decimal` plus an explicit currency or asset. Floats are prohibited at financial boundaries. Timestamps are timezone-aware UTC. Raw provider payloads are preserved separately from normalized values.

### Perflo adapter

The adapter invokes Perflo with `asyncio.create_subprocess_exec`, never a shell string. It supplies arguments as a sequence, captures stdout and stderr separately, enforces timeouts and output-size limits, parses the uniform JSON envelope, and redacts diagnostic data.

The adapter exposes explicit methods for inspection, schema retrieval, one authorized paid execution, and activity retrieval. It never retries a money-moving command after an uncertain submission. An uncertain write is recorded as an evidence state requiring status/history verification, not silently repeated.

### Investigation Agent

The PydanticAI agent receives typed dependencies exposing only approved evidence tools. It has no raw shell, filesystem, database, or unrestricted HTTP access.

The agent may select non-paid evidence tools and request the deterministic verifier. The paid execution tool requires an authorization capability created by the application from the user's command. That capability permits one exact target, request body digest, and maximum budget, and is consumed after one call.

The run enforces all of the following:

- maximum model requests;
- maximum tool calls;
- maximum input and output tokens;
- model timeout and overall investigation deadline;
- at most one paid execution capability;
- no paid retry after a failure or uncertain submission;
- validated `InvestigationResult` output;
- an explanation whose cited finding IDs and artifact IDs must exist.

The agent cannot mutate `Report`, `Finding`, or verdict fields. Its explanation is stored beside, not inside, the deterministic result.

### Hyperfusion model provider

A provider factory constructs PydanticAI's OpenAI-compatible chat model with Hyperfusion's base URL, API key, and configured model ID. Provider configuration is injected; no provider secret or model name is embedded in prompts or source defaults.

Before production implementation is accepted, a no-payment compatibility contract must prove that the chosen Hyperfusion model supports:

- Chat Completions at the configured base URL;
- tool calls with JSON-schema arguments;
- multi-turn tool result continuation;
- structured output compatible with the required Pydantic schema;
- documented timeout, rate-limit, and malformed-response errors;
- usage reporting when the provider supplies it.

Unsupported optional fields are omitted through a model profile rather than retried dynamically with weakened schemas.

### Storage

SQLite stores normalized runs, artifacts, findings, explanations, and a small event timeline. Raw artifacts are JSON blobs with schema versions and redaction metadata. Repository fixtures remain ordinary versioned JSON files and do not depend on SQLite.

Storage is accessed through a repository protocol so the verifier and tests can use an in-memory implementation. Database migrations are explicit and forward-only. WAL mode may be enabled for the local UI, but the database remains a local runtime artifact and is never committed.

### Interfaces

Typer and FastAPI call the same application service. Neither interface contains verification rules or invokes Perflo directly.

The server-rendered UI provides a run list and an Expected/Executed/Recorded detail page. HTMX is limited to status refresh and expandable raw artifacts. There is no separate frontend build, client state store, or duplicated domain model.

## Data Flow

### Live run

1. Validate target, request body, and explicit budget.
2. Create a run and one-use paid-execution authorization.
3. Inspect contract metadata through Perflo.
4. Let the bounded agent select required evidence tools.
5. Execute the authorized paid call at most once.
6. Persist the raw execution envelope before normalization.
7. Collect the service response, receipt, and candidate Activity records.
8. Match Activity deterministically using identifier priority and a confidence result.
9. Run all deterministic checks and compute verdict precedence.
10. Ask the agent for a grounded explanation of existing findings.
11. Validate explanation references and render the same report through CLI and web UI.

### Fixture replay

Fixture replay skips Hyperfusion, Perflo, and all paid behavior. It loads versioned sanitized artifacts, normalizes them, runs matching and verification, and compares the resulting report with expected findings. This is the default development, CI, and demo path.

## Error Model

Errors are typed by boundary and preserved as evidence:

- `InputError`: invalid URL, body, amount, or missing authorization;
- `ProviderError`: Hyperfusion authentication, rate limit, timeout, or invalid model output;
- `PerfloCommandError`: a known Perflo error envelope with recoverability and submission certainty;
- `ArtifactParseError`: provider data cannot be normalized without guessing;
- `MatchError`: no deterministic ledger match or ambiguous candidates;
- `VerificationError`: an internal invariant failed;
- `StorageError`: local persistence failed;
- `InvestigationLimitError`: request, tool, token, cost, or deadline limit reached.

Missing or ambiguous evidence produces `UNVERIFIABLE` or an explicit `UNKNOWN` finding as defined by the check. It never defaults to success.

Money-moving errors follow Perflo's no-double-spend rule: uncertain submission triggers status/history verification. No automatic retry path exists in the application service or agent tools.

## Security and Privacy

- Secrets come from environment-backed settings and are never persisted.
- Subprocess commands use argument arrays, fixed executable resolution, timeouts, and bounded output.
- URLs are validated before the required eligible live fetch; Context.dev remains the only web-evidence adapter in the MVP.
- Raw artifacts are redacted before persistence and again before display.
- Recipient, account, transaction, session, and device identifiers are masked by default.
- Prompt and tool payload capture is disabled in telemetry by default.
- Fixture sanitization is a CI gate.
- The local web server binds to loopback by default.
- The agent has no capability to increase a budget, retry payment, or alter deterministic output.

## Testing and Evaluation

### Deterministic tests

- Unit tests for every normalization rule, matcher tier, check, and verdict precedence rule.
- Property tests for financial comparison invariants, normalization idempotence, and verdict monotonicity.
- Fixture replay for clean success, chain difference, paid failure, recipient representation difference, missing Activity, and ambiguous Activity.
- Adapter contract tests against captured, sanitized Perflo envelopes.
- CLI and FastAPI tests through their public interfaces.

### Agent tests

- `FunctionModel` scripts exact tool-call trajectories for missing schema, Activity retrieval, ambiguous evidence, and maximum-step termination.
- `TestModel` validates tool schemas and structured result wiring.
- `ALLOW_MODEL_REQUESTS=False` prevents accidental inference calls in the normal test suite.
- Tests prove that paid execution requires and consumes authorization, an uncertain write cannot be repeated, and explanations can reference only existing evidence and findings.

### Evals

The eval suite measures investigation behavior, not financial truth. Initial cases grade:

- required evidence coverage;
- unnecessary tool calls;
- prohibited paid retry attempts;
- stopping within configured limits;
- correct escalation to `UNVERIFIABLE` when evidence is insufficient;
- explanation citation validity and contradiction with the machine verdict.

Code-based graders are primary. Model-based grading is deferred until a subjective explanation-quality question demonstrates value and can be calibrated against human review. Live Hyperfusion evals are opt-in and excluded from default CI.

## Observability

Each run has a correlation ID shared across application logs, subprocess spans, PydanticAI spans, matching, verification, storage, and HTTP rendering.

Minimum spans are:

- `settlediff.run`;
- `settlediff.payment_rail.inspect`;
- `settlediff.payment_rail.execute`;
- `settlediff.payment_rail.activity`;
- `settlediff.match_activity`;
- `settlediff.contextdev.verify` when eligible;
- `settlediff.verify`;
- `settlediff.agent.explain`.

Metrics are low-cardinality counters and histograms for run verdict, check status, duration, tool count, model requests, parse failures, ambiguous matches, and provider errors. Wallets, URLs, transaction IDs, prompts, bodies, and run IDs are excluded from metric labels.

OpenTelemetry export is optional. Local structured logs and the stored event timeline remain useful with no hosted observability product.

## Repository Shape

```text
.
├── AGENTS.md
├── Product-spec.md
├── README.md
├── pyproject.toml
├── .env.example
├── .gitignore
├── docs/
│   ├── architecture/
│   ├── decisions/
│   ├── development/
│   ├── evaluation/
│   ├── observability/
│   ├── testing/
│   └── superpowers/
│       ├── plans/
│       └── specs/
├── fixtures/
│   ├── clean-success/
│   ├── chain-diff/
│   ├── paid-failure/
│   └── recipient-diff/
├── src/settlediff/
│   ├── agent/
│   ├── api/
│   ├── application/
│   ├── contextdev/
│   ├── domain/
│   ├── perflo/
│   ├── storage/
│   ├── telemetry/
│   └── ui/
└── tests/
    ├── contract/
    ├── evals/
    ├── fixtures/
    ├── integration/
    └── unit/
```

Implementation files are not created during foundation initialization. Empty source and test directories are also avoided until the first implementation task needs them.

## Delivery Sequence

1. Establish project tooling and domain models.
2. Implement normalization, matching, verification, and fixture replay without an LLM.
3. Implement the safe Perflo subprocess adapter and captured-envelope contracts.
4. Complete the Hyperfusion compatibility spike.
5. Add the bounded PydanticAI investigator and deterministic agent tests.
6. Add the Typer CLI.
7. Add SQLite persistence and the local FastAPI/Jinja UI.
8. Add required Context.dev evidence verification for live investigations.
9. Add opt-in live evals and OpenTelemetry export.
10. Consider ElevenLabs only after core acceptance criteria pass.

## Deferred Decisions

The following choices require owner input before their implementation task begins:

- the exact Hyperfusion base URL and model identifier;
- whether live runs require an interactive confirmation in addition to the explicit CLI budget;
- local report retention duration and whether raw artifacts should be disabled by default;
- the initial deployment target after the local-only MVP.

These decisions do not block documentation or deterministic core planning.
