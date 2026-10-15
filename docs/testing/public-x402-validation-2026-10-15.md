# Public x402 validation — 2026-10-15

## Authorized request

The owner approved one GET purchase from
`https://example.x402.goplausible.xyz/evm/weather`, with no body and a maximum of
0.001 Base Sepolia test USDC. The absent-body digest was
`74234e98afe7498fb5daf1f36ac2d78acc339464f950703b8c019892f982b90b`.
Fresh interactive terms confirmed x402 v2, `exact`, Base Sepolia USDC, 1,000 atomic
units, the configured payer, and the advertised recipient. The signer launched
once. No retry occurred.

## Observed outcome

At 06:38 UTC, the paid resource returned HTTP 200. Independent RPC observation
confirmed the exact token transfer against the authorized payer, recipient,
network, asset, and amount. Provider comparison was `MATCH`, with diagnostic
`PROVIDER_SETTLEMENT_CONFIRMED`. The independent observation was `CONFIRMED`, with
diagnostic `EXACT_TRANSFER_CONFIRMED`.

All 13 deterministic findings passed, including budget, quoted price, settlement,
service execution, persisted activity, and the advertised response contract. The
machine verdict was **VERIFIED**. Groq explained the unchanged machine report.
The command required the configured authenticated observability path; dashboard
inspection is not claimed by this record.

## Durable evidence and limits

The saved SQLite report retains `VERIFIED`. Its private evidence bundle exports
and verifies with the same verdict and all 13 findings passing. Local scans of
the database and bundle found neither the wallet Keychain value nor configured
integration secrets. No raw seller JWS was present. Local evidence remains ignored
by Git; no full wallet or transaction identifier is copied into this record.

This resource advertises Bazaar metadata and does not advertise the signed
offer/receipt extension. The run therefore validates ordinary x402 payment,
independent settlement, and delivery. It does not resolve the separate
[PubFi signed-receipt failures](signed-x402-attempt-2026-10-14.md) or claim a live
signed-receipt success. The [earlier public validation](x402-public-endpoint-validation.md)
records the same provider's historical behavior; this record establishes the new
observed result rather than assuming continuing availability.
