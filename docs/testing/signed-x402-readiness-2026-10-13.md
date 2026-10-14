# Signed x402 readiness — 2026-10-13

## Scope

The owner authorized live validation and asked for research to find a signed test
endpoint. These checks inspected unpaid resources, public seller keys, signer
metadata, and a read-only Base Sepolia RPC. No x402 signing or payment occurred.
Raw JWS strings, key documents, wallet identifiers, and credentials were not saved.

## Selected candidate

The [PubFi staging guide](https://docs.pubfi.ai/getting-started/staging) identifies
`https://api-stg.pubfi.ai` as its Base Sepolia boundary and directs callers to the
current capability catalog. The [accountless x402 guide](https://docs.pubfi.ai/getting-started/x402)
describes Ed25519 signed offers and receipts. Documentation alone was not treated
as availability evidence.

The live staging catalog declared this exact GET operation ready and Quantro-priced:

- Resource: `https://api-stg.pubfi.ai/v1/gateway/alternative-me/fng`.
- Method/body: GET with no body.
- Observed unpaid response: HTTP 402 with `PAYMENT-REQUIRED`.
- Observed network: Base Sepolia (`eip155:84532`).
- Observed amount: 1,000 atomic units, or 0.001000 test USDC.
- Extensions: `offer-receipt` and `pubfi-contract`.
- Public DID: `https://api-stg.pubfi.ai/.well-known/did.json`, HTTP 200.

After the verifier correction below, two production-adapter inspections returned
`SIGNED_OFFER_VALID`, with the same signed-offer proof digest. These are observations
at inspection time, not a promise that future terms or proofs will stay unchanged.
No receipt or independent transfer was observed, so no paid-run verdict is claimed.
The previous GoPlausible weather endpoint returned HTTP 402 with only a Bazaar
extension and could not exercise signed offers/receipts.

## Compatibility correction

The verifier rejected the public seller JWK solely because it included standard
`alg`, `use`, and `kid` metadata in addition to `kty`, `crv`, and `x`.
[RFC 8037](https://www.rfc-editor.org/rfc/rfc8037.html#section-2) specifies the OKP
key fields; [RFC 7517](https://www.rfc-editor.org/rfc/rfc7517.html#section-4)
defines the additional JWK metadata.

The supported profile now admits compatible metadata while requiring EdDSA,
signature use, and verification-only operations when those declarations are present.
Private key material, conflicting declarations, and unsupported fields remain
refused. Synthetic regression tests cover both acceptance and contradictions.

## Remaining live gates

- The installed external signer metadata probe and Base Sepolia RPC chain check pass.
- On 2026-10-14, the installed signer was updated to revalidate the seller proof
  through the tracked read-only `scripts/x402_signer_offer.py` bridge. A live
  unpaid compatibility check confirmed a valid offer and an exact match between
  the signer's schema-4 digest and the production application's authorization.
  No payment payload was created. This also exposed an asset-spelling difference:
  contract identity uses the supported canonical USDC address, while the settlement
  profile preserves the advertised spelling. Both now match the application.
- Seven tracked offline bridge checks cover proof equivalence, expiry, revocation,
  redirects, size bounds, unsigned services, and redacted refusal output. Seventeen
  local signer checks cover Python/JavaScript digest vectors, changed proof refusal
  before payment, the real pipe bridge, and exactly one intercepted paid request.
- A paid trial requires fresh approval of the exact request and test-USDC budget,
  with interactive authorization and no automatic retry.
- The separate Groq compatibility and synthetic production-investigator suite now
  passes with the accepted setup in the [model guide](../development/model-providers.md).
