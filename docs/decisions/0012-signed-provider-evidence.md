# ADR 0012: Signed Provider Evidence with Ephemeral Signatures

**Status:** Accepted
**Date:** 2026-10-12

## Context

The improvement roadmap prioritizes signed offers and receipts. The current x402
helpers verify captured Ed25519 JWS artifacts but do not participate in the paid path.
The repository forbids persisting nested signatures and separates provider assertions
from independent settlement. Integration must preserve those rules.

## Decision

Support the x402 `offer-receipt` extension's JWS/Ed25519 profile first, following the
[normative extension specification](https://github.com/x402-foundation/x402/blob/dd927a26cfefc98c24b3ec38b3a8f204dad0c60d/specs/extensions/extension-offer-and-receipt.md)
and the same pinned upstream implementation. Offers are in `info.offers`, receipts
in `info.receipt`; their envelopes use `format`, `signature`, and optional offer
`acceptIndex`. Signed payloads use `scheme` and optional receipt `transaction`.
Do not infer aliases from the overview documentation's different field names.

Unsigned services keep their existing behavior. When an extension is advertised,
require one unambiguous supported offer matching the selected primary requirement.
Fetch the seller's bounded DID document through read-only, same-origin HTTPS with
no redirects. Verify its assertion key and offer before authorization. Bind the
exact offer and captured identity digest in payment terms. Reinspect and verify
again immediately before signer launch; changed proof or identity requires fresh
authorization. Unsupported, malformed, mismatched, or expired offers cannot launch
the signer. The selected `acceptIndex` is only a hint; signed terms must match.

Capture receipt verification after the one authorized attempt. Check seller-key
continuity, signed resource/network/payer, issuance time, and transaction references
when available. Missing or omitted optional transaction data cannot establish
transaction binding and is reported explicitly. No receipt authorizes a retry.

Persist rail-neutral verification assessments, source handles, and digests with
schema-4 reports. Add deterministic findings without upgrading settlement from
signatures. Raw JWS strings remain in memory, are redacted from snapshots, and are
excluded from persistence, export, publication, agent summaries, and telemetry.
The signer boundary permits only bounded public seller JWS objects in the exact
extension namespace; wallet/payment signatures remain forbidden everywhere.

## Consequences

- A seller signature remains provider evidence, not independent settlement proof.
- Existing unsigned report/authorization serialization remains unchanged.
- Bundles preserve assessed results and digests, not replayable cryptographic proof.
- Freshly reissued offers or mutable DID documents may require new authorization.
- EIP-712, other JWS algorithms, key-rotation history, and broader networks remain unsupported.
- Default tests use fixed clock values, synthetic signing keys, mocked HTTP, and intercepted writes.

## Verification

Implement and commit passing increments for extension parsing, bounded DID capture,
authorization binding, paid-path receipt capture, deterministic findings/persistence,
and public projection. Run the paid-boundary verification loop offline, then the
full gate. No paid or live identity-fetch trial is authorized by this change.
