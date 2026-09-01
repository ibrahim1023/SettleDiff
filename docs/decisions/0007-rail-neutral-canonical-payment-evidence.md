# ADR 0007: Rail-Neutral Canonical Payment Evidence

**Status:** Accepted  
**Date:** 2026-08-31

## Context

SettleDiff's first live adapter is Perflo. Its canonical models preserve the
concepts needed by the first MPP investigations, but an unsigned x402 v2
challenge captured from the official reference implementation exposed
settlement-relevant evidence the current models cannot represent without loss.

The capture contained:

- CAIP-2 network `eip155:84532`, which must remain distinct from Base mainnet;
- a network-bound USDC contract and atomic-unit amount;
- an advertised `payTo` recipient;
- x402 protocol version and `exact` scheme;
- a payment-term timeout and transfer-method semantics;
- an HTTP GET request with no body.

The current contract has no advertised recipient or payment scheme. Its `chain`
and asset-symbol fields cannot losslessly identify a testnet or token contract.
The paid request has no HTTP method or absent-body representation. The report
cannot carry provider settlement receipt and independent ledger evidence as
separate cited records.

These gaps must be resolved before generalizing the Perflo-shaped application
port or introducing x402 signing.

## Decision

### Network identity

Add an optional canonical `network` field using validated CAIP-2 identity while
retaining the existing `chain` field during the compatibility period.

- `network` is the lossless settlement identity.
- `chain` remains readable for existing schema-v1 Perflo reports and fixtures.
- Base Sepolia (`eip155:84532`) and Base mainnet (`eip155:8453`) are never
  collapsed into the same canonical value.
- Rail adapters own aliases from provider-specific chain names to canonical
  network identity. Unknown mappings remain unknown.

### Asset identity

Add a strict optional `AssetIdentity` carrying:

- symbol;
- canonical network;
- asset contract/reference;
- decimal precision.

`Money` continues to own exact `Decimal` amount arithmetic and unit comparison.
An asset symbol alone does not establish token identity. Atomic-unit conversion
requires a matching asset identity whose decimals come from versioned trusted
configuration or independently verified metadata.

Existing Perflo evidence that provides only a symbol remains valid with
`asset_identity=None`; the verifier must not invent a token contract.

### Advertised terms

Extend canonical contract evidence with optional:

- advertised recipient;
- payment scheme;
- canonical network and asset identity.

Provider protocol and payment scheme remain separate concepts. For example,
`x402` is the protocol and `exact` is the scheme.

### Request representation and authorization

Extend paid execution requests with an explicit HTTP method and an optional JSON
body. Existing Perflo callers retain `POST` and object-body behavior through
backward-compatible construction.

After preflight selects one payment requirement, create a versioned canonical
payment-terms descriptor containing the adapter, protocol version, scheme,
network, asset identity, recipient, quoted amount, timeout, resource URL, method,
and request-body digest. Bind the one-use authorization to the digest of that
complete descriptor as well as the existing run, target, budget, and expiry.

Any changed term requires a new preflight and fresh explicit authorization.

### Provider and independent settlement

Add optional `receipt: PaymentReceipt` to `MachineReport` for provider-reported
settlement evidence. Retain `ledger: LedgerRecord` for independently observed
settlement evidence, including a validated network transaction and token
transfer. `MachineReport.adapter_id` is optional provenance metadata for CLI/UI
presentation and bundle diagnostics only; deterministic checks and verdicts must
not branch on it.

- A provider receipt is evidence, not independent truth.
- Transaction existence or receipt success alone does not prove the expected
  token transfer.
- Independent EVM settlement requires deterministic validation of network,
  token contract, transfer event, payer where available, recipient, and amount.
- The facilitator may pay gas, so transaction sender is not automatically the
  payer.
- Missing or contradictory evidence remains unknown/diff according to
  deterministic checks; no adapter-specific verdict branch is permitted.

### Version compatibility

Introduce new canonical report fields through an explicit report schema-version
increment. New fields are optional when reading schema-v1 reports, fixtures,
database JSON, and bundle payloads.

- Existing schema-v1 values retain their original meaning and version.
- New reports use the new schema version once the fields are implemented.
- Bundle compatibility metadata must equal the contained report version.
- SQLite stores immutable report JSON, so no relational schema change is needed
  solely for optional canonical fields; storage round-trip and migration tests
  still gate the change.
- Renderers display unavailable new fields as unavailable and never synthesize
  them from legacy values.
- Any later incompatible reinterpretation requires another schema version and
  explicit migration logic.

## Consequences

- The deterministic verifier can compare equivalent economic evidence across
  Perflo and x402 without checking the adapter name.
- Canonical models become more precise but retain legacy Perflo evidence.
- Asset and network consistency checks can detect same-symbol and testnet/mainnet
  mismatches.
- Authorization protects the exact selected payment requirement, not only the
  maximum budget.
- Reports can show provider claims separately from independently observed
  settlement.
- Domain and report changes must be propagated through normalization, redaction,
  bundles, storage, fixtures, CLI, UI, and tests in one versioned increment.

## Rejected

- Store CAIP-2 values only in `chain`: mixes labels and network identities and
  encourages loss between testnet and mainnet.
- Replace `chain` immediately: breaks existing reports and fixtures without
  improving the first x402 proof.
- Identify tokens by symbol alone: permits same-symbol assets to compare equal.
- Put network and token identity inside `Money`: couples arithmetic to settlement
  provenance and duplicates identity across values.
- Treat `PAYMENT-RESPONSE` as independent settlement truth: the response is
  supplied by the resource-server/facilitator path being investigated.
- Use only `ExecutionRecord` plus the existing ledger field: obscures the
  provider receipt and prevents direct provider-versus-independent citation.
- Bind authorization only to target, body, and budget: permits scheme, network,
  asset, recipient, or quote drift after user confirmation.
- Build a generic evidence list: broader abstraction than the two concrete
  settlement roles currently require.
