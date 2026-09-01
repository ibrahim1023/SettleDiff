# Controlled x402 Base Sepolia Live Cycle — 2026-09-01

## Scope

This cycle exercised the committed x402 v2 adapter against a controlled loopback resource server using the official x402 testnet facilitator and independent Base Sepolia RPC evidence. It authorized one exact `0.001 USDC` payment and did not retry.

This was not validation against an unrelated public x402 endpoint. That remains a separate compatibility step.

## Authorized terms

| Field | Authorized value |
|---|---|
| Rail / version | x402 v2 |
| Scheme | `exact` |
| Network | `eip155:84532` |
| Resource | `http://127.0.0.1:4021/weather` |
| Method / body | GET / absent |
| Body digest | `7423…90b` |
| Terms digest | `f466…a189` |
| Asset | Base Sepolia USDC, `0x036C…F7e` |
| Atomic amount | `1000` (0.001000 USDC) |
| Maximum budget | 0.001 USDC |
| Payer | `0x7ACe…3F92` |
| Recipient | `0xCd50…2200` |
| Challenge timeout | 300 seconds |
| Facilitator | `https://x402.org/facilitator` |
| Independent RPC | `https://sepolia.base.org` |

The unpaid challenge was run first and explicitly declined. The displayed terms matched the expected controlled configuration. Fresh authorization was then obtained for exactly one signed submission.

## Observed result

- The resource returned HTTP 200 with the controlled synthetic weather response.
- The provider settlement response reported success and a transaction reference, but omitted the settled amount.
- The independent transaction receipt had status 1 and two logs.
- Exactly one log matched the selected USDC contract and `Transfer` event.
- The decoded transfer payer, recipient, and atomic amount matched the authorized terms.
- The transaction reference matched across provider, execution, and independent evidence.
- The payer balance changed from 20.000000 to 19.999000 test USDC.
- The recipient balance changed from 0.000000 to 0.001000 test USDC.
- All 12 deterministic checks passed and the final verdict was `VERIFIED`.
- The deterministic fallback explanation was used with zero model requests.

The transaction reference is retained only in local evidence and is abbreviated here as `0x7d9d…37bd`.

## Submission and recovery behavior

The signer performed one unsigned challenge request and one request containing `PAYMENT-SIGNATURE`. Its local guard rejects any second signature-bearing request. SettleDiff also performed unsigned preflight and immediate pre-sign challenge validation. No retry path was invoked.

Independent verification did not equate receipt existence with settlement. It checked chain ID, receipt status, selected token contract, transfer topic shape, payer, recipient, and atomic amount. The additional unrelated receipt log did not create ambiguity.

## Secret and persistence checks

The payer and recipient keys were generated for Base Sepolia only and stored as separate macOS Keychain generic-password items. SettleDiff configuration contained the signer command and masked RPC URL, never a wallet key.

After persistence to an ignored local SQLite database:

- neither Keychain private key was present;
- no `PAYMENT-SIGNATURE`, payment payload, private-key field, mnemonic, or seed phrase was present;
- provider receipt and independent ledger evidence remained separate;
- wallet and transaction identifiers were masked;
- the persisted verdict remained `VERIFIED`.

Raw local setup metadata, the SQLite database, and signer runtime remain under `.local/` and are ignored by Git.

## Offline regressions

The live cycle added two sanitized regression properties:

1. `x402-clean-success` now proves that provider success without a provider-reported amount remains verifiable when the independent transfer supplies the exact amount.
2. Receipt verification now proves that unrelated logs do not interfere with selecting exactly one matching USDC transfer.

No raw signature, payment payload, private key, or full live transaction identifier was copied into fixtures or documentation.

## Limitations

- This was one controlled Base Sepolia sample, not broad compatibility evidence.
- The seller was a loopback reference server, not an independent public service.
- The public facilitator and RPC are external dependencies whose future availability is not guaranteed.
- Mainnet, x402 v1, non-`exact` schemes, other assets, and other networks remain unsupported.
