# Verification Loops

These loops are the project-specific reusable process. No custom Codex skill is created initially: the rules are local, mechanically verifiable, and better kept beside the code. Create a reusable skill only after the same judgment failure recurs across projects and cannot be enforced by tests or tooling.

## Per-change loop

1. Identify one behavior and its owning boundary.
2. Write the smallest failing test that proves the behavior.
3. Run that exact test and confirm the expected failure reason.
4. Implement the minimum coherent change.
5. Run the focused test until green.
6. Run neighboring module tests, lint, and type checks.
7. Review the diff for safety, scope, and AI slop.
8. Commit the passing increment with a specific Conventional Commit message.

Do not combine unrelated refactors, documentation cleanup, and behavior changes merely because they are nearby.

## Deterministic-core loop

```bash
uv run pytest tests/unit/domain/<test_file>.py -q
uv run pytest tests/unit/domain tests/fixtures -q
uv run pyright src/settlediff/domain tests/unit/domain
uv run ruff check src/settlediff/domain tests/unit/domain tests/fixtures
```

Review invariants:

- no I/O or agent imports in domain code;
- `Decimal` and explicit units for money;
- missing/unknown values remain explicit;
- finding order does not affect verdict;
- new failure states cannot improve verdict severity.

## Paid-boundary loop

Before any Perflo execution change:

1. run offline adapter contracts;
2. prove authorization mismatch and reuse are rejected;
3. prove timeout/uncertain submission cannot invoke execution again;
4. inspect the exact subprocess argument list;
5. confirm tests use a fake executable or captured envelopes;
6. keep live/paid markers excluded.

No paid test runs without explicit user authorization in the current conversation, the paid environment flag, a test budget, and an interactive confirmation.

## Agent loop

```bash
uv run pytest tests/unit/agent -q
uv run pytest tests/evals -m "not live" -q
```

Review trajectory assertions, not just final prose:

- required and forbidden tools;
- arguments and authorization identity;
- request/tool/token/cost/deadline limits;
- no repeated paid call;
- evidence sufficiency;
- valid finding/artifact citations;
- no contradiction with the machine report.

## Hyperfusion compatibility loop

This loop is unpaid but live and never runs in default CI:

```bash
SETTLEDIFF_LIVE_HYPERFUSION=1 uv run pytest tests/contract/test_hyperfusion_live.py -m live_hyperfusion -q
```

Validate the configured model's tool call, multi-turn continuation, structured output, usage metadata, timeout, rate-limit, and malformed-response behavior. Pin the accepted model ID in deployment configuration, not source prompts.

## Documentation loop

```bash
git diff --check
uv run python scripts/check_docs.py
```

`scripts/check_docs.py` verifies internal links, required ADR fields, ignored local inputs, command references, and banned placeholder markers.

## Anti-slop review

Reject the change if any answer is unclear:

- Which accepted requirement does each new file, dependency, abstraction, or UI element serve?
- Which consumer uses it now?
- Which test proves its behavior?
- Does it duplicate an existing type or layer?
- Does prose state evidence and decisions, or merely sound polished?
- Are names specific to SettleDiff rather than generic `utils`, `manager`, `handler`, or `processor` buckets?
- Are comments explaining non-obvious constraints rather than restating code?
- Is sample data clearly synthetic?
- Did the model invent a provider field or command instead of using captured evidence or official documentation?
- Can any code or copy be deleted without losing accepted behavior?

Generic gradients, decorative charts, dashboard filler, fake metrics, repeated cards, placeholder copy, and gratuitous “AI-powered” language fail this review.

## Pre-completion gate

Before reporting an implementation task complete:

```bash
uv lock --check
uv run ruff format --check .
uv run ruff check .
uv run pyright
uv run pytest -m "not live and not paid"
uv run python scripts/check_docs.py
git diff --check
git status --short
```

Then inspect the resulting artifact manually: CLI output for CLI work, rendered pages for UI work, trajectories for agent work, and redacted spans/logs for observability work. Do not claim success from exit codes alone when the product output is visual or interpretive.
