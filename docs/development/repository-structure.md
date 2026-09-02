# Repository Structure

The repository keeps architecture, source, tests, and sanitized fixtures together while local specifications and live captures remain ignored. Empty directories and placeholder modules are avoided.

```text
.
├── AGENTS.md
├── README.md
├── Product-spec.md                 # local-only, ignored
├── task.md                         # future local-only input, ignored
├── pyproject.toml                  # Phase 1
├── uv.lock                         # Phase 1, committed
├── .env.example                    # Phase 1, no secrets
├── docs/
│   ├── architecture/               # system views and boundaries
│   ├── decisions/                  # accepted ADRs
│   ├── development/                # workflow and verification loops
│   ├── evaluation/                 # probabilistic agent evals
│   ├── observability/              # logs, traces, metrics, redaction
│   ├── research/                   # primary sources and practice decisions
│   ├── security/                   # threat-aware data handling
│   ├── testing/                    # deterministic test strategy
│   └── superpowers/
│       ├── plans/                  # executable implementation plans
│       └── specs/                  # approved designs
├── fixtures/                       # sanitized versioned evidence bundles
├── src/settlediff/
│   ├── agent/                      # PydanticAI model factory, tools, prompt
│   ├── api/                        # FastAPI routes and dependencies
│   ├── application/                # run/replay use cases and rail-neutral adapter contracts
│   ├── contextdev/                 # required live independent evidence adapter
│   ├── domain/                     # pure models, normalization, checks
│   ├── perflo/                     # neutral adapter, subprocess client, envelope parsers
│   ├── x402/                       # v2 adapter, unsigned HTTP, signer contract, RPC/recovery
│   ├── storage/                    # repositories and SQLite implementation
│   ├── telemetry/                  # structured logging and OTel wiring
│   └── ui/                         # Jinja templates and static assets
└── tests/
    ├── contract/                   # captured provider/CLI envelopes
    ├── evals/                      # agent datasets and graders
    ├── fixtures/                   # fixture replay expectations
    ├── integration/                # adapter/use-case/interface boundaries
    └── unit/                       # pure domain and isolated agent tests
```

## Boundary rules

- `domain` imports only the standard library and Pydantic.
- `application` depends on domain protocols, not concrete adapters.
- adapters may depend inward; domain code never imports adapters.
- Typer and FastAPI call application services and contain no verification logic.
- raw payloads and normalized records are separate types.
- PydanticAI types do not cross into the deterministic verifier.
- optional integrations live behind protocols and cannot block fixture replay.

## File-size signal

A file that mixes two reasons to change should be split. As a review signal, investigate Python modules above roughly 300 lines and templates above roughly 250 lines; this is not an automatic failure when cohesion is demonstrable.
