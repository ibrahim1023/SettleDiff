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

The later GoPlausible public-endpoint cycle established one external compatibility case: a strict supported Base Sepolia requirement at index 0 may be followed by bounded unsupported network alternatives. Selection remains pinned to index 0, and an unsupported primary requirement fails closed.

## Evidence classification

| Classification | Meaning |
|---|---|
| `EXACT` | Copied without semantic transformation from validated evidence |
| `TRANSFORMED` | Deterministically converted using identified metadata or a versioned registry |
| `PROVIDER_ASSERTED` | Claimed by the resource server or facilitator but not independently established |
| `INDEPENDENT` | Established by a separate read-only ledger or network source |
| `UNAVAILABLE` | Not present or not representable without guessing |

## Captured challenge mapping

| x402 source | Field | Canonical target | Classification | Implemented handling |
|---|---|---|---|---|
| HTTP response | status `402` | preflight/challenge evidence | `EXACT` | retained as bounded adapter evidence, not service execution |
| `PAYMENT-REQUIRED` | `x402Version: 2` | adapter protocol version | `EXACT` | retained in `AdapterEvidence.protocol_version` and compatibility metadata |
| `resource` | URL | `ExpectedContract.url` | `EXACT` | must equal the authorized target |
| selected requirement | `scheme: exact` | payment scheme | `EXACT` | retained separately from protocol |
| selected requirement | `network: eip155:84532` | canonical network | `EXACT` | retained losslessly and mapped to legacy Base chain only for compatibility |
| selected requirement | atomic amount | `ExpectedContract.price` | `TRANSFORMED` | converted with the canonical Base Sepolia USDC identity and six decimals |
| selected requirement | asset contract | canonical asset identity | `EXACT` | bound to network, contract address, symbol, and decimals |
| selected requirement | `payTo` | advertised recipient | `EXACT` | retained for expected/executed/recorded comparison |
| selected requirement | timeout | payment-term timeout | `EXACT` | retained and bound into authorization |
| requirement `extra` | transfer method | raw/normalized contract evidence | `TRANSFORMED` | absent means EIP-3009 only under the accepted exact-EVM contract |
| requirement `extra` | token name/version | provider metadata | `PROVIDER_ASSERTED` | cannot establish asset identity by itself |
| HTTP request | method and optional body | `PaidExecutionRequest` | `EXACT` | GET requires no body; POST retains the bounded JSON value |
| initial challenge | payer/transaction/settlement absent | unavailable evidence | `UNAVAILABLE` | remains unavailable without guessing |

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

## Canonical model result

Report schema 2 resolved the capture-established gaps without changing schema-v1 meaning. It added lossless network and asset identity, recipient and timeout fields, HTTP method and optional-body authorization, adapter provenance, and separate provider receipt versus independent ledger evidence. The selected payment terms are hashed into the one-use capability and revalidated immediately before signer launch.

Unavailable fields remain `None`/unknown rather than being inferred. Compatibility readers continue to accept schema-v1 reports and bundles that predate x402 metadata.

## Signed and settlement evidence

The controlled and public Base Sepolia cycles established these mappings:

| Source | Evidence | Canonical role | Classification |
|---|---|---|---|
| `PAYMENT-SIGNATURE` | accepted requirement and payer authorization | ephemeral transport only; never persisted | provider/client payload |
| final HTTP response | service status/body | execution and service outcome | `EXACT` |
| `PAYMENT-RESPONSE` | success, reason, transaction, network, payer, optional amount | provider receipt | `PROVIDER_ASSERTED` |
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

The minimal rail-neutral schema was accepted in [ADR 0007](../decisions/0007-rail-neutral-canonical-payment-evidence.md) and implemented across reports, bundles, SQLite round trips, fixtures, CLI, and UI. Perflo and x402 now feed the same deterministic checks. Compatibility is bounded to the demonstrated x402 v2 exact/Base-Sepolia/test-USDC profile; other networks, assets, schemes, primary requirement shapes, and mainnet remain unsupported until a separate contract is accepted.
