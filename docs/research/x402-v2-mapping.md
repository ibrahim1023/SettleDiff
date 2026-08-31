# x402 v2 Canonical Evidence Mapping

## Scope

This mapping is based on an unsigned challenge captured on 2026-08-31 from a
loopback reference resource server using the official x402 packages:

- `@x402/core==2.23.0`
- `@x402/evm==2.23.0`
- `@x402/express==2.23.0`
- x402 version 2
- `exact` scheme
- Base Sepolia (`eip155:84532`)
- test USDC

The initial request returned HTTP 402 with `PAYMENT-REQUIRED`. No
`PAYMENT-SIGNATURE`, payer key, facilitator settlement, or paid request was used.
The receiving address was synthetic. Raw headers and decoded capture remain under
ignored `.local/x402-captures/` and are not published.

This is reference-protocol evidence, not external-vendor compatibility evidence.

## Evidence classification

| Classification | Meaning |
|---|---|
| `EXACT` | Copied without semantic transformation from validated evidence |
| `TRANSFORMED` | Deterministically converted using identified metadata or a versioned registry |
| `PROVIDER_ASSERTED` | Claimed by the resource server or facilitator but not independently established |
| `INDEPENDENT` | Established by a separate read-only ledger or network source |
| `UNAVAILABLE` | Not present or not representable without guessing |

## Captured challenge mapping

| x402 source | Field | Observed shape | Canonical target | Classification | Current fit |
|---|---|---|---|---|---|
| HTTP response | status | `402` | preflight/challenge evidence | `EXACT` | Preservable as raw evidence; not an `ExecutionRecord` service result |
| `PAYMENT-REQUIRED` | `x402Version` | integer `2` | adapter protocol version | `EXACT` | No canonical field |
| `resource` | `url` | absolute loopback URL | `ExpectedContract.url` | `EXACT` | Fits |
| `resource` | `description` | string | raw contract metadata | `PROVIDER_ASSERTED` | Preservable only in raw evidence |
| `resource` | `mimeType` | MIME string | raw contract metadata | `PROVIDER_ASSERTED` | Preservable only in raw evidence |
| selected requirement | `scheme` | `exact` | payment scheme | `EXACT` | No distinct canonical scheme field; must not be conflated with protocol |
| selected requirement | `network` | CAIP-2 `eip155:84532` | canonical network | `EXACT` | Current `chain` accepts only `base`/`tempo`, so this becomes unknown |
| selected requirement | `amount` | atomic-unit decimal string `1000` | `ExpectedContract.price` | `TRANSFORMED` | Requires independently trusted token decimals and asset identity |
| selected requirement | `asset` | Base Sepolia USDC contract address | canonical asset identity | `EXACT` | Current asset is a symbol string and would become unknown |
| selected requirement | `payTo` | EVM address | advertised recipient | `EXACT` | `ExpectedContract` has no recipient |
| selected requirement | `maxTimeoutSeconds` | positive integer `300` | payment-term expiry/timeout | `EXACT` | No canonical field |
| requirement `extra` | `assetTransferMethod` | absent | transfer mechanism | `TRANSFORMED` | Exact-EVM specification defines absent as `eip3009`; the normalized value must cite that specification |
| requirement `extra` | `name` | `USDC` | token signing/domain metadata | `PROVIDER_ASSERTED` | Must not establish asset identity by itself |
| requirement `extra` | `version` | string `2` | token EIP-712 domain metadata | `PROVIDER_ASSERTED` | Preservable only in raw evidence |
| HTTP request | method | `GET` | authorized request method | `EXACT` | `PaidExecutionRequest` has no method |
| HTTP request | body | absent | authorized body representation | `EXACT` | Current request requires a JSON object body |
| initial challenge | payer | absent | authorized signer/payer | `UNAVAILABLE` | Correctly unavailable before signing |
| initial challenge | transaction reference | absent | payment/transaction reference | `UNAVAILABLE` | Correctly unavailable before submission |
| initial challenge | settlement | absent | provider/independent settlement | `UNAVAILABLE` | Correctly unavailable before submission |

## Deterministic transformations

### Atomic amount

The captured requirement advertises `1000` atomic units. Converting this to
`0.001 USDC` requires all of the following to be established:

1. network `eip155:84532`;
2. the official Base Sepolia USDC token contract;
3. six token decimals;
4. a versioned trusted asset registry or independent metadata lookup.

The resource server's `extra.name == "USDC"` is provider-asserted metadata and is
not sufficient by itself. Binary floating point must not be used.

### Network

`eip155:84532` must remain distinct from Base mainnet (`eip155:8453`). Mapping
both to `base` would discard settlement-critical evidence. CAIP-2 should be
retained as the canonical network identity or mapped through a lossless,
versioned representation.

### Recipient

`payTo` is advertised contract evidence. It must be retained separately from the
recipient later observed in a provider settlement response or independent token
transfer. This enables deterministic advertised/executed/recorded comparison.

## Canonical model gaps established by the capture

The current model cannot represent the captured challenge without losing or
guessing settlement-relevant information:

1. `ExpectedContract` has no advertised recipient.
2. `ExpectedContract` has no payment scheme separate from protocol.
3. `chain` cannot losslessly represent CAIP-2 network identity.
4. asset strings and `Money.unit` do not identify network, token contract, and
   decimals together.
5. `PaidExecutionRequest` has no HTTP method or absent-body representation.
6. authorization cannot bind adapter, x402 version, scheme, selected network,
   asset, recipient, quote, timeout, or the selected payment requirement.
7. there is no canonical adapter/payment-protocol version field.
8. provider settlement and independently observed settlement cannot both be
   represented explicitly in `MachineReport`.
9. payment requirement timeout and transfer method are available only as raw
   evidence.

These gaps do not justify guessed defaults. They require a versioned canonical
model decision before the production adapter boundary is generalized.

## Expected later evidence

The following mappings remain hypotheses until signed testnet evidence is
captured:

| Source | Expected evidence | Proposed canonical role | Classification |
|---|---|---|---|
| `PAYMENT-SIGNATURE` | accepted requirement and payer authorization | ephemeral authorization evidence | provider/client payload; do not persist raw signature |
| final HTTP response | service status/body | execution and service outcome | `EXACT` |
| `PAYMENT-RESPONSE` | success, error reason, transaction, network, payer, optional amount | provider settlement evidence | `PROVIDER_ASSERTED` |
| Base Sepolia RPC | receipt status and chain ID | independent transaction evidence | `INDEPENDENT` |
| USDC transfer log | token, payer, recipient, atomic amount | independent settlement evidence | `INDEPENDENT` |

A successful transaction receipt alone is insufficient. Independent settlement
requires validating the expected token transfer log, token contract, network,
recipient, and amount. Because the facilitator may submit the transaction,
`transaction.from` must not automatically be treated as the payer.

## Sources

- [x402 v2 protocol specification](https://github.com/x402-foundation/x402/blob/main/specs/x402-specification-v2.md)
- [x402 HTTP transport](https://github.com/x402-foundation/x402/blob/main/specs/transports-v2/http.md)
- [x402 exact EVM scheme](https://github.com/x402-foundation/x402/blob/main/specs/schemes/exact/scheme_exact_evm.md)
- [Official Express reference server](https://github.com/x402-foundation/x402/tree/main/examples/typescript/servers/express)
- [x402 network and token support](https://docs.x402.org/core-concepts/network-and-token-support)

## Decision gate result

**Result: the current canonical model cannot represent the captured x402 v2
challenge without losing settlement-relevant distinctions.**

Before implementation continues, accept a minimal schema design covering:

- lossless CAIP-2 network identity;
- network-bound asset identity and decimals;
- advertised recipient;
- payment protocol version and scheme;
- HTTP method and optional body;
- digest-bound selected payment terms;
- provider versus independent settlement evidence;
- report, bundle, database, fixture, CLI, and UI version compatibility.

These decisions were accepted in
[ADR 0007](../decisions/0007-rail-neutral-canonical-payment-evidence.md). Production
model work may proceed test-first; adapter refactoring and payment execution remain
blocked until the canonical schema increment passes its compatibility gates.
