# PR 2 Live Validation — 2026-09-28

## Scope and authorization

The owner separately authorized one controlled x402 Base Sepolia payment and one Perflo v8 catalog payment. Neither path was retried. This record contains only masked identifiers and bounded outcomes; signer authority, payment payloads, raw credentials, and unmasked wallet addresses remain local.

An earlier x402 attempt failed during preflight because the local reference server had stopped. No signed request or payment was sent by that attempt.

## x402 controlled Base Sepolia cycle

The controlled resource was the loopback `GET /weather` route. The exact authorization was `0.001` test USDC. SettleDiff displayed payment-terms digest `2c923e81…4fe8` and settlement-profile digest `ae22c88f…e0c7` before confirmation.

The ignored, independently owned local signer was updated before the payment to reconstruct `PaymentTerms` schema 4, including the mandatory pre-authorized payer and canonical settlement-profile digest. Its offline suite passed 14 tests, including a Python/JavaScript cross-language digest vector. Exactly one signed GET was then submitted.

Observed deterministic result:

- verdict `VERIFIED`;
- all 13 checks `PASS`;
- delivery `SATISFIED`;
- independent settlement `CONFIRMED` with diagnostic `EXACT_TRANSFER_CONFIRMED`;
- chain, asset, amount, recipient, and payer dimensions all verified;
- payer policy `REQUIRED`;
- provider comparison `MATCH`;
- report ledger equal to the observation ledger;
- no `provider_activity` record;
- retry `DO_NOT_RETRY`;
- transaction `0x43dd…753c`.

Read-only balance observations around the one payment were:

| Account | Before | After |
|---|---:|---:|
| Payer | 19.996000 test USDC | 19.995000 test USDC |
| Recipient | 0.003000 test USDC | 0.004000 test USDC |

## Perflo v8 credit-funded cycle

The local Perflo CLI was upgraded from `4.1.0` to `8.0.0` using the package tarball whose verified SHA-256 was `4482e2d6…1210`. Read-only readiness showed account credit of `17.504 USD` and wallet headroom of `0.00 USD`.

The selected catalog vendor was `ottoai-filtered-news`:

- advertised price `0.001 USD`;
- `maxChargePerCall` `0.001 USD`;
- vendor contract digest `66253b03…0806`;
- payment-terms digest `9c5f6653…3075`.

A dry run was declined at the authorization prompt. The owner then separately authorized one call. The second vendor observation matched the authorized terms before the paid command. Perflo reported:

- result `succeeded`;
- charge `0.001 USD`;
- funding source `credit`;
- upstream HTTP 200;
- provider settlement status `finalized`;
- provider settlement flow `authorization`;
- no SettleDiff retry.

The SettleDiff report remained conservative:

- verdict `UNVERIFIABLE`;
- independent settlement `UNAVAILABLE` with diagnostic `SETTLEMENT_PROFILE_UNAVAILABLE`;
- provider comparison `NOT_COMPARABLE`;
- retry `REQUIRES_HUMAN_DECISION`;
- no automatic retry.

A read-only Base mainnet lookup of provider `settlement.txHash` `0xa5bb…bbad` showed successful status and one USDC transfer of 1000 atomic units from `0xdeaf…b943` to `0x0e84…b808`; the transaction sender was `0xb87e…5860`. None matched the customer's masked Perflo addresses `0x2e3e…3084` or `0x01d7…d9b0`.

The bounded conclusion is that this hash refers to Perflo's vendor settlement from a Perflo-operated wallet. It is useful provider-side evidence for execution-to-Activity correlation, but it is not the customer's transfer and cannot establish independent settlement. The customer debit remains provider-ledger evidence.

## Defects exposed and fixed

The pre-fix report preserved uncertainty correctly but lost useful provider correlation evidence:

1. provider settlement status `finalized` normalized to `UNKNOWN`;
2. credit funding suppressed `settlement.txHash`;
3. execution and Activity did not match because the Activity row had a different `id`, no `transactionId`, and only the shared settlement hash;
4. Perflo adapter evidence omitted protocol version, so the authorization banner printed `Version: unknown`.

The follow-up regression fixtures and tests preserve these v8 shapes with synthetic identifiers. The fixes retain the provider-side hash, normalize `finalized`/`submitted`, match canonical hashes case-insensitively, and label all Perflo adapter evidence as protocol version 8 without promoting provider evidence into independent settlement.
