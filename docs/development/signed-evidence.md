# Signed seller evidence

The x402 adapter supports the `offer-receipt` extension's compact JWS/Ed25519
profile. It follows the [pinned normative specification](https://github.com/x402-foundation/x402/blob/dd927a26cfefc98c24b3ec38b3a8f204dad0c60d/specs/extensions/extension-offer-and-receipt.md)
under [ADR 0012](../decisions/0012-signed-provider-evidence.md). Unsigned services
retain the existing workflow. EIP-712, other JWS algorithms, historical key rotation,
and networks outside the existing Base Sepolia exact-USDC profile are unsupported.

## Before authorization

The unpaid challenge advertises offers at
`extensions.offer-receipt.info.offers`. SettleDiff requires one unambiguous primary
offer. Its `acceptIndex` is an unsigned hint; the signed `resourceUrl`, `scheme`,
`network`, `asset`, `payTo`, and `amount` must match the selected requirement.
When present, `validUntil` must not have expired.

The adapter captures the seller's `/.well-known/did.json` through same-origin HTTPS,
without redirects, bounded to 16 KiB. The JWS header's `kid` must belong to that
origin's `did:web` identity, and the Ed25519 key must be authorized for assertions.
Mathematical signature validity and seller-key authorization are separate results.
Public JWKs may also carry compatible `alg`, `use`, `kid`, and `key_ops` metadata;
conflicting algorithms or encryption-only key uses are rejected.

A digest of the exact JWS and captured identity-document digest becomes
`PaymentTerms.signed_offer_digest` and `ExpectedContract.signed_offer_digest` in
schema 4. The CLI shows the proof digest with the exact request confirmation.
Immediately before signer launch, the adapter repeats the challenge and identity
capture and verifies the offer again. Changed proof, identity document, terms,
revoked key, or expiry refuses execution and requires fresh authorization.

## After the one attempt

The external signer returns the public seller receipt at
`provider_settlement.extensions.offer-receipt.info.receipt`. SettleDiff checks its
signature, assertion authorization, offer-to-receipt key continuity, signed
resource/network/payer, and `issuedAt` (at most one hour old, with 30 seconds of
future clock tolerance). Every available transaction reference is checked for a
contradiction. Positive transaction binding requires both the signer/provider and
independent observer references to match the signed `transaction`.

The deterministic report adds `signed_offer`, `signed_receipt`,
`signed_receipt_continuity`, and `signed_receipt_transaction` findings. A missing
advertised receipt is unknown. An omitted optional transaction is a warning;
a known mismatch fails even when another source is unavailable. Receipt capture
also runs when the outer signer result is uncertain. Recovery remains read-only
and never repeats the payment.

These signatures remain **provider assertions**. They cannot establish settlement
when independent observation is unavailable, and cannot convert a contradiction
into a verified result. Existing independent transfer and delivery checks still run.

## Signer compatibility and evidence retention

The external request stays schema 2 and carries the opaque payment-terms digest;
the result stays schema 3. A signed-service-compatible signer must preserve the
challenge's public offer extension and the provider settlement's public receipt
extension. Only those exact namespaces admit bounded public seller JWS strings.
Wallet signatures, payment payloads, credentials, and other nested signatures are
still rejected. Before returning its result, the signer must project challenge
extensions to `offer-receipt.info.offers` and settlement extensions to
`offer-receipt.info.receipt`. Extension schema metadata and unrelated signed vendor
markers do not belong in this output. Passing the entire extension tree through
can trigger refusal even when its primary offer is valid. Run the existing metadata/doctor compatibility check before any
separately authorized live trial; offline integration does not prove an external
signer's signed-service compatibility.

The locally installed signer now calls `scripts/x402_signer_offer.py` before
creating a payment payload. The helper receives exactly `challenge` and `request`
(the schema-2 signer envelope) as JSON on stdin, reuses the production read-only
adapter to validate the offer and fetch its seller identity, and emits only
`{"signed_offer_digest":"…"}`. It refuses malformed, expired, revoked, redirected,
or oversized evidence. It has no signing key, payment port, persistence, or model.
The signer inserts the returned digest into schema-4 payment terms and compares
that full digest with the authorization before signing. A refusal or mismatch
must stop execution. Invoke it from the repository root through
`uv run --no-sync python scripts/x402_signer_offer.py`; keep its input in pipes,
not command arguments or files. Signer runtime files and wallet setup remain local.

The local runtime also narrows EIP-3009 validity to the advertised timeout instead
of using an epoch-zero start. Its five-second past-clock allowance is included in
the total interval; the signature and serialized authorization contain identical
timestamps. It supports 12–300-second timeouts and refuses other lifetimes before
submission. This local signing policy does not change the selected terms, paid
budget, or deterministic verification criteria.

Raw JWS strings and DID documents are used in memory. Persisted schema-4
`provider_signatures` contain assessments, digests, timestamps, and source handles;
raw signatures are redacted from captured snapshots and excluded from SQLite,
bundles, public reports, agent summaries, and telemetry. Unsigned reports omit the
new optional fields, preserving their existing serialization.

Private bundle verification recomputes signature findings from the retained
assessments and checks consistency and integrity. It **cannot replay cryptographic
verification** because the raw proof is deliberately absent, and a checksum does
not authenticate the bundle's origin. Current DID capture also does not establish
which keys were authorized at a historical time.

## Offline verification

Tests use generated synthetic Ed25519 keys, fixed clocks, mocked HTTP/RPC, and an
intercepted signer. They cover authorization drift, bad/missing receipts, uncertain
results, forbidden signer material, persistence, bundle consistency, and public
projection. An [unpaid live staging inspection](../testing/signed-x402-readiness-2026-10-13.md)
verified a seller offer. The updated external signer also matches the production
authorization digest on unpaid staging evidence. [Approved staging trials](../testing/signed-x402-attempt-2026-10-14.md)
remain unresolved. The original attempt lacked usable execution evidence; later
trials retained HTTP 400 `x402.invalid_payment` responses without a transaction or
signed receipt. The bounded-window signer trial also received that rejection.
Receipt verification remains covered offline; a successful live signed-purchase
trial is still outstanding. No automatic retry followed an uncertain submission.

The installed signer also caps `validBefore` to five seconds before the verified
offer's `validUntil`, even when the advertised timeout is longer. It refuses
before submission if fewer than 12 seconds remain after that allowance. A returned
HTTP 402 challenge can contain duplicate seller signatures in its body; retain
its HTTP status and exact byte count while omitting that parsed body from signer
output. Seller proofs remain restricted to their permitted extension envelopes.

```bash
uv run pytest tests/unit/x402/test_signed.py tests/unit/x402/test_signed_extension.py tests/unit/x402/test_seller_identity_http.py tests/unit/x402/test_signed_adapter.py tests/unit/domain/test_provider_signatures.py -q
```
