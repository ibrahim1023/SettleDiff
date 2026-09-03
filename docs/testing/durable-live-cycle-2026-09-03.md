# Durable Controlled Live Cycle — 2026-09-03

## Scope

This cycle validated the schema-4 durable run ledger, active UI visibility, schema-2 external signer readiness, one controlled Base Sepolia payment, independent settlement verification, final report persistence, and restart-free UI observation.

The purchased resource was the controlled loopback x402 reference weather route. Its response is synthetic; the payment and Base Sepolia settlement are real testnet activity.

## Readiness and authorization

`settlediff doctor --rail x402` established before authorization that:

- the selected SQLite database was writable;
- Context.dev configuration was present;
- the independent signer reported schema 2 and payer `0x7ACe…3F92`;
- the read-only RPC reported Base Sepolia chain ID `0x14a34`.

An unsigned preflight was declined first and appeared in the already-running UI as a durable `controlled_live` run with state `refused` and no final report.

Fresh authorization then covered exactly:

| Field | Authorized value |
|---|---|
| Resource | `http://127.0.0.1:4021/weather` |
| Method / body | GET / absent |
| x402 version / scheme | 2 / `exact` |
| Network | `eip155:84532` |
| Asset | Base Sepolia test USDC, `0x036C…CF7e` |
| Payer | `0x7ACe…3F92` |
| Recipient | `0xCd50…2200` |
| Amount and maximum | 0.001000 test USDC |
| Terms digest | `f4665419…a0a189` |
| Signer launches | one |
| Automatic retries | zero |

The live run appeared in the open UI as `preflight / Pending` before confirmation.

## Result

- The signer launched once and returned schema-2 payer attribution.
- The controlled service returned HTTP 200.
- Provider settlement reported `settled`.
- Read-only verification matched Base Sepolia, canonical test USDC, payer, recipient, and atomic amount.
- The persisted independent transaction reference is masked as `0x2ca5…69c7`.
- All 12 deterministic checks passed.
- The final verdict was `VERIFIED`.
- The deterministic fallback explanation used zero model requests and zero tool calls.
- No Context.dev request was eligible.

The payer balance changed from 19.998 to 19.997 test USDC, exactly matching the authorized 0.001 payment.

## Durability and UI evidence

The finalized run `live_cc39…ec89` retained:

- provenance `controlled_live`;
- terminal state `complete`;
- six ordered events;
- six redacted artifacts;
- the deterministic report and fallback explanation;
- no failure record.

The already-running UI observed both the refused preflight and the paid run without restart. The paid run moved from pending to `VERIFIED` through the shared SQLite ledger.

Bundle export and checksum/internal-consistency verification passed. A database scan found no Keychain payer value, `PAYMENT-SIGNATURE`, payment payload, private-key field, or mnemonic. Bundle verification does not authenticate origin.

## Operational defects resolved before the cycle

Manual testing exposed and drove regressions for:

- blank verdict filters returning a raw 422 response;
- duplicate recovery artifacts rolling back final persistence;
- treating a configuration script as an executable signer launcher;
- reports existing only after finalization rather than before preflight;
- reverted transactions being labeled as not submitted.

The current local signer launcher is independently owned and ignored by Git. Publication still requires a separate signer repository and release owner.
