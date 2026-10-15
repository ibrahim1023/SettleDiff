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

## Follow-up preflight

A full synthetic test through the locally installed official SDK, external-process
result validator, signed adapter, independent RPC observer, deterministic checks,
SQLite, and bundle verification reproduces refusal for the original extension
tree and returns `VERIFIED` after projection. All 17 checks pass in the corrected
path. The two SDK resource requests are intercepted, the buyer and seller keys
are synthetic, and no external payment request is sent. This is local runtime
validation rather than a live purchase or ordinary CI coverage.

A fresh unpaid production-adapter inspection still verifies the staging offer at
0.001000 test USDC. A read-only token balance check confirms sufficient funds for
that budget. Neither observation establishes settlement or authorizes execution.

## Fresh live attempts and payment rejection

Fresh owner-authorized attempts at 06:09 and 06:17 UTC used the corrected output
projection. Both retained real execution evidence with HTTP 400 and
`x402.invalid_payment`. Their reports remain `UNVERIFIABLE`: neither response
supplied a transaction reference or signed receipt. Both private bundles now
export and verify consistently without raw JWS. These results resolve the original
output-loss symptom; they do not establish successful settlement.

The second attempt aligned the local runtime with the
[official PubFi example's client dependencies](https://github.com/helixbox/pubfi-docs/blob/main/examples/agents/x402-base-sepolia/package.json):
`@x402/core`, `@x402/evm`, and `@x402/fetch` 2.26.0, plus viem 2.56.0. Alignment alone
did not resolve the rejection. The official
[failure-action guide](https://docs.pubfi.ai/getting-started/x402#failure-actions)
describes `invalid_payment` as a rejection; its generic message does not identify
which validation rule failed.

A read-only protocol check with a synthetic buyer confirmed mathematical signature
validity, a matching on-chain token domain separator, unchanged accepted terms,
matching recipient and amount, preserved vendor marker, and agreement between the
unpaid header and body. It sent no payment request. Narrow block-range searches
found no matching transfer near the rejected attempts, and the payer remains
funded. Those searches do not prove non-submission.

A further compatibility hypothesis concerns the SDK's epoch-zero `validAfter`.
The local signer now signs and serializes the same shorter authorization window,
using a five-second past-clock allowance and the advertised timeout as the total
window length. The supported local timeout range is 12–300 seconds. It never
extends the advertised expiry horizon or spending limit. A new test failed on the
unbounded interval, then passed after the correction; it independently verifies
the resulting typed signature. All 19 local signer checks and the 17-check
synthetic end-to-end pipeline pass. The seller's generic error does not identify
the rejected validation rule.

## Bounded-window live trial

The owner approved one bounded-window purchase under the same exact target,
no-body digest, and 0.001 test USDC budget. The first invocation stopped before
signer launch because the signed offer changed between preflight and execution;
it sent no paid request. A fresh invocation under that unused purchase approval
passed reinspection and sent one paid request at 06:50 UTC.

The seller again returned HTTP 400 `x402.invalid_payment`, without a transaction
reference or signed receipt. The retained report is `UNVERIFIABLE`, independent
settlement is `UNAVAILABLE (NO_TRANSACTION_REFERENCE)`, and submission remains
unresolved. Its private bundle exports and verifies with the same verdict and no
raw JWS. The bounded-window change did not resolve the live rejection; it must not
be described as the proven cause or a successful payment fix. No further paid
request followed this rejection.

A read-only search across 25 blocks around the attempt found no matching
1,000-unit transfer, and the payer still has sufficient test USDC. This bounded
search does not prove non-submission or change the persisted verdict.

Comparing the installed EIP-3009 client against the official HTTP example confirms
that its base signing path requires only an address and typed-data signing. The
example's additional read-only client capabilities serve extension flows; their
absence alone does not explain this EIP-3009 rejection. A successful synthetic
pipeline still does not establish acceptance by the live seller. Further diagnosis
needs a specific failing validation rule from the seller or a reproducible
compatibility difference before another paid trial.

## Further compatibility trials

The owner then requested continued corrected attempts. An intercepted comparison
using one live unpaid challenge and a synthetic buyer found matching request body,
accepted terms, resource, and extension contents between the manual signer and
the official fetch wrapper, apart from nonces, signatures, and the deliberately
bounded validity window. The manual request now also carries the wrapper's
`Access-Control-Expose-Headers` header. This alignment did not establish live
acceptance: a diagnostic trial at 07:16 UTC retained another HTTP 400.

Unpaid inspection exposed a second deadline: the signed offer and vendor marker
share an expiry that can precede the advertised 90-second timeout. The signer now
caps authorization to five seconds before the verified offer expires, and refuses
before submission when fewer than 12 seconds remain. A regression failed before
that cap and passed afterward. This fixes an observable permission-lifetime
mismatch; it does not prove why the seller rejected earlier requests.

Trials at 07:19, 07:26, and 07:27 UTC returned HTTP 402 challenges. The first exposed
another output-contract failure: its parsed service body duplicated signature
material outside the permitted seller-proof namespaces. A regression now keeps
the response status and byte count while omitting the parsed signed challenge
body. Later reports retain the HTTP 402 outcome instead of losing execution
evidence. All 22 installed runtime tests and the 17-check synthetic pipeline pass.
No financial verifier was relaxed.

The 07:27 response carried `PAYMENT-REQUIRED`, without `PAYMENT-RESPONSE`, an error
reason, or transaction reference. Independent settlement remains unavailable;
these are not successful purchases. Narrow searches around each submitted trial
found no matching transfer, including a payer-only check after the last trial.
They do not prove non-submission. Separate attempts refused on offer drift or
insufficient remaining lifetime before signing or submission; they were not
counted as successful live runs.

The configured buyer's typed signature verifies locally. Public reads show an
ordinary account, no token pause, no buyer blacklist, and adequate test funds.
A proposed signature-bearing RPC simulation was rejected by automatic approval
review because it could expose a spend-capable authorization to the RPC operator.
It was not run. The replacement check verified the signature locally and sent
no authorization to another service.

An alternative current route, GET
`https://api-stg.pubfi.ai/v1/gateway/beefy/articles/latest`, advertises the same
0.001 test USDC price and passes production-adapter signed-offer inspection.
No purchase of that different exact target is authorized by this record.

A further single trial at 07:33 UTC combined the official SDK's epoch-zero start
with the verified-offer expiry cap. It also returned HTTP 402 without a settlement
response or error reason. The runtime preserves that SDK start while bounding the
future expiry; the earlier recent-start policy did not resolve acceptance. A
07:37 trial removed only extension schemas while preserving both signed `info`
objects; it also returned HTTP 402. That unsuccessful wire projection was removed,
and the original SDK extension echo remains. These experiments do not justify
changing financial findings or claiming a successful live purchase.

## Authorized alternative route and historical token diagnostic

The owner separately approved up to five GET trials of
`https://api-stg.pubfi.ai/v1/gateway/beefy/articles/latest`, no body, maximum 0.001
test USDC per trial and 0.005 total. All five authorized trial slots were used;
that is an authorization cap, not a claim that 0.005 USDC settled.

| UTC time | Trial | Retained HTTP status |
| --- | --- | --- |
| 08:38:53 | Existing offer-bounded signer | 402 |
| 08:40:32 | Official SDK signing with advertised 90-second future expiry | 400 |
| 08:45:58 | Omit only nonstandard `resource.serviceName` | 400 |
| 08:48:19 | Raw HTTP/1 transport with payment-header capitalization | 402 |
| 08:51:21 | Existing signer with private provider correlation capture | 402 |

The HTTP 400 bodies contain `x402.invalid_payment`. HTTP 402 returns another
challenge, without a signed receipt or transaction reference. All five reports
remain `UNVERIFIABLE`; all five private bundles verify that unchanged result and
exclude raw JWS. Each trial sent at most one paid request. Failed compatibility
projections were reverted rather than kept as fixes. A private correlation ID was
retained separately for provider-side diagnosis, without payment payloads or keys.

After automatic approval review rejected signature-bearing RPC diagnostics, the
owner explicitly approved one historical read-only `eth_call` on the official Base
Sepolia RPC. The script required the authorization to be expired against the
current chain timestamp before signing or RPC egress. At the selected historical
block, the token accepted `transferWithAuthorization` from the configured buyer to
the advertised payee for 1,000 units with the advertised EIP-712 domain. The local
signature check also passed. No transaction was broadcast. This validates the
basic token path at that historical state, not current provider intake, receipt,
delivery, or settlement.

Narrow read-only payer searches after the trials found no matching transfer;
absence still does not prove non-submission. The next useful evidence is the
provider's validation/facilitation-stage diagnostic for these UTC windows.

## Provider report

The owner approved publishing the prepared redacted report. GitHub refused issue
creation in `helixbox/pubfi-issue-report` because that repository is archived.
The owner then separately approved PubFi's active documentation Q&A channel. The
report is published as [PubFi discussion 94](https://github.com/helixbox/pubfi-docs/discussions/94).
It contains the routes, UTC trial windows, SDK versions, and bounded diagnostic
results, without wallet addresses, payment signatures, raw seller proofs, keys,
transaction identifiers, or the private provider correlation ID.

The next needed evidence remains the provider's validation/facilitation-stage
diagnostic. Publication does not establish the cause, successful settlement, or a
verified purchase. The five-trial authorization cap remains consumed; this report
does not authorize further paid attempts.

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
