# Security and Data Handling

## Assets

SettleDiff may encounter API credentials, paid request bodies, service responses, receipts, wallet/recipient identifiers, transaction/session/device identifiers, activity records, and model prompts/tool results.

## Trust boundaries

- local user input;
- Hyperfusion model API;
- Perflo executable and backend;
- x402 resource/facilitator headers and independently owned external signer;
- Context.dev live evidence service;
- local SQLite/filesystem;
- browser rendering;
- optional OpenTelemetry exporter.

## Required controls

### Secrets

- Load secrets through environment-backed settings.
- `.env` is ignored; `.env.example` contains names and safe descriptions only.
- Never persist or include secrets in exceptions, prompts, fixtures, reports, or telemetry.
- Fail startup for a requested live feature whose credential is absent.

### Paid execution

- Validate URL, JSON body, and explicit budget before authorization.
- Bind authorization to run, target, method, canonical request-body digest, exact budget, and the versioned selected payment-terms digest (adapter/version, scheme, network/chain, asset, recipient, quote, timeout, and resource URL). The authorization prompt shows full public asset/recipient identifiers and timeout so the owner can verify every bound term; persistence, telemetry, and ordinary report views remain masked.
- Consume authorization with the same payment terms and revalidate the terms immediately before invoking a payment-rail adapter.
- Permit at most one paid execution per live run.
- Never retry on uncertain submission; verify history/status and ask before a new run.
- Keep fixture replay structurally unable to acquire a paid capability.

### Subprocess

- Resolve an allowlisted Perflo executable.
- Pass arguments without a shell.
- Set timeout, output limit, controlled environment, and working directory.
- Capture streams with `communicate` and terminate/kill predictably on timeout.
- Treat malformed output as evidence failure, not permission to fall back to text scraping.

### x402 headers

- Treat `PAYMENT-REQUIRED` and `PAYMENT-RESPONSE` as untrusted bounded input.
- Enforce encoded-header, decoded-JSON, and nesting-depth limits before strict validation.
- Support only x402 v2 `exact` on Base Sepolia test USDC until another contract is accepted.
- Treat `PAYMENT-SIGNATURE`, nested signatures, and reusable payment payloads as secret-bearing ephemeral material; never persist, display, export, log, or emit them through telemetry.
- Invoke an independently owned signer at most once through the versioned bounded JSON contract. SettleDiff passes a controlled environment that does not inherit private-key variables; the signer must obtain authority independently without returning it.
- Reject oversized signer input before launch; treat timeout, malformed/secret-bearing output, output overflow, and non-proven post-launch failure as submission uncertainty.
- A provider settlement response remains provider-asserted evidence and cannot replace independent settlement verification.
- Independent x402 verification permits only `eth_chainId` and `eth_getTransactionReceipt`, with bounded request count and response size and no retry/poll loop. The configured RPC is independent of the resource/facilitator but is still untrusted input: chain identity, receipt structure, token address, event signature, payer when supplied, recipient, and amount are validated rather than accepted by source reputation.
- Receipt success alone is insufficient: require the expected Base Sepolia chain plus exactly one matching USDC transfer event for token, payer when supplied, recipient, and amount. The facilitator transaction sender is not the payer.
- A mined reverted transaction proves submission, not non-submission. Missing/pending receipts, malformed evidence, and RPC failure remain unresolved; only an explicit pre-transmission result or separately proven non-submission may set the non-submission state.
- Once an external signer process launches, that client instance cannot launch again, including after timeout, overflow, malformed output, or another uncertain failure.
- SettleDiff configuration contains only a signer command and RPC URL, never a wallet key; both fields are hidden from configuration representations, the potentially credential-bearing RPC URL uses `SecretStr`, secret-bearing command arguments are rejected, and no private-key setting exists.
- x402 live composition requires explicit rail selection, environment and CLI testnet gates, and the same interactive exact-request authorization. No gate bypasses confirmation.
- The resource client does not follow redirects. Remote targets require HTTPS; x402 permits HTTP only for parsed loopback hosts without credentials or fragments. It repeats the unsigned challenge immediately before signing; pre-launch drift fails without signer invocation, while post-launch signer/provider contradictions preserve the transaction reference and force settlement unknown.

### Data minimization

- Preserve raw payload only when it serves replay/debugging.
- Redact before persistence and again before display/export.
- Mask identifiers by default; explicit local expansion is auditable.
- Send the model normalized summaries and artifact handles, not unrestricted raw data.
- Keep reports local for MVP. Retain sanitized reports until explicit per-run deletion or an owner-applied age purge; do not retain x402 signatures, reusable authorizations, signer secrets, or raw live captures in the report database.
- Export only redacted artifacts and integrity-checked compatibility metadata. Bundle export never upgrades provider evidence into independent evidence and never exports signer material.

### Web UI

- Bind to loopback by default.
- Escape all provider/model content in templates.
- Do not render raw HTML returned by services or the model.
- Apply CSP and safe response headers.
- Use POST plus CSRF protection for future state-changing UI actions.

### Fixtures

- Use synthetic identifiers and fixed timestamps.
- Scan for private keys, tokens, credentials, emails, unmasked addresses, and unexpected entropy.
- Record provenance as a scenario description, never a real account identity.

## Security invariants

1. Model output cannot alter machine findings.
2. Missing evidence cannot become success.
3. A consumed or mismatched authorization cannot execute.
4. Fixture mode cannot spend.
5. An uncertain mutation cannot be retried automatically.
6. Telemetry failure cannot alter the financial result.
7. External content is untrusted at parsing, prompting, logging, and rendering boundaries.

## Deferred threat model

A hosted, shared, remotely accessible, or unattended SettleDiff deployment requires a dedicated threat model covering authentication, tenant isolation, SSRF, database access, key management, audit retention, rate limits, and abuse prevention. Local MVP controls are not sufficient for that deployment.
