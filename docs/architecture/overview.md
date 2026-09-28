# Architecture Overview

## Architectural thesis

SettleDiff is agentic where evidence selection benefits from judgment and deterministic where financial truth requires repeatability.

```text
User authorization
       │
       ▼
Application service ───────► one-use paid capability
       │
       ▼
Bounded investigator ──────► typed evidence tools
       │                           │
       │          ┌────────────────┼───────────────┐
       │          ▼                ▼               ▼
       │    payment adapters    Activity       Context.dev
       │    ┌──────┴──────┐     matcher       (conditional)
       │  Perflo         x402
       │    └──────┬──────┘
       │           └───────────────┼───────────────┘
       │                           ▼
       └──────────────────► evidence bundle
                                   │
                                   ▼
                         deterministic verifier
                                   │
                    ┌──────────────┴──────────────┐
                    ▼                             ▼
             machine report              grounded explanation
                    └──────────────┬──────────────┘
                                   ▼
                              CLI / local UI
```

## Payment-rail boundary

Paid execution reaches SettleDiff through adapters that translate rail-specific
envelopes into canonical evidence. Perflo and x402 are implemented adapters; direct
MPP clients and other rails remain architectural extension points:

```text
                       ┌──────────────────────┐
                       │   Paid execution     │
                       │      adapters        │
                       └──────────┬───────────┘
                                  │
                  ┌───────────────┼───────────────┐
                  │               │               │
               Perflo           x402          future rail
                  │               │               │
                  └───────────────┴───────────────┘
                                  │
                                  ▼
                      canonical evidence model
                                  │
                                  ▼
                      deterministic verifier
                                  │
                    ┌─────────────┴─────────────┐
                    ▼                           ▼
               machine report             explanation
```

The unsigned x402 reference capture led to a versioned rail-neutral evidence model
and application port. `PaymentRailAdapter` requires contract inspection, one exact
execution, and activity collection. Schema, transaction lookup, contract reinspection,
and independent settlement are separate runtime-checkable capabilities, so an adapter
is not forced to implement operations it does not support. Every operation returns
strict `AdapterEvidence` carrying adapter, operation, source, artifact type, data,
provider observation time when supplied, submission certainty, available
payment/transaction references, optional provider-receipt evidence, and an evidence
class: `provider_assertion` or `independent_observation`. Perflo evidence is always a
provider assertion. The x402 receipt/transfer observation is independent evidence;
checks and verdicts do not branch on adapter identity.

A `PaidExecutionRequest` carries a discriminated resource reference: an
`HttpResourceReference` (URL, method, optional body) or a `CatalogResourceReference`
(vendor slug plus canonical `input`/`query` objects and an optional sub-account).
Authorization binds the exact resource digest — URL/method/body for HTTP resources and
slug/input/query/sub-account for catalog resources — plus the budget, so no resource
field can change after authorization.

Perflo implements this boundary through `perflo/adapter.py`; its command envelopes
and aliases no longer cross into application services. The verdict, check, and
matching layers contain no adapter-specific branching. The x402 package provides
bounded offline v2 challenge/settlement-response parsing, explicit Base Sepolia test
USDC normalization, and the versioned request/result contract for an independently
owned signer (request schema 2, result and metadata schema 3). Before authorization,
the CLI invokes the signer's read-only metadata probe and captures the public payer.
The selected payment requirement and that mandatory payer form a `SettlementProfile`:
CAIP-2 network, numeric chain ID, asset identity and decimals, exact atomic amount,
recipient, and payer. `PaymentTerms` schema 4 binds the payer and canonical profile
digest alongside the HTTP resource terms and optional response-contract digest.

Immediately before signer launch, the adapter re-fetches the challenge, rebuilds the
profile with the pre-authorized payer, and refuses any drift. A signer-returned payer
mismatch becomes submission uncertainty; it never replaces the expected payer. The
shell-free one-shot client launches with a controlled environment that does not inherit
wallet keys, and the external signer acquires signing authority without returning secret
material.

Rail-neutral settlement observation lives in `observers/evm_rpc.py` and
`observers/evm_transfer.py`. The bounded `EvmRpcClient` allowlists only read-only chain-ID
and receipt calls. The transfer observer validates the chain, receipt, and exactly one
matching ERC-20 `Transfer` against the pre-authorized profile; the facilitator transaction
sender is not payer evidence. It returns `CONFIRMED`, `FAILED`, `INDETERMINATE`, or
`UNAVAILABLE`, with separate verification dimensions for chain, asset, amount, recipient,
and payer. Provider/observer comparison is a different deterministic record:
`MATCH`, `CONTRADICTED`, or `NOT_COMPARABLE`.

The x402 recovery classifier preserves signer submission state and transaction reference,
uses the same pre-authorized profile, and performs only bounded read-only observation.
Confirmed and reverted receipts prove submission; missing, pending, malformed, or
unavailable evidence remains unresolved, and only explicit pre-transmission proof
establishes non-submission. CLI composition still requires explicit `--rail x402`, both
testnet gates, and interactive exact-request authorization. The signer implementation
remains independently owned and outside the tracked application. Historical controlled
and public endpoint cycles remain bounded compatibility evidence, not substitutes for the
current profile and observer contract.

## Components

### Domain core

Owns strict canonical models, normalization, activity matching, independent checks, settlement comparison, verdict precedence, and redaction. It accepts data and returns data; it performs no I/O and contains no model calls. New live reports use `MachineReport` schema 4: `provider_activity` retains optional provider accounting evidence, `independent_settlement` is mandatory, `settlement_comparison` is mandatory, and `ledger` equals only the independent observation's ledger. Schemas 1–3 retain their historical meaning.

### Application services

The domain accepts canonical protocol identifiers without a provider registry and imports neither payment adapter. Provider-specific envelopes, versions, facilitator behavior, and transport branches remain inside adapter packages; the application core depends only on rail-neutral ports and canonical evidence.

Coordinate live investigations and fixture replay. They create run IDs, authorization capabilities, evidence timelines, and invoke ports in a fixed safety order. After preflight they create a versioned canonical payment-terms descriptor. Legacy HTTP terms (schemas 1 and 2) cover adapter/version, scheme, network/legacy chain, asset identity, recipient, quote, timeout, resource URL, method, body digest, and the advertised response-contract digest when present. Catalog terms (schema 3) bind the resource digest, canonical vendor-contract digest, advertised quote, vendor-required maximum charge, and user-authorized maximum charge, with no HTTP fields. x402 HTTP terms use schema 4 and additionally require the pre-authorized payer and canonical `SettlementProfile` digest. The descriptor's SHA-256 digest is bound into the one-use capability and revalidated immediately before adapter execution and signer launch. For catalog resources the vendor declaration is re-read after interactive confirmation and before capability consumption; contract drift or malformed evidence fails before `pay` launches. They do not duplicate verification rules.

### Perflo adapter

Runs a narrow allowlist of Perflo v8 CLI commands through argument-based subprocess
execution: `vendor <slug> --json` for contract inspection and pre-payment reinspection,
`pay <slug>` with conditional `--input`, `--query`, and `--sub-account` arguments plus a
major-unit USD `--max-charge`, `activity --json`, and `tx status <hash> --json`. It never
passes `--out`, `--full`, or `--no-wait`. It captures raw envelopes before normalization
and surfaces submission certainty on mutations.

Bounded CLI output projections are preserved as provider evidence: a capped-output
projection keeps its truncation flag, byte count, preview, and note, and a provider
`savedTo` file projection keeps its byte count and preview with the local path redacted.
Omitted output content cannot prove delivery.

Agent Activity rows (`agent.rows` with `agent.meta`) carry signed major-unit amounts;
`ledgerState` `posted|pending|voided` maps to `CONFIRMED|PENDING|FAILED` as provider
accounting state, and a non-positive signed Activity amount never supplies actual charge
evidence. Perflo Activity and `tx status` are provider assertions in the same trust
domain as `pay`, not independent ledger observations. Credit-funded results may carry a
Base `settlement.txHash` for Perflo's own vendor settlement when
`flow: "authorization"`; the hash is retained for provider Activity correlation, not
interpreted as the customer's transfer. Perflo v8 does not expose the exact pre-payment
network, asset reference/decimals, atomic amount, recipient, and mandatory payer needed for
a `SettlementProfile`. Current Perflo reports therefore record
`SETTLEMENT_PROFILE_UNAVAILABLE`; provider-only success remains `UNVERIFIABLE`.
When the user configures `SETTLEDIFF_PERFLO_RPC_URL`, an optional Base mainnet RPC
checks only the chain ID and receipt for the in-memory provider-referenced hash.
It records a separate redacted `evm_rpc.provider_transaction` artifact; a reverted receipt
can contradict `finalized`, but a successful receipt never proves a transfer or a
customer debit. Without configuration the artifact records `OBSERVER_NOT_CONFIGURED`.
This corroboration never changes findings or the report verdict (ADR 0011).

### x402 adapter

Issues bounded unsigned GET/POST challenge requests without redirects. Remote resources require HTTPS; HTTP is accepted only when URL parsing proves the host is loopback. It strictly parses x402 v2 exact/Base-Sepolia/test-USDC terms, binds the signer-probed payer and exact settlement profile before authorization, rebuilds the profile from the second challenge before signer launch, launches one independently owned signer process, preserves signer-owned bounded paid-response facts (status, media type, byte count, truncation, bounded parsed JSON) for deterministic delivery validation, and normalizes provider settlement separately. Its `IndependentSettlementPort` exposes the observer result without converting provider consistency into independent truth.

### Investigation Agent

One PydanticAI agent chooses among typed tools. Hyperfusion supplies the model through an OpenAI-compatible Chat Completions client. PydanticAI owns the model/tool loop; SettleDiff owns authorization, evidence state, limits, and all financial checks.

### Activity matcher

Matches persisted records using ordered deterministic strategies:

1. transaction ID;
2. session ID plus execution vendor, or the authoritative selected contract vendor when execution omits it;
3. transaction hash;
4. vendor, amount, and bounded timestamp window.

Every result includes strategy and confidence. Ties or weak fallback matches remain ambiguous; the agent cannot promote them.

Perflo Activity status is normalized conservatively:

```text
broadcast        → PENDING
broadcast_failed → FAILED
confirmed        → CONFIRMED
settled          → CONFIRMED
```

A matched Activity record establishes charge evidence only when the match is
high-confidence, the canonical status is `CONFIRMED`, and a normalized amount is present.
`PENDING`, `FAILED`, ambiguous, or low-confidence records do not establish a charge.

### Storage

SQLite schema 4 creates and backfills durable run records, events, artifacts, and explanations; schema 5 adds insert-only evidence timelines; schema 6 adds immutable content-addressed contract snapshots plus append-only observations keyed by target — the contract URL for HTTP/x402 rails and the catalog slug for Perflo contracts. A run record is created before live preflight, redacted events and artifacts are appended during execution, and finalization writes the report, explanation, and immutable timeline atomically in one transaction. Timeline rows are written once and never updated or deleted individually. Repository open deterministically backfills only missing timelines for finalized pre-schema-5 records from persisted report, events, and artifacts; unavailable source times remain null, existing timelines are never rewritten, and historical evidence is preserved. Timeline source timestamps are used only when canonical evidence supplies them; otherwise observation time and generation order provide a deterministic supported partial order, not a claim of exact chronology. Failed and refused runs remain inspectable without a final report. Every run records `fixture`, `controlled_live`, or `external_live` provenance. Fixtures remain versioned JSON so CI and demos do not depend on a database.

### Interfaces

Typer provides automation and developer output, including non-paying readiness checks and persisted-evidence inspection/recovery. FastAPI renders active, failed, and completed run records. Historical reports retain Expected/Executed/Recorded diffs; schema-4 reports label the final column Independently observed and display provider claims separately. The run list polls the same SQLite ledger, so a separate CLI writer becomes visible without restarting the server.

## Run state

The application owns an explicit state machine even though no graph framework is used:

```text
PREFLIGHT
  → AUTHORIZED
  → EXECUTING
  → EVIDENCE_RECOVERY (only after submission uncertainty)
  → VERIFYING
  → EXPLAINING
  → COMPLETE
```

Invalid transitions fail closed. Evidence recovery permits only status/activity inspection and never another paid execution. `RunInvestigation` emits `REFUSED` when capability consumption fails and `FAILED` when a post-authorization stage raises; configured event persistence receives those terminal events. Interactive CLI decline occurs before capability consumption and therefore remains a pre-run refusal.

## Data model principles

- Strict Pydantic models reject unknown external fields at canonical boundaries only after raw payload preservation.
- Provider parsers tolerate documented envelope evolution and explicitly record ignored fields.
- Financial values use `Decimal`, an explicit unit, and normalized minor-unit metadata where supplied.
- Timestamps are timezone-aware UTC.
- Normalized enums retain an `unknown` state rather than guessing.
- Findings cite artifact IDs and field paths.
- Explanations cite existing finding and artifact IDs and are validated after generation.
- Artifact schemas and report schemas carry explicit versions; ADR 0010 defines current schema-4 settlement provenance.
- Public report schema 2 allowlists only independent/comparison statuses and diagnostics, five verification dimensions, and mandatory payer policy; it excludes observer source, transaction reference, profile, ledgers, provider Activity, and evidence IDs.
- Bundle compatibility metadata records the report/database versions, payment adapter identity, and x402 protocol/signer schema when applicable. Older bundles without the additive x402 fields retain their original integrity representation and remain readable.

## Context strategy

The model receives a compact investigation state: unresolved checks, normalized artifact summaries, stable artifact handles, allowed tools, remaining limits, and the immutable verifier result when explaining. Raw receipts, full response bodies, activity feeds, and credentials stay outside context unless a bounded tool returns a redacted excerpt.

This makes context selection observable and testable while avoiding a separate RAG system or compaction layer.

## Failure policy

- Invalid input: stop before external action.
- Hyperfusion transient failure: the investigation may end without an explanation; the machine report remains authoritative.
- Perflo read failure: retry only when the error is explicitly recoverable and the operation is read-only.
- Perflo mutation failure: never retry until submission certainty is resolved; ambiguous outcome requires explicit user approval before any new attempt.
- Parse failure: preserve raw evidence and mark the affected check unknown.
- Ambiguous activity match: return candidates and `UNVERIFIABLE`/warning according to check rules.
- Storage failure after paid execution: retain the in-memory report, emit a critical local diagnostic, and never repeat execution.
- Telemetry failure: do not fail the investigation.

## Deployment boundary

The MVP binds the web server to loopback and stores data locally. It has no multi-tenant authentication, remote database, worker queue, or unattended background agent. A future hosted deployment requires a new threat model and ADR rather than reusing local assumptions.

## Decision index

See [Architecture Decisions](../decisions/README.md) for the rationale and consequences behind the selected stack and boundaries.
