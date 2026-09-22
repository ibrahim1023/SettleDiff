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
execution, and independent activity collection. Schema and transaction lookup are
separate runtime-checkable capabilities, so an adapter is not forced to implement
provider operations it does not support. Every operation returns strict
`AdapterEvidence` carrying adapter, operation, source, artifact type, data,
provider observation time when supplied, submission certainty, available
payment/transaction references, and optional provider-receipt evidence.

Perflo implements this boundary through `perflo/adapter.py`; its command envelopes
and aliases no longer cross into application services. The verdict, check, and
matching layers contain no adapter-specific branching. The x402 package now provides
bounded offline v2 challenge/settlement-response parsing and explicit Base Sepolia test
USDC normalization. It also defines the versioned request/result contract for an
independently owned signer (request schema 2, result and metadata schema 3) and a
shell-free, one-shot, bounded subprocess client. Before signing, the signer
reconstructs schema-2 `PaymentTerms`, including the advertised response-contract
digest when `resource.mimeType` exists. The client launches with a controlled environment that does not inherit wallet keys; the
external signer is responsible for acquiring signing authority without returning secret
material. Offline independent settlement verification uses a bounded read-only JSON-RPC
port and requires the Base Sepolia chain ID plus exactly one matching USDC transfer event
for the expected token, payer, recipient, and amount; the facilitator transaction sender
is not treated as payer evidence. The x402 recovery classifier preserves the signer
submission state and transaction reference, performs only bounded read-only verification,
and emits canonical adapter evidence. Confirmed and reverted receipts prove submission;
missing/pending evidence or validation/RPC failure remains unresolved, and only explicit
pre-transmission proof establishes non-submission. The production x402 adapter composes
an unsigned bounded resource client, the independently owned one-shot signer process, and
the read-only RPC verifier. It re-fetches the challenge immediately before signer launch,
pins requirement index zero in the signer contract, checks returned challenge terms after launch, and preserves structured uncertainty and
transaction references. CLI composition requires explicit `--rail x402`, an environment
testnet gate, a command-line testnet gate, and the ordinary interactive exact-request
authorization. The adapter and composition are offline-tested and completed one controlled
authorized Base Sepolia cycle and one independently operated GoPlausible test endpoint
cycle. The public challenge demonstrated that bounded unsupported alternatives may follow
a strict supported primary requirement; selection remains pinned to index zero, and an
unsupported primary still fails closed. The signer implementation remains independently
owned and outside the tracked application. The 2026-09-22 controlled cycle
validated response-bound clean delivery, while an HTTP-500 signed submission
without a provider transaction reference remained `UNVERIFIABLE` and was not
retried.

## Components

### Domain core

Owns strict canonical models, normalization, activity matching, independent checks, verdict precedence, and redaction. It accepts data and returns data; it performs no I/O and contains no model calls.

### Application services

The domain accepts canonical protocol identifiers without a provider registry and imports neither payment adapter. Provider-specific envelopes, versions, facilitator behavior, and transport branches remain inside adapter packages; the application core depends only on rail-neutral ports and canonical evidence.

Coordinate live investigations and fixture replay. They create run IDs, authorization capabilities, evidence timelines, and invoke ports in a fixed safety order. After preflight they create a versioned canonical payment-terms descriptor covering adapter/version, scheme, network/legacy chain, asset identity, recipient, quote, timeout, resource, method, body digest, and the advertised response-contract digest when present. Its SHA-256 digest is bound into the one-use capability and revalidated immediately before adapter execution and signer launch. They do not duplicate verification rules.

### Perflo adapter

Runs a narrow allowlist of Perflo CLI commands through argument-based subprocess execution. It captures raw envelopes before normalization and surfaces submission certainty on mutations.

### x402 adapter

Issues bounded unsigned GET/POST challenge requests without redirects. Remote resources require HTTPS; HTTP is accepted only when URL parsing proves the host is loopback. It strictly parses x402 v2 exact/Base-Sepolia/test-USDC terms, revalidates them against the consumed capability, launches one independently owned signer process, preserves signer-owned bounded paid-response facts (status, media type, byte count, truncation, bounded parsed JSON) for deterministic delivery validation, normalizes provider settlement separately, and exposes bounded independent receipt/transfer evidence through the same application port.

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

SQLite schema 4 creates and backfills durable run records, events, artifacts, and explanations; schema 5 adds insert-only evidence timelines; schema 6 adds immutable content-addressed contract snapshots plus append-only observations. A run record is created before live preflight, redacted events and artifacts are appended during execution, and finalization writes the report, explanation, and immutable timeline atomically in one transaction. Timeline rows are written once and never updated or deleted individually. Repository open deterministically backfills only missing timelines for finalized pre-schema-5 records from persisted report, events, and artifacts; unavailable source times remain null, existing timelines are never rewritten, and historical evidence is preserved. Timeline source timestamps are used only when canonical evidence supplies them; otherwise observation time and generation order provide a deterministic supported partial order, not a claim of exact chronology. Failed and refused runs remain inspectable without a final report. Every run records `fixture`, `controlled_live`, or `external_live` provenance. Fixtures remain versioned JSON so CI and demos do not depend on a database.

### Interfaces

Typer provides automation and developer output, including non-paying readiness checks and persisted-evidence inspection/recovery. FastAPI renders active, failed, and completed run records plus Expected/Executed/Recorded diffs. The run list polls the same SQLite ledger, so a separate CLI writer becomes visible without restarting the server.

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
- Artifact schemas and report schemas carry explicit versions.
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
