# Research Sources and Practice Assessment

Research was conducted on 2026-08-12. Primary and official sources were preferred for technical decisions. GitHub repository counts are only popularity signals, not quality guarantees.

## Sources consulted

### Product and model provider

- [Hyperfusion](https://hyperfusion.io/) — OpenAI-compatible APIs, function calling, structured outputs, Pydantic/JSON Schema compatibility, and regional deployment claims.
- The local `Product-spec.md` — authoritative product scope; intentionally ignored by Git.
- Perflo instructions supplied with the workspace — command envelopes, account capability differences, ambiguous amounts, mutation certainty, and no-double-spend recovery.

### Python application stack

- [uv projects](https://docs.astral.sh/uv/guides/projects/) and [locking/syncing](https://docs.astral.sh/uv/concepts/projects/sync/) — reproducible project environments, committed lockfile, locked CI execution, and dependency groups.
- [FastAPI dependencies](https://fastapi.tiangolo.com/tutorial/dependencies/), [testing](https://fastapi.tiangolo.com/tutorial/testing/), and [deployment concepts](https://fastapi.tiangolo.com/deployment/concepts/) — dependency overrides, HTTPX-backed tests, and deployment concerns.
- [Python asyncio subprocesses](https://docs.python.org/3/library/asyncio-subprocess.html) — argument-based process creation, `communicate`, timeouts through `wait_for`, and pipe deadlock avoidance.
- [SQLite file format](https://sqlite.org/fileformat.html) and [WAL](https://sqlite.org/wal.html) — transactional local persistence and WAL operational trade-offs.
- [Typer testing](https://typer.tiangolo.com/tutorial/testing/) — CLI tests through `CliRunner`.
- [Hypothesis](https://hypothesis.readthedocs.io/en/latest/) — property-based invariant testing.
- [GitHub Actions secure use](https://docs.github.com/en/actions/reference/security/secure-use) and [Python build/test guidance](https://docs.github.com/en/actions/tutorials/build-and-test-code/python) — least-privilege workflow permissions, immutable full-SHA action pinning, and consistent Python setup.

### Agent, context, harness, loop, graph, and eval practices

- [PydanticAI agents](https://pydantic.dev/docs/ai/core-concepts/agent/) — typed dependencies and structured outputs.
- [PydanticAI OpenAI-compatible providers](https://pydantic.dev/docs/ai/models/openai/) — custom `AsyncOpenAI` client and base URL for Hyperfusion.
- [PydanticAI testing](https://pydantic.dev/docs/ai/guides/testing/) — `TestModel`, `FunctionModel`, `Agent.override`, and blocking real model requests.
- [PydanticAI evals](https://pydantic.dev/docs/ai/evals/evals/) — datasets, cases, code-based evaluators, and experiment reports.
- [PydanticAI observability](https://pydantic.dev/docs/ai/integrations/logfire/#using-opentelemetry) — OpenTelemetry export without requiring a particular hosted backend.
- [OpenTelemetry semantic conventions](https://opentelemetry.io/docs/specs/semconv/) — standardized trace, metric, and log naming; GenAI conventions remain evolving.
- [Effective context engineering for AI agents](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents) — treat context as finite and provide the smallest high-signal evidence.
- [Demystifying evals for AI agents](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents) — outcome grading, trajectories, balanced cases, reference solutions, regression/capability separation, and transcript review.
- [Harness design for long-running application development](https://www.anthropic.com/engineering/harness-design-long-running-apps) — external evaluation and structured handoffs; assessed but largely unnecessary for this short loop.
- [LangGraph persistence](https://docs.langchain.com/oss/python/langgraph/persistence) and [interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts) — durable state, recovery, and human interruption patterns.
- [OpenAI Agents SDK](https://github.com/openai/openai-agents-python) and [tool guardrails](https://openai.github.io/openai-agents-python/guardrails/) — free MIT-licensed alternative with provider support and guardrails.
- [CrewAI](https://github.com/crewAIInc/crewAI) and [AutoGen](https://github.com/microsoft/autogen) — popular multi-agent alternatives; AutoGen is now in maintenance mode.

## Adopted practices

| Practice | Concrete value to SettleDiff |
|---|---|
| Functional core, imperative shell | Keeps financial truth deterministic and independently testable. |
| One bounded PydanticAI agent | Adds evidence selection without multi-agent cost or verdict ambiguity. |
| Typed tools and structured output | Makes the model boundary inspectable and validates explanation references. |
| Request, tool, token, cost, and deadline limits | Prevents runaway investigation loops and unexpected inference cost. |
| Minimal high-signal context | Sends normalized summaries and artifact handles instead of dumping raw provider payloads. |
| One-use paid-execution capability | Makes the no-retry/no-budget-increase rule enforceable in code. |
| Fixture replay and captured contracts | Makes default tests deterministic, free, and safe. |
| Outcome and trajectory evals | Scores both collected evidence and the tool path used to obtain it. |
| Code-based graders first | Financial and citation correctness do not need an LLM judge. |
| Separate capability and regression suites | Preserves known-safe behavior while allowing investigation quality to improve. |
| OpenTelemetry with content capture off | Provides portable visibility without leaking financially sensitive data. |
| Correlation IDs and low-cardinality metrics | Connects layers without turning identifiers into unsafe metric labels. |
| Small reviewable commits | Makes generated changes auditable and failures easy to bisect. |
| Explicit anti-slop review | Prevents speculative abstractions, filler UI, invented fields, and placeholder artifacts. |
| Least-privilege offline CI with full-SHA action pinning | Repeats the local gate without credentials and reduces workflow supply-chain exposure. |

## Rejected or deferred practices

| Practice | Decision |
|---|---|
| LangGraph/Pydantic Graph in MVP | Rejected until pause/resume or durable recovery becomes a measured requirement. |
| Multi-agent crews or handoffs | Rejected; one investigator is sufficient and easier to evaluate. |
| Long-term agent memory | Rejected; each purchase investigation is self-contained and persisted as evidence, not conversation memory. |
| Context compaction | Rejected for the bounded loop; explicit artifact selection prevents context growth. |
| Vector database/RAG | Rejected; the evidence set is small, structured, and run-scoped. |
| LLM-as-judge for verdicts | Prohibited. Deterministic code owns financial truth. |
| LLM-as-judge for initial explanations | Deferred until a subjective quality gap cannot be captured by code graders and human calibration. |
| Hosted observability dependency | Rejected; OTLP export is optional and local logs must remain sufficient. |
| React/Vite SPA | Rejected for MVP; server-rendered diff pages avoid a second build and duplicated types. |
| Postgres, queues, workflow engines | Rejected until multi-user concurrency or durable background execution is required. |
| Automatic payment retries | Prohibited because transport uncertainty can cause double spending. |
| Custom project skill | Not created. AGENTS.md plus executable verification loops are more direct; add a skill only after repeated cross-project judgment failures demonstrate a reusable need. |

## Compatibility risk requiring an early spike

“OpenAI-compatible” does not guarantee identical tool-call, structured-output, streaming, usage, or error behavior. Before agent implementation, an unpaid Hyperfusion contract test must validate the selected base URL and model against the exact PydanticAI chat path. The application must use a model profile for known capability differences rather than weakening schemas after runtime failures.
