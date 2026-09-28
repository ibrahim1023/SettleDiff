# ADR 0010: Independent Settlement Observation

**Status:** Accepted  
**Date:** 2026-09-28

## Context

ADR 0007 reserved `MachineReport.ledger` for independently observed settlement, and ADR 0008 separated provider assertions from independent observations. The live collector still placed any matched Activity record in `ledger`, and settlement checks could fall back to provider execution status. For Perflo, `pay`, agent Activity, and `tx status` are one provider trust domain, so a clean Perflo run could reach `VERIFIED` without any external settlement evidence.

The x402 path already validated an exact USDC transfer through a read-only RPC, but that validation was x402-specific, used a post-payment payer attribution, and did not persist a single-source observation separately from provider comparison.

## Decision

### Evidence classes

Adapter evidence declares `provider_assertion` or `independent_observation`. Perflo evidence is always a provider assertion. The x402 read-only transaction-receipt evidence is an independent observation. Evidence class is provenance metadata; checks and verdicts never branch on adapter identity.

### Settlement profile

Independent settlement requires a `SettlementProfile` bound before payment: CAIP-2 network, numeric chain ID, asset identity (network, contract reference, decimals), exact atomic amount, recipient, and payer. Payer validation is mandatory (`REQUIRED` is the only policy); there is no optional or not-exposed policy in this release.

For x402 the profile is built from the selected pre-payment requirement and the payer reported by the signer metadata probe before authorization. `PaymentTerms` schema 4 binds the payer and the canonical profile digest; the profile is rebuilt from the reobserved challenge before signer launch and any drift refuses execution. A signer result whose payer differs from the pre-authorized payer is treated as uncertain submission, and observation still uses the pre-authorized payer.

Perflo v8 does not supply these exact terms before payment, so Perflo reports record `SETTLEMENT_PROFILE_UNAVAILABLE`. SettleDiff never derives a profile from symbols, friendly chain names, defaults, or post-payment fields.

### Observation

A rail-neutral read-only EVM observer validates chain ID, receipt, and exactly one matching ERC-20 `Transfer` log against the profile. It produces one of:

| Status | Meaning | Diagnostics |
|---|---|---|
| `CONFIRMED` | Exact authorized transfer observed; all five dimensions verified | `EXACT_TRANSFER_CONFIRMED` |
| `FAILED` | External ledger shows the transaction reverted | `RECEIPT_REVERTED` |
| `INDETERMINATE` | Observer responded, but evidence is pending, malformed, mismatched, or unsupported | `RECEIPT_PENDING`, `CHAIN_MISMATCH`, `MALFORMED_RECEIPT`, `MALFORMED_RPC_RESPONSE`, `TRANSFER_MISMATCH`, `INVALID_TRANSACTION_REFERENCE`, `UNSUPPORTED_SETTLEMENT_PROFILE` |
| `UNAVAILABLE` | No observation could be obtained | `OBSERVER_UNAVAILABLE`, `NO_TRANSACTION_REFERENCE`, `SETTLEMENT_PROFILE_UNAVAILABLE` |

Each observation records the observer source, transaction reference, profile, and per-dimension verification (chain, asset, amount, recipient, payer). A configured RPC is an external interface outside the provider's CLI evidence surface; reports call it independently observed through that source and do not claim organizational neutrality of the RPC operator.

### Comparison

Provider/observer comparison is a separate deterministic record: `MATCH`, `CONTRADICTED`, or `NOT_COMPARABLE`. A reverted receipt contradicts only a provider success claim; a confirmed transfer contradicts only a provider failure claim; a transfer mismatch contradicts a provider success claim. Unavailable or inconclusive observations are never comparable.

### Verdict semantics

For schema-4 reports, the `settlement` and `paid_failure` checks derive settlement only from the independent observation: `CONFIRMED` settles unless the provider claims failure, `FAILED` fails unless the provider claims success, and every other state or contradiction is unknown. Provider Activity and transaction status cannot establish settlement. Consequently a provider-only Perflo run is `UNVERIFIABLE`, and a provider-only paid service failure cannot become `PAID_FAILURE`. Verdict precedence is unchanged.

Retry analysis still treats provider evidence as payment-attempt evidence; unavailable or inconclusive independent observation never proves non-submission.

### Report schema 4

`MachineReport` schema 4 requires `independent_settlement` and `settlement_comparison`, adds optional `provider_activity`, and requires `ledger` to equal the observation's ledger. Schemas 1–3 reject the new keys, including explicit null, and retain their original meaning: historical Perflo `ledger` values remain provider Activity; historical x402 `ledger` values remain independently observed receipt/log evidence. No SQLite migration is required because reports are stored as immutable JSON. Public reports use public schema 2 to publish only independent status, comparison status, diagnostics, verification dimensions, and payer policy; observer source, transaction reference, profile, and identifiers are never published.

## Consequences

- SettleDiff can say a Perflo payment is independently verified only when an external observer validates the exact pre-authorized transfer; with current Perflo v8 evidence that remains unavailable.
- x402 keeps exact-transfer semantics while binding the payer before signing.
- Provider consistency findings remain useful for incident analysis but no longer improve a settlement verdict.
- Adding a Perflo observer later requires a captured, versioned pre-payment settlement profile; no adapter-specific verdict branch is permitted.

## Rejected

- Treat Perflo Activity or `tx status` as independent: they share the provider trust domain.
- Combine observation and comparison in one enum: encodes a cross-source conclusion in a single-source record.
- Optional or not-exposed payer policy: silently converts missing payer evidence into confirmation.
- Use post-payment signer or provider payer as the expected payer: lets the investigated path choose its own verification key.
- Multi-observer quorum in this release: availability improvement deferred until single-observer semantics are proven; quorum must never convert unavailable evidence into success.
