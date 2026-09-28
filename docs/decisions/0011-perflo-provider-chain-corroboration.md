# ADR 0011: Perflo Provider-Referenced Chain Corroboration

**Status:** Accepted  
**Date:** 2026-09-28

## Context

Perflo v8 can return `settlement.txHash` for credit-funded calls. One controlled 2026-09-28 call showed that the hash refers to a transaction sent by Perflo to pay the vendor, not a debit from the customer's wallet. Perflo's pre-payment vendor declaration does not contain the exact network, token amount, recipient, and payer required by ADR 0010 for independent customer settlement verification. A later hash or provider-declared chain cannot fill that gap. Persisted execution identifiers are masked and cannot safely be used as a new RPC lookup key after the run.

## Decision

An opt-in, user-configured, bounded read-only RPC may query a canonical 64-hex `settlement.txHash` directly from the in-memory Perflo pay result before persistence redacts it. This is *provider-referenced transaction corroboration*, not an `IndependentSettlementObservation` and not customer settlement verification.

For the first release, the scope is Base mainnet (`eip155:8453`) only, and only when Perflo declares `settlement.chain=base`. The observer checks `eth_chainId`, then `eth_getTransactionReceipt`, requires the returned transaction hash to match, and classifies `RECEIPT_SUCCEEDED`, `RECEIPT_REVERTED`, `INDETERMINATE`, or `UNAVAILABLE`. Missing/invalid references, non-Base chain claims, malformed responses, network mismatch, and pending receipts remain non-conclusive. A reverted receipt contradicts Perflo's `finalized` claim; a successful receipt is *not* a match or proof of delivery, transfer, customer charge, or pay-to identity. The implementation does not infer an ERC-20 transfer from the receipt status or from arbitrary log shapes.

The result is a separate redacted `evm_rpc.provider_transaction` evidence artifact, with stable status and diagnostic, the claimed and observed chain IDs when available, the masked reference, and a comparison code. It does not change `MachineReport.ledger`, `independent_settlement`, `settlement_comparison`, findings, verdict, delivery, or retry classification. RPC failures cannot trigger a new paid request or make the existing investigation fail. No RPC URL or credential is persisted or published. Without an explicitly configured Perflo RPC, no query is made and an unavailable artifact records why.

## Consequences

- Provider-only Perflo runs remain `UNVERIFIABLE` even if an external receipt succeeds.
- A reverted receipt can surface a concrete contradiction to Perflo's provider claim without overstating what a successful receipt proves.
- A receipt may describe a batch, sweep, or unrelated transaction; its existence alone does not establish the vendor's payment or the customer's credit debit.
- Reports from earlier schema versions remain unchanged because corroboration is a separate evidence artifact, not a new verdict input.

## Rejected

- Use Perflo's `tx status` as the external observer: it remains in the provider trust domain.
- Treat a successful receipt as a verified payment: no exact pre-authorized transfer profile exists.
- Discover or query transactions using masked persisted hashes: truncation cannot be reversed safely.
- Read arbitrary chain IDs or RPC URLs from post-payment provider fields: the user must configure the read-only source, and non-Base network support needs an explicit reviewed contract.
