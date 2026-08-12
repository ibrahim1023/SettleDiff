# ADR 0001: Agentic Investigation, Deterministic Verdict

**Status:** Accepted  
**Date:** 2026-08-12

## Context

Evidence for an autonomous purchase may be distributed across contract metadata, a paid execution, a receipt, a service response, and a persisted activity record. Choosing the next useful artifact benefits from model judgment. Budget, settlement, and consistency results must remain repeatable and auditable.

## Decision

Use one bounded Investigation Agent to select and explain evidence. Use pure deterministic code for normalization, matching confidence, every finding, and verdict precedence. If an explanation conflicts with the verifier, validation rejects the explanation and the machine report remains complete.

## Consequences

- The agent cannot improve a result by hiding or rewriting discrepancies.
- Fixture replay and financial tests require no model.
- Agent quality can evolve independently of the verifier.
- Reports carry separate machine and narrative sections.
- Some investigations may complete without a narrative when the model provider is unavailable.

## Rejected

- LLM-generated financial verdicts.
- A deterministic fixed workflow with no evidence-selection agent.
- Multiple specialist agents for the MVP.
