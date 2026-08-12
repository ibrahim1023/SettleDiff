# ADR 0005: Fixture-First Tests and Code-First Agent Evals

**Status:** Accepted  
**Date:** 2026-08-12

## Context

Default development must be reproducible and free while the product operates around real payments and stochastic model behavior.

## Decision

Make sanitized fixture replay the primary integration path. Test deterministic logic with pytest and Hypothesis. Test agent trajectories using PydanticAI `FunctionModel`, tool schemas using `TestModel`, and globally disable real model requests in the offline suite.

Use Pydantic Evals for investigation behavior. Grade outcomes and trajectories with code first. Keep live Hyperfusion contracts and live Perflo smoke tests explicit, separate, and opt-in.

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
