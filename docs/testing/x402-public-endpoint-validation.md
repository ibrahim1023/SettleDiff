# Public x402 Endpoint Validation — 2026-09-02

## Endpoint selection and ownership

The selected endpoint was the EVM weather route operated by GoPlausible:

- public test page: `https://example.x402.goplausible.xyz/`;
- paid resource: `https://example.x402.goplausible.xyz/evm/weather`;
- operator site: `https://www.goplausible.com/`;
- environment: testnet;
- advertised price: 0.001 USDC.

The operator’s public page identifies the route as an Ethereum / Base Sepolia x402 test endpoint and links to the GoPlausible facilitator documentation. This validation does not imply endorsement or continuing availability.

## Unpaid compatibility observations

Each external request was separately authorized and no unpaid request launched the signer.

The first challenge failed closed before signing because its valid x402 v2 `accepts` array contained three network alternatives:

1. Base Sepolia EVM at index 0;
2. Algorand testnet;
3. Solana devnet.

SettleDiff previously required every alternative to match the narrow EVM model. The offline parser contract was changed to retain bounded unsupported alternatives while requiring the selected index to be a strict supported `PaymentRequirements`. Selection remains pinned to index 0. An unsupported primary requirement still fails before signer invocation.

A second unpaid request verified that the revised parser selected the EVM requirement. A third separately authorized unpaid request displayed every authorization field in full; payment was declined both times.

## Authorized payment terms

| Field | Authorized value |
|---|---|
| x402 version | 2 |
| Selected requirement | index 0 |
| Scheme | `exact` |
| Network | `eip155:84532` |
| Resource | `https://example.x402.goplausible.xyz/evm/weather` |
| Method / body | GET / absent |
| Body digest | `7423…90b` |
| Terms digest | `6e4c…cd0e` |
| Asset | Base Sepolia USDC, `0x036C…F7e` |
| Atomic amount | `1000` (0.001000 USDC) |
| Maximum budget | 0.001 USDC |
| Payer | `0x7ACe…3F92` |
| Recipient | `0xcccB…2605` |
| Maximum timeout | 300 seconds |
| Independent RPC | `https://sepolia.base.org` |

Fresh authorization was obtained for exactly one signature-bearing request. No retry was authorized.

## Observed paid result

- The public resource returned HTTP 200 with a weather report.
- The provider settlement response reported success and a transaction reference but omitted amount.
- The independent Base Sepolia receipt had status 1 and two logs.
- Exactly one log matched the selected USDC contract and transfer event.
- Decoded payer, recipient, and atomic amount matched the authorized terms.
- The payer balance decreased from 19.999000 to 19.998000 test USDC.
- All 12 deterministic checks passed.
- The final verdict was `VERIFIED`.
- The deterministic fallback explanation used zero model requests.

The transaction reference is retained only in ignored local evidence and abbreviated here as `0x0ec1…d280`.

## Persistence and secret checks

Safe evidence was persisted to an ignored local SQLite database. Post-run scans established that:

- the payer Keychain value was absent;
- no `PAYMENT-SIGNATURE` or payment payload was present;
- no private-key field was present;
- wallet and transaction identifiers were masked;
- provider receipt and independent ledger evidence remained separate;
- the persisted verdict remained `VERIFIED`.

## Offline regressions

The public validation added:

- a synthetic multi-network v2 challenge fixture with supported EVM primary and bounded unsupported alternatives;
- parser tests that preserve alternatives without selecting them;
- normalization tests that reject an unsupported primary requirement;
- authorization-output tests for full public asset reference, recipient, and timeout.

No live signature, payment payload, Keychain value, full transaction reference, or raw public challenge was committed.

## Compatibility statement

Demonstrated support is limited to x402 v2, selected requirement index 0, `exact`, Base Sepolia, canonical test USDC, GET without a body or POST with a bounded JSON value, and EIP-3009. A challenge may contain bounded unsupported alternatives after a supported primary entry. This does not establish support for selecting Algorand, Solana, other EVM networks, other assets, other schemes, mainnet, or arbitrary requirement ordering.
