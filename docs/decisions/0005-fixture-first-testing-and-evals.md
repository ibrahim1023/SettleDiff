# ADR 0005: Fixture-First Tests and Code-First Agent Evals

**Status:** Accepted  
**Date:** 2026-08-12

## Context

Default development must be reproducible and free while the product operates around real payments and stochastic model behavior.

## Decision

Make sanitized fixture replay the primary integration path. Test deterministic logic with pytest and Hypothesis. Test agent trajectories using PydanticAI `FunctionModel`, tool schemas using `TestModel`, and globally disable real model requests in the offline suite.

Use small pytest datasets and deterministic code graders for investigation behavior. Add a dedicated eval framework only when repeated capability trials require it. Keep live Groq contracts and manually authorized paid cycles explicit, separate, and opt-in.

The offline dataset declares fixed evidence, scripted model stimuli, and independent
pass criteria. Every case runs the production investigation interface; payment safety
cases also run the authorization interface with an intercepted write. Grade observed
tools, grounding, limits, immutable findings, and one-use authorization separately.
Scripted trials prove enforcement and wiring, not a live model's evidence-selection ability.

Execution budgets remain bounded. Observed token-usage counters in investigation
results and explanation records are nonnegative without a smaller storage ceiling:
a provider can report an overrun on the response that triggers termination, and
the fallback must retain that observation rather than fail validation or truncate it.

## Consequences

- CI never spends money or depends on external uptime.
- Known provider envelope changes are visible through contract failures.
- Agent regressions are measurable without granting financial capabilities.
- Live compatibility still requires a controlled pre-release gate.

## Rejected

- Live-model unit tests.
- Paid calls in ordinary CI.
- Snapshot-only grading of prose.
- LLM judges for financial truth.
