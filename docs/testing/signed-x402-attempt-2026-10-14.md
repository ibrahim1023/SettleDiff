# Signed x402 attempt — 2026-10-14

## Authorization and outcome

The owner approved one GET request to
`https://api-stg.pubfi.ai/v1/gateway/alternative-me/fng`, with no body and a maximum
of 0.001 Base Sepolia test USDC. Fresh interactive terms showed 1,000 atomic units,
a verified signed offer, the exact body digest, and a bound settlement profile.
The configured external signer was launched once. No second execution was launched.

The persisted machine verdict is **UNVERIFIABLE**. The signed offer passes, but no
usable execution envelope, provider receipt, or transaction reference reached the
application. Independent settlement is `UNAVAILABLE` with
`NO_TRANSACTION_REFERENCE`; recovery is unresolved without proof of non-submission.
Groq supplied a grounded explanation of that unchanged report. This is not a
successful signed-purchase validation, and it does not establish whether a payment
was submitted.

A bounded read-only RPC search found no matching 1,000-unit transfer from the
approved payer to the approved recipient in the small block window around the
attempt. Absence from that search is not proof of non-submission. The original
report remains unchanged, and no payment retry followed.

## Envelope diagnosis and correction

A fresh unpaid challenge reproduces the output-contract refusal. Besides the
supported seller offer, it includes `signature` keys in extension schema metadata
and a proprietary `pubfi-contract.info.marker`. The result boundary deliberately
admits seller JWS only in its exact offer/receipt envelopes; it refuses these other
namespaces. The retained attempt contains insufficient evidence to determine
whether this was its only failure.

The local signer now projects extensions before returning evidence: the challenge
keeps only `offer-receipt.info.offers`, and settlement keeps only
`offer-receipt.info.receipt`. It drops extension schemas and proprietary markers.
The application boundary remains strict. Eighteen local signer tests pass,
including this real envelope shape represented with synthetic data. Tracked
contract tests preserve refusal of signature-bearing metadata in other namespaces.
The corrected projection passes the result contract on a fresh unpaid staging
challenge. The read-only proof bridge and complete authorization digest also pass
under the application's restricted signer environment. Neither check sends a
payment.

Signer runtime and wallet setup remain ignored local files. No raw JWS, DID
document, payment payload, key, or full wallet/transaction identifier was copied
into this record. The report and private evidence remain in the ignored local
SQLite database. The original record's bundle export refused an `UNKNOWN` settlement finding
that cited absent execution evidence. A regression now prevents future reports
from citing that missing execution; their verdict remains `UNVERIFIABLE` and bundle
verification passes. This historical record was not rewritten or exported as if
it passed. Bundle verification cannot recreate the absent receipt or replay a raw
signature.

## Observability

The live command required authenticated OTLP configuration and emitted redacted
run-state events. A separate synthetic delivery check on the same configuration
received HTTP 200 for traces and metrics. This confirms collector acceptance, not
that the live trace was inspected in the Logfire dashboard. No raw proof or model
content was sent in that check.

## Remaining gate

A successful signed receipt, delivery, and independent-transfer trial is still
outstanding. Another execution requires fresh explicit authorization after review
of the unresolved attempt; this record does not authorize it. No historical finding
or verdict was rewritten to make the trial pass.
