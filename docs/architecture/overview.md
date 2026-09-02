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
       │             ┌─────────────┼─────────────┐
       │             ▼             ▼             ▼
       │          Perflo        Activity      Context.dev
       │          adapter        matcher      (optional)
       │             └─────────────┼─────────────┘
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
envelopes into canonical evidence. Perflo is the first adapter; x402 or direct MPP
clients are architectural extension points, not implemented integrations:

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
independently owned signer and a shell-free, one-shot, bounded subprocess client. The
client launches with a controlled environment that does not inherit wallet keys; the
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
owned and outside the tracked application.

## Components

### Domain core

Owns strict canonical models, normalization, activity matching, independent checks, verdict precedence, and redaction. It accepts data and returns data; it performs no I/O and contains no model calls.

### Application services

The domain accepts canonical protocol identifiers without a provider registry and imports neither payment adapter. Provider-specific envelopes, versions, facilitator behavior, and transport branches remain inside adapter packages; the application core depends only on rail-neutral ports and canonical evidence.

Coordinate live investigations and fixture replay. They create run IDs, authorization capabilities, evidence timelines, and invoke ports in a fixed safety order. After preflight they create a versioned canonical payment-terms descriptor covering adapter/version, scheme, network/legacy chain, asset identity, recipient, quote, timeout, resource, method, and body digest. Its SHA-256 digest is bound into the one-use capability and revalidated immediately before adapter execution. They do not duplicate verification rules.

### Perflo adapter

Runs a narrow allowlist of Perflo CLI commands through argument-based subprocess execution. It captures raw envelopes before normalization and surfaces submission certainty on mutations.

### x402 adapter

Issues bounded unsigned GET/POST challenge requests without redirects. Remote resources require HTTPS; HTTP is accepted only when URL parsing proves the host is loopback. It strictly parses x402 v2 exact/Base-Sepolia/test-USDC terms, revalidates them against the consumed capability, launches one independently owned signer process, normalizes provider settlement separately, and exposes bounded independent receipt/transfer evidence through the same application port.

### Investigation Agent

One PydanticAI agent chooses among typed tools. Hyperfusion supplies the model through an OpenAI-compatible Chat Completions client. PydanticAI owns the model/tool loop; SettleDiff owns authorization, evidence state, limits, and all financial checks.

### Activity matcher

Matches persisted records using ordered deterministic strategies:

1. transaction ID;
2. session ID plus vendor;
3. transaction hash;
4. vendor, amount, and bounded timestamp window.

Every result includes strategy and confidence. Ties or weak fallback matches remain ambiguous; the agent cannot promote them.

### Storage

SQLite persists local run metadata, versioned artifacts, findings, explanations, and event summaries. Fixtures remain versioned JSON so CI and demos do not depend on a database.

### Interfaces

Typer provides automation and developer output. FastAPI renders Jinja pages for run lists and Expected/Executed/Recorded diffs. Both call the same application services.

## Run state

The application owns an explicit state machine even though no graph framework is used:

```text
CREATED
  → CONTRACT_CAPTURED
  → EXECUTION_AUTHORIZED
  → EXECUTION_CAPTURED | EXECUTION_REJECTED | SUBMISSION_UNCERTAIN
  → EVIDENCE_COLLECTED
  → VERIFIED | UNVERIFIABLE
  → EXPLAINED
```

Invalid transitions fail closed. `SUBMISSION_UNCERTAIN` permits status/history inspection but never another paid execution.

## Data model principles

- Strict Pydantic models reject unknown external fields at canonical boundaries only after raw payload preservation.
- Provider parsers tolerate documented envelope evolution and explicitly record ignored fields.
- Financial values use `Decimal`, an explicit unit, and normalized minor-unit metadata where supplied.
- Timestamps are timezone-aware UTC.
- Normalized enums retain an `unknown` state rather than guessing.
- Findings cite artifact IDs and field paths.
- Explanations cite existing finding and artifact IDs and are validated after generation.
- Artifact schemas and report schemas carry explicit versions.

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
