# ADR 0009: Defer Facilitator Comparison Pending Per-Run Provenance

**Status:** Accepted
**Date:** 2026-09-21

## Context

Phase 3 proposes comparing equivalent persisted paid runs across facilitators. Such a
comparison is meaningful only if each run carries an authoritative facilitator identity;
comparing runs without it would falsely attribute evidence to the wrong operator and could
fabricate a provider ranking from provenance we never actually recorded.

## Evidence audit

Every candidate source of facilitator identity was audited:

- **`PaymentRequired` challenge:** no facilitator identity field exists in the captured x402
  v2 shape.
- **`ExternalSignerResult` and signer metadata:** no facilitator identity field.
- **`SettlementResponse` / provider settlement:** no facilitator identity field.
- **Independent EVM receipt `from`:** this is transaction-submitter evidence only. It does not
  establish the operator URL or name, nor that the submitter is the configured facilitator.
  Canonical recovery intentionally does not persist it as facilitator identity.
- **Controlled reference server configuration:** the local controlled server is configured
  with `https://x402.org/facilitator`, and historical docs name it, but this is local
  configuration, not a persisted per-run artifact; it is unavailable for external resources
  and cannot establish per-run provenance or detect runtime/config conflicts.
- **Public endpoint operator pages and docs:** descriptive assertions, not authoritative
  machine evidence tied to each run.

No audited source provides authoritative per-run facilitator provenance.

## Decision

Task 9 and Phase 3 are deferred. SettleDiff will not add `FacilitatorIdentity`,
`FacilitatorDiff`, a `facilitator-diff` command, a `PaymentTerms` field, a report field, a
migration, or any facilitator comparison. The `FACILITATOR_CHANGED` drift component remains
reserved/null. Existing documents that name a facilitator are historical setup descriptions
only, not provenance evidence.

This gate reopens only when a captured, versioned source contract provides all of:

- stable identifier semantics;
- source ownership and classification;
- per-run persistence with an artifact ID;
- conflict representation among challenge, configuration, provider, and on-chain sources;
- availability before authorization whenever facilitator selection can affect execution;
- exact `PaymentTerms` binding and re-observation before signer launch;
- backward compatibility, redaction, and publication rules;
- at least two separately authorized equivalent runs to compare.

## Consequences

Phase 3 completes by explicit deferral rather than by implementation. Phase 4 can proceed:
bundles and publications can represent absent facilitator comparison as unavailable. The
on-chain submitter address may later be persisted under a distinct name as submitter
evidence, but that alone cannot satisfy this provenance gate.

## Rejected alternatives

- Inferring facilitator identity from the receipt `from` field.
- Trusting operator prose or documentation as provenance.
- Treating local server configuration as evidence for arbitrary resources.
- Adding an optional unbound CLI facilitator label.
- Comparing runs by adapter name alone.
