# SettleDiff Improvement Plan from Perflo + x402

**Date:** 2026-10-05

**Repository reviewed:** https://github.com/ibrahim1023/SettleDiff

**Current baseline:** `main`, reviewed through recent September 2026 changes, including Perflo v8 support, independent x402 settlement observation, provider-chain corroboration, Bazaar checks, delivery-contract verification, retry analysis, bundles, publication, and schema-4 evidence boundaries.

---

# 1. Current SettleDiff baseline

SettleDiff is no longer a simple "compare advertised x402 payment against actual payment" tool.

Its current architecture already models:

```text
intent
  ->
advertised contract
  ->
authorization
  ->
payment execution
  ->
provider settlement assertion
  ->
independent settlement observation
  ->
service delivery
  ->
activity/history
  ->
deterministic verdict
```

Important capabilities already present in the repository include:

- rail-neutral evidence adapters,
- Perflo v8 vendor inspection and payment evidence,
- x402 v2 paid-request handling,
- strict one-use paid authorization,
- second pre-payment observation to detect contract drift,
- exact x402 settlement-profile binding,
- read-only EVM receipt observation,
- exact ERC-20 transfer matching,
- separation between provider evidence and independent evidence,
- conservative handling of uncertain submission,
- `VERIFIED`, `VERIFIED_WITH_WARNINGS`, `PAID_FAILURE`, `PAYMENT_FAILURE`, and `UNVERIFIABLE`,
- response-media-type delivery checks,
- Bazaar declaration inspection,
- deterministic fixture replay,
- provider-chain corroboration for Perflo,
- activity correlation,
- retry analysis,
- evidence bundles,
- tamper detection,
- masked public reports,
- local UI,
- bounded LLM investigator that cannot decide financial truth.

This means the next phase should **not** be a broad rewrite.

The right direction is to make SettleDiff a stronger **agent financial action assurance layer** by extending its evidence model to newer x402 primitives and deeper Perflo provenance.

---

# 2. Strategic product position

The best positioning is:

> **SettleDiff independently checks whether an AI agent's financial action matched the authority, terms, execution, settlement, and delivery evidence available for that action.**

That is broader and more durable than:

> x402 payment verifier

but narrower and more credible than:

> universal financial auditor.

A useful invariant model is:

```text
INTENT
What did the agent mean to buy or transfer?

AUTHORITY
What was the agent permitted to do?

TERMS
What was promised before money could move?

EXECUTION
What operation was actually attempted?

ACCOUNTING
What was actually charged or debited?

SETTLEMENT
What externally or independently observable movement occurred?

DELIVERY
What did the seller actually return?

HISTORY
What did the provider later record?

PROVENANCE
Which source made each claim?
```

SettleDiff should compare those layers without collapsing them into one "transaction succeeded" boolean.

---

# 3. Highest-priority improvement: Signed x402 offers and receipts

## Implementation status (2026-10-12)

The JWS/Ed25519 profile is integrated and verified offline. Offers are checked
against same-origin DID assertions before authorization, proof and identity digests
are bound to schema-4 terms, and reinspection refuses drift before signer launch.
Receipt authenticity, key continuity, signed terms, and available transaction
references produce deterministic findings after the one attempt, including uncertain
results. See [the signed evidence guide](docs/development/signed-evidence.md) and
[ADR 0012](docs/decisions/0012-signed-provider-evidence.md).

The gap analysis below describes the starting point. EIP-712, historical key
rotation, replayable exported signatures, and a live signed-service trial remain
outside this implementation. Raw signatures remain ephemeral under the data policy.

## Why this should be P0

x402 now has a Signed Offers & Receipts extension.

A server can sign:

- the offer attached to the `402` payment requirement,
- the receipt attached to the successful response.

The extension is explicitly designed for:

- auditing,
- reputation,
- dispute resolution,
- portable third-party verification.

Reference:

https://docs.x402.org/extensions/offer-receipt

This is almost exactly the evidence model SettleDiff wants.

## Current gap

SettleDiff currently binds x402 terms itself and preserves the challenge as provider evidence. That protects the local authorization process, but it does not yet make the seller's pre-payment promise a cryptographically authenticated artifact.

Likewise, SettleDiff assesses delivery from the returned HTTP response and advertised response contract, but does not yet verify a seller-signed receipt.

## Add

Introduce:

```python
SignedOfferEvidence
SignedReceiptEvidence
SignerIdentity
SignatureVerification
OfferReceiptBinding
```

Suggested canonical fields:

```json
{
  "format": "eip712 | jws",
  "kid": "...",
  "signerIdentity": "...",
  "signatureStatus": "VALID | INVALID | UNVERIFIABLE",
  "signedPayloadDigest": "...",
  "serviceTermsDigest": "...",
  "transactionReference": "...",
  "issuedAt": "...",
  "rawEvidenceId": "..."
}
```

## New deterministic checks

### Offer authenticity

```text
signed offer verifies
AND
signed terms == authorized terms
```

### Receipt authenticity

```text
signed receipt verifies
AND
receipt references same interaction/purchase
```

### Offer/receipt continuity

```text
offer signer identity == receipt signer identity
OR
documented authorized signer rotation exists
```

### Receipt/settlement relationship

```text
receipt settlement reference
    ==
provider settlement reference
    ==
independent observation target
```

when all are available.

### Delivery claim versus observed delivery

If a signed receipt says delivery succeeded but the observed HTTP response violates the advertised contract:

```text
SIGNED_RECEIPT_DELIVERY_CONTRADICTION
```

That would be a strong SettleDiff finding.

## Product value

This moves SettleDiff from:

> We captured what the server told us.

to:

> We can prove which server identity committed to these terms and which identity attested delivery.

---

# 4. Add x402 Payment-Identifier / logical-request assurance

Reference:

https://docs.x402.org/extensions/payment-identifier

x402 now defines a payment-identifier extension for idempotency.

Its key concept is important:

> the identifier belongs to the logical request, not to an individual network retry.

## Current gap

SettleDiff has excellent one-shot authorization and safe retry behavior, but it should also understand and verify protocol-level idempotency evidence when the server advertises it.

## Add a canonical logical operation ID

```python
LogicalPaymentIdentity:
    scheme
    value
    advertised_required
    observed_in_payment
    observed_in_response
```

## Checks

### Identifier support

```text
server advertised payment-identifier support
```

### Required identifier included

```text
required == true
=> signed payment contains identifier
```

### No request mutation under same identifier

Store a digest of:

```text
method
URL
body
selected payment terms
authorized maximum
```

Then assert:

```text
same payment identifier
=> same logical-request digest
```

If not:

```text
IDEMPOTENCY_KEY_REUSED_WITH_DIFFERENT_REQUEST
```

### Replay experiment support

For controlled test endpoints, SettleDiff could optionally perform a **non-money-moving replay only when the protocol guarantees cached response behavior and the user explicitly authorizes the test**.

The default product should remain no-retry.

The useful feature is not "automatically retry." It is:

> understand whether the underlying protocol offers a safe reconciliation/replay mechanism.

## Report output

Add:

```text
Logical request identity: VERIFIED
Duplicate-payment protection: ADVERTISED / OBSERVED / NOT AVAILABLE
```

---

# 5. Upgrade x402 settlement-pending handling to the current protocol semantics

Reference:

https://docs.x402.org/core-concepts/facilitator

The current x402 docs explicitly define `settlement_pending`:

- a transaction was broadcast,
- confirmation is not yet established,
- the response includes a transaction hash,
- callers should reconcile that transaction before deciding what to do.

The x402 facilitator may internally retry settlement once while using a pending-settlement store to reconcile the already-broadcast transaction rather than broadcasting a duplicate.

## Current SettleDiff state

The repository already recognizes `settlement_pending` in its x402 response model and treats pending settlement conservatively.

That is good.

## Improvement

Make pending reconciliation a first-class evidence state instead of treating it mostly as an error-path variant.

Introduce:

```text
SUBMISSION:
NOT_SUBMITTED
SUBMITTED
SUBMISSION_UNKNOWN

FINALITY:
CONFIRMED
REVERTED
PENDING
UNAVAILABLE
```

These should be separate axes.

Example:

```text
submission = SUBMITTED
finality = PENDING
retry = DO_NOT_RETRY_MUTATION
recommended_action = RECONCILE_TX
```

## Add deterministic diagnostics

- `SETTLEMENT_BROADCAST_PENDING`
- `PENDING_TX_CONFIRMED`
- `PENDING_TX_REVERTED`
- `PENDING_TX_NOT_FOUND`
- `PENDING_TX_OBSERVER_UNAVAILABLE`

## UI improvement

Show a state timeline:

```text
Authorized
   ↓
Signed
   ↓
Submitted
   ↓
Broadcast 0x...
   ↓
Pending confirmation
   ↓
Confirmed
```

This is clearer than one settlement field.

---

# 6. Add x402 `upto` scheme assurance

Reference:

https://docs.x402.org/schemes/upto

The `upto` scheme allows:

```text
buyer authorizes maximum
seller chooses final amount <= maximum
```

This is conceptually close to Perflo's `maxCharge`.

That makes it strategically useful because SettleDiff can build one **variable-charge assurance abstraction** that applies across rails.

## Current limitation

The current x402 external model accepts only:

```text
scheme = exact
network = eip155:84532
```

for supported paid execution.

## Add a rail-neutral charge authorization model

```python
ChargeAuthorization:
    mode: EXACT | MAXIMUM
    authorized_amount
    actual_amount
    currency_or_asset
```

Then:

```text
EXACT:
actual == authorized

MAXIMUM:
0 < actual <= authorized
```

## New checks

- actual charge <= authorized maximum,
- actual charge matches provider settlement amount,
- actual charge matches independent transfer,
- final amount was not changed after execution,
- seller did not settle for the maximum when evidence reports lower usage,
- metering claim exists when the seller claims usage-based pricing.

## Cross-rail value

Map:

```text
x402 upto.max
       \
        -> Canonical Maximum Charge
       /
Perflo maxCharge
```

This is exactly the kind of normalization SettleDiff is designed for.

---

# 7. Add variable-price evidence, not just a variable-price number

For `upto` and Perflo `maxCharge`, checking only:

```text
actual <= maximum
```

is necessary but weak.

A service could authorize $1.00, use $0.03 worth of compute, and charge $0.99.

SettleDiff should optionally preserve the provider's **metering evidence**.

## Canonical model

```json
{
  "metering": {
    "unit": "tokens | seconds | bytes | requests | provider-defined",
    "quantity": "...",
    "rate": "...",
    "calculatedCharge": "...",
    "providerEvidenceId": "..."
  }
}
```

## Findings

- `METERING_MATCH`
- `METERING_CHARGE_DIFF`
- `METERING_UNAVAILABLE`
- `METERING_NOT_OBJECTIVELY_VERIFIABLE`

Do not claim that provider-reported token usage is independently verified unless there is an external source.

This keeps SettleDiff's trust model intact.

---

# 8. Add x402 multi-network support through profiles, not hard-coded widening

The current supported x402 payment profile is intentionally narrow:

```text
exact
Base Sepolia
test USDC
```

That was a sensible safety decision.

The next expansion should preserve that discipline.

x402 currently documents:

- EVM networks,
- SVM/Solana,
- multiple production facilitators,
- `exact`,
- `upto`,
- `batch-settlement`.

References:

- https://docs.x402.org/core-concepts/facilitator
- https://docs.x402.org/core-concepts/client-server
- https://docs.x402.org/schemes/upto
- https://docs.x402.org/schemes/batch-settlement

## Do not change models to arbitrary strings and call it support

Instead introduce:

```python
SettlementProfile
```

with profile plugins:

```text
x402-exact-evm-eip3009
x402-upto-evm
x402-exact-svm
x402-batch-evm
...
```

Each profile declares:

- supported network identifiers,
- asset validation,
- signer semantics,
- transaction-reference semantics,
- independent-observer implementation,
- required fields,
- replay risk,
- finality rules.

## Why

"Supports x402" should never mean:

> parser accepts any x402-shaped JSON.

It should mean:

> SettleDiff knows how to establish the required evidence invariants for this profile.

---

# 9. Do not rush into batch settlement, but design the evidence model for it now

Reference:

https://docs.x402.org/schemes/batch-settlement

Batch settlement changes the economic model significantly:

```text
deposit into escrow
    ->
many off-chain cumulative vouchers
    ->
service delivery
    ->
later batched claim
    ->
later settlement/sweep
```

A one-request-one-transaction assumption no longer works.

## Current SettleDiff model risk

Current settlement reasoning is naturally transaction-centric.

Batch settlement introduces at least four different economic artifacts:

```text
channel deposit
voucher authorization
seller claim
final settlement
```

## Recommended Phase 1

Do **not** implement paid batch execution immediately.

First make the canonical domain model capable of representing:

```python
EconomicLeg:
    kind
    reference
    amount
    cumulative_amount
    payer
    recipient
    asset
    network
    status
    evidence_role
```

A purchase can then reference multiple legs.

## Future checks

For a single request in a batch channel:

```text
voucher delta == authorized request charge
voucher cumulative value is monotonic
voucher channel == advertised channel
voucher signer == buyer
claim includes voucher or superseding cumulative voucher
seller cannot claim > latest authorized cumulative amount
service delivered after voucher verification
```

This could become a very strong differentiator for SettleDiff later.

---

# 10. Expand Bazaar assurance from schema comparison to discovery integrity

Reference:

https://docs.x402.org/extensions/bazaar

The repo already has substantial Bazaar declaration validation. That work should be extended rather than recreated.

## Current capability

SettleDiff can inspect Bazaar-related declaration material and compare embedded contract fields.

## Next additions

### Discovery-to-challenge drift

Compare:

```text
Bazaar discovered listing
vs
live 402 challenge
```

Dimensions:

- service URL,
- HTTP method,
- network,
- asset,
- amount,
- payTo,
- scheme,
- input schema,
- response media type.

### Discovery freshness

Record:

```text
discovered_at
challenged_at
listing_version/digest
```

Do not call a difference fraud. Report:

```text
DISCOVERY_CONTRACT_DRIFT
```

### Facilitator-specific discovery provenance

Bazaar discovery can be provided by a facilitator.

Preserve:

```text
which facilitator produced the listing
```

rather than treating "Bazaar" as one global authoritative marketplace.

### Cataloging extension response

The current x402 docs describe `EXTENSION-RESPONSES` with Bazaar outcomes such as:

- success,
- processing,
- rejected.

SettleDiff could capture this for seller-side test workflows and validate that advertised discovery metadata was actually accepted by the facilitator.

---

# 11. Add facilitator provenance as a first-class field

The x402 docs explicitly state that there is no single facilitator deployment model. A resource can:

- use public testnet facilitator,
- use a production facilitator provider,
- self-facilitate.

Reference:

https://docs.x402.org/core-concepts/facilitator

## Current gap

A settlement response identifies the network and transaction, but assurance reports should also preserve **who asserted verification and settlement**.

## Add

```python
FacilitatorProvenance:
    endpoint
    operator_identity
    discovered_from
    verification_mode: REMOTE | SELF_FACILITATED | UNKNOWN
    software/version_if_known
```

## Why

These are different trust configurations:

```text
seller says payment valid
```

```text
seller calls independent facilitator, facilitator says valid
```

```text
seller runs its own facilitator
```

They may produce identical protocol payloads while having different organizational independence.

SettleDiff already understands that technical separation is not automatically organizational independence. Make this visible in reports.

---

# 12. Generalize evidence independence into a trust-domain graph

This would be a substantial architectural improvement.

Today, evidence is broadly separated into provider versus observer roles.

That can become more precise.

## Proposed model

```python
TrustDomain:
    id
    operator
    relationship
```

Artifacts reference a trust domain.

Example:

```text
Perflo vendor API           -> perflo
Perflo activity             -> perflo
Perflo tx-status            -> perflo
Base RPC provider A         -> rpc-a
Base RPC provider B         -> rpc-b
x402 resource server        -> merchant
x402 facilitator            -> facilitator-x
signed receipt signer       -> merchant-signing-key
local signer                -> user-wallet
```

Then SettleDiff can state:

```text
3 records agree
but all are in the same trust domain
```

versus:

```text
provider assertion agrees with independently operated chain observer
```

## Future extension

Allow multiple observers:

```text
RPC A + RPC B + explorer API
```

without pretending that simple majority voting establishes truth.

The report can show evidence diversity.

---

# 13. Perflo: add a direct REST adapter alongside the CLI adapter

## Current state

The Perflo implementation targets the Perflo v8 CLI surface:

- vendor inspection,
- pay,
- activity,
- transaction status.

This is appropriate for what was available and tested.

## Why add REST

The current Perflo documentation exposes broader API concepts such as:

- capabilities/search,
- vendor payment,
- transfers,
- balances/sub-accounts,
- idempotency,
- guardrail-related outcomes.

A direct API adapter could collect richer structured evidence than CLI output.

## Important rule

Do not replace the CLI adapter.

Use:

```text
perflo-cli-v8
perflo-rest-v1
```

as separate provider adapters/profiles.

That allows SettleDiff to compare provider surfaces against each other when they represent the same purchase.

## Potential checks

```text
CLI execution
vs
REST transaction record
vs
Activity
vs
provider settlement
```

If Perflo exposes a stable purchase ID, use it as the canonical correlation anchor.

---

# 14. Perflo: ingest policy and delegated-authority evidence

Perflo's largest conceptual addition over x402 is not payment settlement. It is delegated agent authority.

SettleDiff should eventually verify:

```text
Was this action permitted by the policy that existed at execution time?
```

## Add canonical authority evidence

```python
DelegatedAuthority:
    principal
    agent
    sub_account
    policy_id
    policy_version
    valid_from
    valid_until
    per_tx_limit
    period_limit
    category_constraints
    recipient_constraints
    confirmation_rule
```

## Checks

- transaction <= per-action limit,
- action occurred during authority validity,
- service/category allowed,
- sub-account matches,
- human confirmation present if required,
- policy snapshot predates execution,
- policy was not silently substituted after authorization.

## Evidence classification

Perflo-provided policy data remains a **provider assertion** unless independently signed/verifiable.

That is fine.

The value is detecting **internal contradictions** such as:

```text
Activity says charge = $20
policy snapshot says per-tx max = $10
provider says success
```

That is a strong finding even without independent settlement proof.

---

# 15. Perflo: distinguish the customer-debit leg from vendor settlement

This is probably the most important Perflo-specific improvement.

The repo already correctly says a Perflo settlement hash may be a provider-to-vendor transaction and therefore does not independently prove the customer's debit.

Do not weaken this boundary.

## Instead, define two separate settlement targets

```text
CUSTOMER_DEBIT
VENDOR_SETTLEMENT
```

Potential model:

```python
EconomicObservation:
    leg: CUSTOMER_DEBIT | VENDOR_SETTLEMENT
    status
    amount
    asset
    payer
    recipient
    reference
    source
```

Then a Perflo run could report:

```text
Customer debit:
  provider assertion: CONFIRMED
  independent observation: UNAVAILABLE

Vendor settlement:
  provider assertion: FINALIZED
  chain corroboration: RECEIPT_SUCCESS
```

This is substantially clearer than one settlement box.

## Future Perflo integration request

Ask Perflo for:

- stable customer debit ID,
- exact customer/sub-account amount,
- asset/currency,
- immutable link to purchase ID,
- vendor settlement ID,
- tx role,
- mapping between debit and vendor settlement.

That integration would materially improve SettleDiff's assurance strength.

---

# 16. Perflo: test the documented `maxCharge` semantics

Perflo and x402 `upto` can share one assurance family.

## Required test matrix

```text
maxCharge = price        -> allowed
actual < maxCharge       -> allowed
actual = maxCharge       -> allowed
actual > maxCharge       -> must fail
maxCharge omitted        -> documented behavior
negative/zero            -> reject
currency precision edge  -> deterministic behavior
```

Also test:

```text
advertised price
vs
maxCharge authorized
vs
actual provider charge
vs
activity amount
vs
settlement amount
```

This could become one of SettleDiff's strongest cross-rail demonstrations.

---

# 17. Perflo: explicitly test documentation ambiguities as contract probes

The Perflo documentation audit identifies several unresolved semantics.

SettleDiff can turn those into controlled compatibility probes.

Examples:

## Transfer lifecycle probe

Observe whether a real transfer response can produce:

```text
status = queued
terminal = true
nextAction = poll
```

If yes, record the actual subsequent behavior.

Do not execute real asset transfers merely to test docs unless explicitly authorized and economically justified.

Prefer sandbox/test environments if Perflo provides them.

## Confirmation-required probe

Test whether:

```text
CONFIRMATION_REQUIRED
```

has:

- mutation already submitted,
- money reserved,
- no money movement,
- stable operation ID,
- safe read-only continuation.

## Idempotency probe

Using non-production or tightly bounded funds:

```text
same key + same body
same key + different body
retry while in flight
retry after completion
```

The goal is to establish a regression fixture for exact semantics.

---

# 18. Build a first-class invariant engine

SettleDiff currently has deterministic checks, which is the correct foundation.

The next evolution can make those checks more declarative.

## Example invariant schema

```yaml
id: actual-charge-within-authority
inputs:
  - authorization.max_amount
  - accounting.actual_charge
rule: actual_charge <= max_amount
severity: critical
unknown_if:
  - authorization.max_amount missing
  - accounting.actual_charge missing
```

Benefits:

- easier cross-rail reuse,
- explicit evidence requirements,
- easier fixture generation,
- easier UI explanations,
- easier future plugins.

## Core invariant families

### Identity

- expected service == executed service
- expected agent == acting agent
- expected payer == observed payer

### Authority

- action fits delegated policy
- charge <= authorized maximum

### Terms

- challenge/listing did not drift
- asset/network/recipient unchanged

### Execution

- one authorized mutation maps to at most one executed mutation

### Accounting

- provider charge agrees across execution/activity/account ledger

### Settlement

- amount/asset/payer/recipient match independent observation

### Delivery

- status/media type/schema satisfy advertised contract

### Replay

- same logical operation cannot create duplicate payment

### Provenance

- evidence role and trust domain are explicit

---

# 19. Add a "proof level" instead of overloading verdict

The current verdict says whether the transaction can be verified under SettleDiff's rules.

Add a separate proof-strength dimension.

Example:

```text
Verdict: VERIFIED
Proof level: INDEPENDENT_SETTLEMENT

Verdict: UNVERIFIABLE
Provider consistency: STRONG
Proof level: PROVIDER_ONLY
```

Suggested levels:

```text
L0 NO_USABLE_EVIDENCE
L1 PROVIDER_ASSERTION
L2 MULTI_RECORD_PROVIDER_CONSISTENCY
L3 CRYPTOGRAPHIC_PROVIDER_ATTESTATION
L4 INDEPENDENT_SETTLEMENT_OBSERVATION
L5 MULTI_DOMAIN_CORROBORATION
```

Do not imply that L5 is mathematically absolute truth.

This helps users understand the difference between:

```text
Perflo's records agree
```

and:

```text
the exact authorized transfer was independently observed.
```

---

# 20. Verify signed public reports

The current evidence bundle is integrity-protected by hashes, but project docs explicitly note that bundle hashes do not authenticate the publisher.

The x402 signed-offer/receipt model highlights how useful publisher authenticity is.

## Add optional report signing

```text
settlediff sign-report <run>
settlediff verify-report <file>
```

Use a dedicated project signing key, not a payment wallet.

Public report could contain:

```json
{
  "reportDigest": "...",
  "signature": "...",
  "keyId": "...",
  "signedAt": "..."
}
```

## Benefit

A report copied to GitHub, sent to Perflo, or attached to a bug report can be verified as an unchanged report produced by the signing identity.

This authenticates the publisher, not the truth of upstream evidence. Keep that distinction explicit.

---

# 21. Evidence-pack export for provider debugging

Create an export specifically designed for sending to a payment provider.

It should include:

```text
run summary
provider/version
sanitized intent
advertised terms
execution reference
state timeline
provider contradictions
independent observations
deterministic findings
doc/API expectation that was violated
```

Exclude:

- secrets,
- full wallet credentials,
- private signer data,
- raw payment signatures.

CLI:

```text
settlediff export-provider-case <run-id>
```

This turns SettleDiff from a personal verifier into a useful engineering tool for Perflo/x402 providers.

---

# 22. Add documentation-contract tests

This is unusual and could differentiate SettleDiff.

## Idea

Represent claims from provider documentation as machine-readable expectations.

Example:

```yaml
provider: perflo
claim:
  endpoint: POST /v1/transfers
  status: queued
expected:
  terminal: false
  next_action: poll
source:
  url: https://docs.perflo.ai/api-reference/endpoints/create-transfer
```

Then compare captured API behavior against the documented contract.

Possible output:

```text
API behavior: internally consistent
Documentation example: inconsistent
```

or:

```text
Live API differs from documented enum
```

## Important

Do not present docs as financial truth.

This feature is about **contract drift and developer reliability**, not settlement verification.

---

# 23. Add protocol-version snapshots

Both Perflo and x402 are moving.

SettleDiff should preserve:

```text
documentation version/date
provider CLI/API version
x402 protocol version
facilitator implementation identity/version when available
```

For every live run.

This makes historical evidence intelligible six months later.

The current repo already does a good job preserving Perflo v8-specific behavior. Generalize that concept.

---

# 24. Add cross-rail scenario equivalence tests

The existing cross-rail testing philosophy is strong.

Extend it with semantic equivalence cases.

## Exact charge

```text
x402 exact
Perflo fixed vendor price
```

Both should satisfy:

```text
authorized == actual
```

## Maximum charge

```text
x402 upto
Perflo maxCharge
```

Both should satisfy:

```text
actual <= authorized max
```

## Idempotency

```text
x402 payment-identifier
Perflo Idempotency-Key
```

Compare their guarantees, but do not assume identical semantics.

## Discovery

```text
x402 Bazaar
Perflo capabilities/search/vendor
```

Test:

```text
discovered terms
vs
pre-execution terms
vs
execution
```

This gives SettleDiff a compelling demo: one invariant language across two different agent-commerce systems.

---

# 25. Add service identity and version assurance

Payment correctness without service identity is incomplete.

A buyer can pay the correct amount to the correct address but receive a different service version.

## Canonical identity

```python
ServiceIdentity:
    provider
    stable_id
    slug_or_url
    version
    contract_digest
```

## Check

```text
discovery service
== pre-payment service
== executed service
```

For Perflo use vendor/service identifiers.

For x402 use resource URL plus stable declaration metadata/signature when available.

---

# 26. Delivery assurance should grow beyond media type

The current media-type contract is a good start.

Next levels:

## JSON Schema

When a Bazaar declaration or service schema provides an output schema:

```text
response JSON validates against pre-payment schema
```

## Declared semantic assertions

Only when objectively testable.

Examples:

```text
required field exists
numeric field within declared range
response contains requested record ID
```

Avoid LLM-based "looks correct" verification as a financial verdict input.

An LLM can explain or propose checks, but deterministic evaluation should remain authoritative.

---

# 27. Add "claim ledger" UI

The current timeline is useful. A more explicit claim ledger would make SettleDiff easier to understand.

Example:

| Claim | Source | Trust domain | Evidence | Result |
|---|---|---|---|---|
| Price was $0.01 | x402 402 | merchant | signed offer | MATCH |
| Max authorized $0.01 | local authorization | user | capability | MATCH |
| Charge was $0.01 | settlement response | merchant/facilitator | provider assertion | MATCH |
| Transfer $0.01 occurred | RPC | independent observer | ERC-20 log | MATCH |
| Response was JSON | HTTP capture | merchant | observed response | MATCH |
| Seller attests delivery | signed receipt | merchant signer | cryptographic attestation | MATCH |

This directly communicates SettleDiff's central idea:

> evidence sources can agree, disagree, or be insufficient.

---

# 28. Add provider-specific "questions SettleDiff cannot answer"

For every rail, generate an explicit gap list.

Example Perflo:

```text
Cannot independently establish:
- customer ledger debit
- relationship between customer debit and provider settlement
- whether provider settlement tx is customer-owned money movement
```

Example x402 exact/Base:

```text
Can independently establish:
- exact ERC-20 transfer on selected network

Cannot establish solely from transfer:
- semantic quality of returned data
- organizational independence of RPC operator
```

This makes the product more credible.

---

# 29. Recommended implementation order

## Phase 1: high-value, low-disruption

### 1. Signed Offers & Receipts

Add parsing, signature verification, canonical evidence, and offer/receipt consistency checks.

### 2. Payment-Identifier

Capture the logical operation ID and verify request binding/idempotency semantics.

### 3. Explicit submission/finality axes

Refactor settlement state presentation so `submitted`, `pending`, `confirmed`, and `reverted` are distinct.

### 4. Facilitator provenance

Record who verified/settled each x402 payment.

### 5. Proof level

Add proof/evidence strength independently of verdict.

These build directly on existing architecture.

---

## Phase 2: cross-rail variable-charge assurance

### 6. Canonical `ChargeAuthorization`

Support:

```text
EXACT
MAXIMUM
```

### 7. x402 `upto`

Add one tightly controlled EVM testnet profile.

### 8. Perflo `maxCharge`

Map it to the same canonical abstraction.

### 9. Metering evidence

Provider-asserted first, without over-claiming independence.

---

## Phase 3: deeper Perflo integration

### 10. Perflo REST adapter

Keep it separate from CLI v8.

### 11. Policy/guardrail snapshots

Ingest delegated-authority evidence.

### 12. Separate customer debit and vendor settlement legs

Do not flatten them.

### 13. Request richer correlation data from Perflo

The most useful integration would expose:

```text
purchase ID
authorization/policy snapshot
customer debit ID
actual debit amount
vendor settlement ID
settlement transaction role
immutable links between them
```

---

## Phase 4: broader protocol assurance

### 14. Multi-network settlement profiles

Add EVM mainnet/testnet networks deliberately.

### 15. SVM observation

Only after designing its distinct settlement/replay semantics.

### 16. Batch-settlement evidence model

Model deposit/voucher/claim/settle legs before supporting paid execution.

---

## Phase 5: productization

### 17. Signed SettleDiff reports
### 18. Provider debugging evidence packs
### 19. Claim-ledger UI
### 20. Documentation-contract testing
### 21. Stable plugin/profile SDK

---

# 30. Suggested next schema evolution

Do not create "schema 5" only because new features exist. Create it when the evidence model actually needs incompatible fields.

A future schema could introduce:

```json
{
  "authority": {},
  "logicalOperation": {},
  "serviceIdentity": {},
  "economicLegs": [],
  "cryptographicAttestations": [],
  "facilitatorProvenance": {},
  "trustDomains": [],
  "proofLevel": "..."
}
```

The existing fields can continue to project into these structures.

---

# 31. Recommended new fixtures

## x402

```text
x402-signed-offer-valid
x402-signed-offer-invalid
x402-signed-receipt-valid
x402-offer-receipt-signer-mismatch
x402-offer-terms-drift
x402-receipt-delivery-contradiction
x402-payment-identifier-replay
x402-payment-identifier-request-mutation
x402-settlement-pending-confirmed
x402-settlement-pending-reverted
x402-upto-below-max
x402-upto-equal-max
x402-upto-over-max-rejected
x402-bazaar-live-challenge-drift
```

## Perflo

```text
perflo-policy-allows-charge
perflo-policy-contradicts-charge
perflo-maxcharge-below-limit
perflo-maxcharge-exceeded
perflo-confirmation-required
perflo-idempotency-in-flight
perflo-idempotency-completed
perflo-provider-debit-vendor-settlement-separated
perflo-transfer-nonterminal
perflo-doc-state-contradiction
```

## Cross-rail

```text
crossrail-exact-equivalence
crossrail-maximum-charge-equivalence
crossrail-idempotency-semantics
crossrail-discovery-to-execution-drift
```

---

# 32. Recommended new deterministic findings

```text
SIGNED_OFFER_VALID
SIGNED_OFFER_INVALID
SIGNED_RECEIPT_VALID
SIGNED_RECEIPT_INVALID
OFFER_AUTHORIZATION_DIFF
OFFER_RECEIPT_IDENTITY_DIFF
RECEIPT_DELIVERY_CONTRADICTION

LOGICAL_PAYMENT_ID_PRESENT
LOGICAL_PAYMENT_ID_REQUIRED_MISSING
LOGICAL_PAYMENT_ID_REQUEST_REUSE

SETTLEMENT_BROADCAST_PENDING
PENDING_SETTLEMENT_CONFIRMED
PENDING_SETTLEMENT_REVERTED

ACTUAL_CHARGE_WITHIN_MAXIMUM
ACTUAL_CHARGE_EXCEEDS_MAXIMUM
METERING_CHARGE_DIFF

FACILITATOR_PROVENANCE_UNKNOWN

POLICY_AUTHORITY_MATCH
POLICY_AUTHORITY_DIFF
HUMAN_CONFIRMATION_MISSING

CUSTOMER_DEBIT_UNAVAILABLE
VENDOR_SETTLEMENT_CORROBORATED
DEBIT_SETTLEMENT_LINK_UNAVAILABLE

DISCOVERY_CONTRACT_DRIFT
SERVICE_IDENTITY_DRIFT
```

---

# 33. What not to do

## Do not weaken `UNVERIFIABLE`

Missing evidence should remain unknown.

## Do not treat Perflo Activity as independent evidence

It is useful provider evidence, but still in the provider trust domain.

## Do not call every successful blockchain receipt a verified purchase

The receipt must match:

- network,
- asset,
- payer,
- recipient,
- amount,
- exact authorized operation.

## Do not let signed receipts override observed contradictions

A valid signature proves who signed a claim, not that the claim is true.

## Do not automatically retry ambiguous money-moving operations

Even though x402 has protocol-level pending-settlement recovery, SettleDiff should distinguish reconciliation from a second business-level purchase attempt.

## Do not add every x402 scheme at once

Support should mean assurance semantics are understood, not merely parsed.

## Do not turn the LLM investigator into a verdict engine

The current deterministic boundary is one of the project's strongest design decisions.

---

# 34. Strongest near-term demo

The best next demo would compare one x402 purchase and one Perflo purchase using the same canonical assurance model.

## x402 path

```text
Bazaar listing
   ↓
signed 402 offer
   ↓
local one-use authorization
   ↓
payment identifier
   ↓
signed payment
   ↓
facilitator settlement
   ↓
independent transfer observation
   ↓
signed receipt
   ↓
delivery validation
```

## Perflo path

```text
vendor listing
   ↓
policy / delegated authority
   ↓
maxCharge
   ↓
one-use SettleDiff authorization
   ↓
Perflo pay
   ↓
customer debit assertion
   ↓
vendor settlement assertion
   ↓
provider-chain corroboration
   ↓
Activity
   ↓
delivery
```

Then produce one report format:

```text
Authority
Terms
Execution
Charge
Settlement
Delivery
Provenance
Proof level
Verdict
```

The contrast itself is valuable:

```text
x402 may expose stronger independently observable settlement evidence.

Perflo may expose stronger delegated-authority and account-control evidence.

SettleDiff checks both without pretending those evidence models are identical.
```

---

# 35. What SettleDiff should ask Perflo for

If Perflo is open to integration, the highest-value request is not "give us more API access" generically.

Ask for an **assurance evidence surface**.

Minimum useful fields:

```text
purchase_id
service_contract_id
service_contract_digest
agent_id
sub_account_id
policy_id
policy_version
authorization_decision
human_confirmation_id (if applicable)
max_charge
actual_charge
currency
customer_debit_id
customer_debit_status
vendor_settlement_id
vendor_settlement_status
settlement_tx_hash
settlement_tx_role
created_at
submitted_at
completed_at
idempotency_key_hash/reference
```

Ideal additional feature:

```text
provider-signed canonical purchase receipt
```

where Perflo cryptographically attests to the internal facts it controls.

That would not make Perflo independent evidence, but it would make provider claims:

- portable,
- tamper-evident,
- attributable,
- easier to compare against external observations.

---

# 36. Product direction after these improvements

SettleDiff can become:

> **an evidence and assurance layer for autonomous financial actions**

rather than merely:

> a test runner for Perflo/x402.

Its product primitives would be:

```text
capture
normalize
bind authority
preserve provenance
observe independently when possible
compare deterministic invariants
classify uncertainty
produce portable evidence
```

That is a coherent product boundary and aligns closely with the architecture already in the repository.

---

# References

## SettleDiff repository

- Repository: https://github.com/ibrahim1023/SettleDiff
- README: https://github.com/ibrahim1023/SettleDiff/blob/main/README.md
- Testing strategy: https://github.com/ibrahim1023/SettleDiff/blob/main/docs/testing/strategy.md
- Architecture overview: https://github.com/ibrahim1023/SettleDiff/blob/main/docs/architecture/overview.md
- Assurance real-world validation: https://github.com/ibrahim1023/SettleDiff/blob/main/docs/testing/assurance-real-world-validation-2026-09-22.md
- Perflo adapter: https://github.com/ibrahim1023/SettleDiff/blob/main/src/settlediff/perflo/adapter.py
- x402 models: https://github.com/ibrahim1023/SettleDiff/blob/main/src/settlediff/x402/models.py
- x402 Bazaar support: https://github.com/ibrahim1023/SettleDiff/blob/main/src/settlediff/x402/bazaar.py
- Settlement comparison: https://github.com/ibrahim1023/SettleDiff/blob/main/src/settlediff/domain/settlement.py

## x402

- Overview: https://docs.x402.org/introduction
- Client / Server: https://docs.x402.org/core-concepts/client-server
- Facilitator: https://docs.x402.org/core-concepts/facilitator
- Bazaar: https://docs.x402.org/extensions/bazaar
- Payment-Identifier: https://docs.x402.org/extensions/payment-identifier
- Signed Offers & Receipts: https://docs.x402.org/extensions/offer-receipt
- Upto: https://docs.x402.org/schemes/upto
- Batch Settlement: https://docs.x402.org/schemes/batch-settlement

## Perflo

- Start here: https://docs.perflo.ai/guides/overview/start-here
- Choose integration: https://docs.perflo.ai/guides/integrate/choose
- Pay-per-use: https://docs.perflo.ai/guides/integrate/pay-per-use/how-it-works
- Guardrails: https://docs.perflo.ai/guides/overview/guardrails
- Denial codes: https://docs.perflo.ai/guides/controls/denial-codes
- Money safety: https://docs.perflo.ai/api-reference/money-safety
- Pay vendor: https://docs.perflo.ai/api-reference/endpoints/pay-vendor
- Create transfer: https://docs.perflo.ai/api-reference/endpoints/create-transfer
- API conventions: https://docs.perflo.ai/api-reference/conventions
- Documentation index: https://docs.perflo.ai/llms.txt
