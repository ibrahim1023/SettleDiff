# Decisions Requiring Owner Input

Resolved choices are recorded alongside the remaining gates so implemented testnet behavior is not mistaken for a production decision.

## Unresolved decisions

| Decision | Recommended default | Needed by |
|---|---|---|
| Non-interactive CLI authorization | Keep mandatory interactive confirmation. Add automation only when a concrete use case defines an equally explicit approval mechanism. | Non-interactive post-MVP use case |
| Local artifact retention | Keep sanitized reports until explicit per-run deletion or an owner-applied age purge. Do not add automatic deletion without an operational requirement. | Post-MVP operations |
| Production x402 facilitator | Do not select one from testnet behavior. Require a production endpoint, ownership review, independent settlement plan, and accepted failure contract. | Mainnet design gate |
| x402 mainnet network, asset, recipient, and budget | Keep mainnet disabled. Select all exact terms together and obtain fresh real-money authorization only after a dedicated threat and compatibility review. | Mainnet smoke |
| Release channel | Build and install local artifacts only. Choose package registry, signing, checksums/SBOM publication, and release ownership together. | First public release |
| Hosted deployment target | Keep the MVP loopback-only. Choose hosting, authentication, retention, and secret management together in a new threat model and ADR. | Post-MVP deployment |
| ElevenLabs voice demo | Keep it outside the financial core; add only for a concrete interface use case over the same persisted report. | Post-core demo |

## Resolved choices

| Decision | Accepted choice | Evidence |
|---|---|---|
| Live CLI confirmation | Display target/resource, method, body and payment-terms digests, adapter/version, scheme, network, asset, recipient, quote, timeout, and budget before consuming a one-use capability. | CLI contract and controlled/public x402 cycles |
| x402 signer boundary | SettleDiff stores no wallet key. A separately installed signer owns authority and is invoked once through signer schema 2 with a controlled environment. | External signer contract and persistence scans |
| First x402 network and asset | Support only x402 v2 `exact` on Base Sepolia (`eip155:84532`) canonical test USDC. Keep mainnet and alternative networks/assets unsupported. | Offline corpus, controlled cycle, public endpoint cycle |
| Testnet facilitator compatibility | The official x402 testnet facilitator worked in the controlled and GoPlausible cycles. This is testnet evidence, not a production facilitator selection. | Sanitized live reports |
| Context.dev live boundary | Require Context.dev configuration for live investigations and call it only for an eligible failed-service HTTPS status URL. Context evidence cannot decide financial truth. | Offline contracts and 2026-09-02 live compatibility record |
| Deterministic ownership | PydanticAI/Hyperfusion may select and explain evidence; only deterministic code creates findings and verdicts. | ADRs and architecture tests |

No input is required for the single-agent boundary, fixture-first tests, optional OpenTelemetry, SQLite, or the server-rendered loopback UI; those choices are accepted in the existing ADRs.
