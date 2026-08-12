# SettleDiff

AI agents can spend real money now. A successful payment does not mean a successful task.

SettleDiff investigates a paid agent purchase and compares what was intended, advertised, executed, settled, persisted, and returned by the service. A bounded Investigation Agent gathers evidence; deterministic Python code alone decides financial findings and the final verdict.

```text
intent → contract → execution → receipt → service outcome → activity record
                         │
                         └── deterministic consistency checks
```

## Status

Phases 1 through 3 are implemented: the offline development foundation now includes strict,
versioned evidence models, exact money semantics, recursive redaction, bounded Perflo envelope
parsing, and explicit raw-to-canonical normalization. Deterministic checks and the end-to-end
product workflow are not implemented yet.

- LLM provider: Hyperfusion, through its OpenAI-compatible Chat Completions API.
- Agent SDK: PydanticAI, one bounded investigator.
- Trust boundary: the model selects and explains evidence but cannot change findings or verdicts.
- Primary integration: Perflo CLI.
- Default development path: sanitized fixture replay with no paid calls and no live model calls.

The local product specification is intentionally excluded from Git. The approved foundation is captured in [the production design](docs/superpowers/specs/2026-08-12-production-foundation-design.md).

## What SettleDiff detects

- quoted price or budget disagreements;
- asset, protocol, chain, and recipient inconsistencies;
- missing or ambiguously matched activity records;
- successful financial settlement paired with a failed paid service;
- explanations that contradict deterministic findings;
- insufficient evidence that makes a run unverifiable.

The flagship result is `PAID_FAILURE`: money settled, but the purchased operation failed.

## Planned 60-second fixture demo

The first implementation milestone will make this path executable without credentials or spending:

```bash
uv sync --locked --all-groups
uv run settlediff verify-fixture fixtures/paid-failure
```

Expected terminal verdict:

```text
PAID_FAILURE
Payment settled successfully, but the purchased service failed.
```

Until that milestone lands, these commands are a documented target rather than an available interface.

## Architecture

SettleDiff is a single Python application with a functional domain core and adapters around external systems:

- `domain`: strict models, normalization, matching, checks, verdicts, and redaction;
- `application`: live-run and fixture-replay use cases;
- `perflo`: safe subprocess adapter and Perflo envelope parsing;
- `agent`: PydanticAI investigator with typed, guarded tools;
- `storage`: local SQLite reports and event timeline;
- `api` and `ui`: FastAPI with server-rendered Jinja/HTMX;
- `telemetry`: optional OpenTelemetry export with sensitive content disabled.

See [Architecture](docs/architecture/overview.md), [ADRs](docs/decisions/README.md), and the [repository map](docs/development/repository-structure.md).

## Development policy

- Product behavior is test-driven and fixture-first.
- Default tests cannot contact Hyperfusion, Perflo, Context.dev, or any paid service.
- Live compatibility and paid smoke tests are explicit opt-in commands.
- Financial values use `Decimal`, never binary floating point.
- Money-moving failures are never retried until submission certainty is resolved.
- Changes are committed in independently reviewable, passing increments.
- Generated-looking filler, unnecessary abstractions, placeholder copy, and other AI slop are rejected.
- Superpowers is not used to execute implementation or fixes; the tracked plan and repository verification loops are authoritative.

Repository instructions are in [AGENTS.md](AGENTS.md). Verification gates are in [Testing](docs/testing/strategy.md), [Evaluation](docs/evaluation/strategy.md), [Observability](docs/observability/strategy.md), and [Verification loops](docs/development/verification-loops.md).

## Implementation sequence

1. Tooling and strict domain models.
2. Normalization, matching, verification, and sanitized fixtures.
3. Safe Perflo subprocess adapter.
4. Hyperfusion compatibility contract.
5. Bounded PydanticAI investigator.
6. CLI, SQLite, and local debugger UI.
7. Optional Context.dev evidence path.
8. ElevenLabs only after core acceptance criteria pass.

The detailed task-by-task plan is [SettleDiff MVP Implementation Plan](docs/superpowers/plans/2026-08-12-settlediff-mvp.md).

## Documentation

- [Production foundation design](docs/superpowers/specs/2026-08-12-production-foundation-design.md)
- [Architecture](docs/architecture/overview.md)
- [Architecture decisions](docs/decisions/README.md)
- [Repository structure](docs/development/repository-structure.md)
- [Testing strategy](docs/testing/strategy.md)
- [Agent evaluation strategy](docs/evaluation/strategy.md)
- [Observability strategy](docs/observability/strategy.md)
- [Security and data handling](docs/security/data-handling.md)
- [Research sources and practice assessment](docs/research/sources.md)
- [Decisions requiring owner input](docs/development/open-decisions.md)

## Scope

The MVP is a local developer tool, not a wallet, payment network, generic agent framework, observability platform, refund engine, multi-tenant SaaS, or fraud-detection system.
