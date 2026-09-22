# Assurance Real-World Validation — 2026-09-22

## Scope and authorization

This validation exercised the Phase 0–6 assurance path against copied historical data, one public unsigned challenge, and a controlled Base Sepolia resource. The owner separately authorized:

- exactly one unsigned GET to the public GoPlausible Base Sepolia weather endpoint;
- exactly one signed GET to the controlled clean-delivery route, capped at `0.001` test USDC;
- exactly one signed GET to a controlled HTTP-500 route, capped at `0.001` test USDC.

No retry was authorized. The two signed submissions therefore had a combined maximum authorization of `0.002` test USDC. This report does not treat testnet tokens as production funds or infer settlement where transaction evidence is absent.

## Historical database migration

A SQLite backup API copy of the ignored 2026-09-01 controlled-cycle database was used; the original database was not migrated or modified. The source copy contained schema versions 1–3, one report, six events, six artifacts, and one explanation.

The first operational rehearsal exposed a migration workflow defect. Opening the copy correctly applied migrations 4–6 and preserved all historical rows, but using `save(report)` to derive the newly introduced timeline replaced omitted events, artifacts, and explanation. This was a misuse of the established replacement API and showed that migration 5 needed an automatic historical backfill path.

Commit `01ef466` adds deterministic open-time backfill for finalized records that have no timeline. It derives events only from the persisted report, run events, and artifacts; leaves unavailable source times null; never rewrites an existing timeline; and leaves the historical report, events, artifacts, and explanation unchanged.

A fresh copy then produced:

- schema versions 1–6;
- byte-identical legacy report, event, artifact, and explanation rows;
- six run-record events, six run-record artifacts, and one run-record explanation;
- 13 source-attributed timeline events;
- identical timeline rows after reopening;
- verdict `VERIFIED` and an available evidence bundle.

The final offline gate at `01ef466` passed with 995 tests and 2 skips, Ruff clean, Pyright with zero errors or warnings, documentation checks passing, and a clean worktree.

## Public unsigned compatibility observation

One authorized unsigned GET was made to the GoPlausible Base Sepolia weather resource. No signer, wallet, RPC settlement lookup, or payment was invoked by this observation.

The current challenge produced:

- `MATCH` for the Bazaar extension presence;
- `MATCH` for the supported primary requirement;
- `MATCH` for GET input method;
- `MATCH` for advertised media type;
- `UNSUPPORTED` for the declaration schema;
- `UNAVAILABLE` for paid evidence;
- overall status `UNSUPPORTED`.

The result preserves the provider declaration as an assertion and does not upgrade absent paid evidence.

## Signer contract correction before payment

The first controlled clean attempt reached the independently owned signer but was refused before submission. SettleDiff now binds the advertised response promise into schema-2 `PaymentTerms`; the local signer still reconstructed the older schema-1 digest. No signed request or payment occurred during that refusal.

The ignored signer was updated to reconstruct schema-2 terms and, when `resource.mimeType` is present, the same canonical response-contract digest as SettleDiff. Its request schema remains 2 and result schema remains 3. Fourteen offline signer/setup tests passed, including the response-bound digest and the case where no response promise is advertised.

The signer source and wallet authority remain independently owned and ignored by Git.

## Controlled clean-delivery cycle

The clean route advertised and the user authorized:

- x402 v2 `exact`;
- Base Sepolia (`eip155:84532`);
- canonical Base Sepolia test USDC;
- GET with no body;
- `application/json` delivery;
- quote and maximum budget `0.001` test USDC;
- one exact recipient and a 300-second challenge timeout.

Exactly one signed request was submitted. Independent receipt and transfer evidence confirmed settlement. The service returned HTTP 200 with a bounded JSON response.

Observed assurance result:

- all 13 deterministic checks passed;
- delivery `SATISFIED` with reason `DELIVERY_SATISFIED`;
- response-contract digest matched the pre-payment promise;
- retry `DO_NOT_RETRY` with confirmed-transfer evidence;
- verdict `VERIFIED`;
- fallback explanation with zero model requests;
- 14 persisted timeline events;
- evidence bundle `AVAILABLE`.

## Controlled HTTP-500 cycle

The separately authorized route used the same rail, network, asset, recipient, media type, price, and budget, but returned HTTP 500 with bounded synthetic JSON after receiving the signed request.

The provider response did not include a settlement transaction reference. With no reference, independent receipt recovery had nothing to query. SettleDiff therefore did not infer that the authorized amount moved and did not promote the result to `PAID_FAILURE`.

Observed assurance result:

- service execution `FAIL` at HTTP 500;
- delivery `FAILED` with reason `HTTP_STATUS_NOT_SUCCESS`;
- settlement `UNKNOWN`;
- paid-failure check `UNKNOWN`;
- retry `REQUIRES_HUMAN_DECISION` with missing-evidence reason;
- verdict `UNVERIFIABLE`;
- no automatic retry;
- 14 persisted timeline events;
- evidence bundle `AVAILABLE`.

The clean transfer proves `0.001` test USDC moved. The second signed submission may or may not have moved another `0.001` test USDC; this report intentionally leaves that unresolved.

## Bundle, publication, and restart inspection

Both real-cycle bundles exported and verified successfully. Their checksums established internal integrity, not publisher authenticity. Changing only the clean bundle's top-level `bundle_sha256` caused verification to fail with `bundle integrity digest does not match its payload`.

Publication to `/tmp` was correctly refused because that macOS path has a symlink ancestor. Publication to canonical `/private/tmp` succeeded for both runs and produced exactly:

- `index.html`;
- `report.json`;
- `public-manifest.json`.

The public outputs contained masked run identifiers and no raw response body, signed payment header or payload, private-key term, mnemonic, seed phrase, loopback URL, full wallet/transaction identifier, or local run ID. The clean public report showed `VERIFIED`, `SATISFIED`, and `DO_NOT_RETRY`; the failed-delivery report showed `UNVERIFIABLE`, `FAILED`, and `REQUIRES_HUMAN_DECISION`.

The local UI was started against the clean database, inspected, stopped, and restarted. Before and after restart the detail page rendered the Purchase assurance and Evidence timeline panels with `VERIFIED`, `SATISFIED`, and `DO_NOT_RETRY`. Report, run-record, event, artifact, explanation, and timeline row counts were unchanged by investigation, publication, HTTP reads, or restart.

## Limitations and follow-up

- The HTTP-500 route did not produce authoritative settlement evidence, so this cycle did not demonstrate a real `PAID_FAILURE`; it demonstrated the required conservative `UNVERIFIABLE` boundary.
- No retry or second failure payment is authorized or safe while the signed submission remains unresolved.
- The public endpoint's current Bazaar declaration schema remains unsupported.
- The controlled resource and signer are local ignored test infrastructure, not a production deployment.
- Base Sepolia facilitator and RPC behavior are external compatibility evidence, not a production facilitator selection or per-run facilitator provenance.
- Bundle hashes do not authenticate a publisher, and public reports remain unsigned and local-only.
- Full transaction references, signatures, payment payloads, wallet keys, and raw live bundles remain local and untracked.
