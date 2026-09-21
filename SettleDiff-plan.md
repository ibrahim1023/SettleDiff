# SettleDiff x402 Assurance and Transaction Forensics Implementation Plan

> Execute this plan task-by-task using `AGENTS.md`, accepted ADRs, and
> `docs/development/verification-loops.md`. Work test-first and commit each coherent task
> after its focused and neighboring checks pass. Do not use an execution framework that
> conflicts with repository instructions.

**Goal:** Extend SettleDiff into an evidence-first purchase-assurance tool that validates
paid delivery, reconstructs individual purchases, detects contract drift, classifies retry
safety, compares equivalent facilitator runs when provenance exists, exports verifiable
reports, and provides a focused investigation workflow.

**Baseline:** `eb05551` on `main`; package `0.1.0`; report schema 2; bundle schema 2;
database schema 4; external signer schema 2.

## Global constraints

- Deterministic code alone produces findings, verdicts, delivery assessments, retry
  classifications, drift classifications, and facilitator comparisons.
- External sources are evidence, not truth. Provider settlement stays separate from
  independent chain evidence.
- Every paid request requires one-use authorization bound to exact URL, method, body digest,
  payment terms, budget, and any advertised response contract used to judge paid delivery.
- Reobserve and revalidate all authorization-bound terms immediately before signer launch.
- Never retry a money-moving request automatically after uncertain submission.
- Default tests make no live model, Perflo, Context.dev, facilitator, RPC, Bazaar, signer, or
  paid calls.
- Use `Decimal`, aware UTC timestamps, strict Pydantic boundaries, bounded parsing, and
  redaction before persistence, export, telemetry, or rendering.
- Preserve `UNKNOWN`, `UNAVAILABLE`, and unresolved retry states rather than treating missing
  evidence as failure or success.
- Support only captured and documented protocol shapes. Unknown settlement-critical fields
  fail closed; bounded additive metadata may be retained without becoming trusted semantics.
- Do not add crawling, ranking, reputation scores, hosted multi-tenancy, unattended buying,
  or a payment/facilitator matrix.

## Current evidence boundaries

The implementation must follow the boundaries that exist at the baseline:

1. `x402/http.py` performs only unsigned challenge requests.
2. Paid service output arrives through `ExternalSignerResult.service_response`.
3. `PaymentTerms` is the authorization-bound contract and currently omits response promises.
4. `PaymentRequired.extensions` already retains bounded embedded x402 metadata, including
   Bazaar-shaped metadata, but the adapter does not persist a canonical observation of it.
5. `RunTimeline` records state transitions, not source-attributed purchase evidence.
6. Recovery evidence is persisted through artifacts/run state; it is not a field on
   `MachineReport`.
7. Bundle schema 2 hashes one canonical payload projection; database schema 4 persists run
   state, artifacts, reports, and explanations.

Do not implement around a different assumed boundary.

## Target file map

| File | Responsibility |
|---|---|
| `docs/decisions/0008-assurance-evidence-and-publication.md` | Evidence taxonomy, response promises, retry semantics, schema policy, publication threat model |
| `src/settlediff/domain/integrity.py` | Shared SHA-256 type and canonical JSON primitives only |
| `src/settlediff/domain/models.py` | Persisted response, delivery, retry, and report contracts |
| `src/settlediff/domain/delivery.py` | Pure response-contract validation and delivery findings |
| `src/settlediff/domain/retry.py` | Pure retry-safety classification |
| `src/settlediff/domain/drift.py` | Pure semantic contract fingerprinting and comparison |
| `src/settlediff/domain/facilitators.py` | Equivalence gates and comparison, only after provenance is captured |
| `src/settlediff/application/timeline.py` | Source-attributed evidence timeline, distinct from run transitions |
| `src/settlediff/application/investigate.py` | Deterministic purchase-forensics projection |
| `src/settlediff/application/publication.py` | Public allowlist projection and local static export |
| `src/settlediff/x402/bazaar.py` | Strict parser for captured embedded Bazaar extension shapes; no network client initially |
| `src/settlediff/x402/client_contract.py` | Versioned paid-response metadata from the external signer |
| `src/settlediff/storage/migrations/005_evidence_timeline.sql` | Immutable evidence timeline rows |
| `src/settlediff/storage/migrations/006_contract_snapshots.sql` | Immutable contract content and observations |
| `src/settlediff/application/bundle.py` | Bundle schema 3 logical manifest and explicit digest projection |
| `src/settlediff/cli.py` | Read-only assurance commands and local publication |
| `src/settlediff/api/app.py`, templates, CSS | Timeline, delivery, retry, and drift views after schemas stabilize |

Do not create generic `utils`, `manager`, `service`, `processor`, or catch-all assurance
modules.

---

## Phase 0 — Accept contracts and bind response promises

### Task 1: Accept ADR 0008 and foundational contracts

**Files:**

- Create `docs/decisions/0008-assurance-evidence-and-publication.md`.
- Modify `docs/decisions/README.md`.
- Create `src/settlediff/domain/integrity.py`.
- Modify `src/settlediff/domain/models.py` and `src/settlediff/application/bundle.py`.
- Create `tests/contract/assurance/test_models.py` and bounded JSON fixtures.

**Required decisions:**

- Define provider assertion, independent observation, derived deterministic assessment, and
  publication projection as distinct evidence classes.
- Define `ResponseContract`, `DeliveryObservation`, `DeliveryAssessment`, `RetrySafety`, and
  `RetryAssessment` as strict persisted models in `domain.models` to avoid circular imports.
- Reserve report schema 3 for optional `delivery` and `retry` fields. Schema-1/2 reports must
  continue to parse without those fields; schema-1/2 reports containing them must fail.
- Move both existing SHA-256 aliases from authorization and bundle code to the narrow
  dependency-free `domain.integrity` module.
- State that SHA-256 proves reproducibility/integrity, not publisher identity or authenticity.
- Define the public-export allowlist and explain why ordinary persistence redaction is not
  sufficient evidence that an artifact is safe to publish.

**Test-first cases:** naive timestamps, floats for money, malformed digests, empty evidence
IDs, incoherent delivery states, retry classifications without reason/evidence codes, and
old reports containing future fields.

```bash
uv run pytest tests/contract/assurance/test_models.py tests/unit/domain/test_models.py -q
uv run pyright src/settlediff/domain tests/contract/assurance
uv run ruff check src/settlediff/domain tests/contract/assurance
uv run python scripts/check_docs.py
```

**Acceptance:** old reports and fixtures load unchanged; no I/O enters `domain`; each model
has a named consumer in a later task.

### Task 2: Capture and authorization-bind the advertised response contract

**Files:**

- Modify `src/settlediff/x402/models.py`, `normalize.py`, and `adapter.py`.
- Create `src/settlediff/x402/bazaar.py` for embedded extension parsing only.
- Modify `src/settlediff/application/auth.py`.
- Modify x402 parser, normalization, adapter, and authorization tests.

**Behavior:**

- Explicit x402 response promises come only from captured `PaymentRequired.resource.mimeType`.
  Captured embedded `extensions.bazaar` shapes are parsed as separate provider declaration
  evidence: `extensions.bazaar.schema` validates `info`, not the paid response body, and
  never becomes `ResponseContract.json_schema`.
- Enforce explicit encoded, decoded, nesting, property-count, and string-size limits.
- Preserve raw bounded extension evidence separately from the canonical response promise.
- Add PaymentTerms schema 2 with `response_contract_digest: Sha256Digest | None`.
- Rebuild that digest from the second unsigned challenge immediately before signing. Any
  response-promise drift must fail before signer launch.
- Do not infer an output contract from examples, descriptions, MIME guesses, or paid output.
- Perflo contracts without authoritative output metadata retain `response_contract=None`.

**Tests:** response-contract present/absent, unsupported nested keyword, malformed Bazaar
shape, bounded unknown extension, challenge drift, legacy PaymentTerms parsing, and zero
signer calls on drift.

```bash
uv run pytest tests/contract/x402/test_parser.py tests/unit/x402/test_normalize.py \
  tests/unit/x402/test_adapter.py tests/unit/application/test_auth.py -q
```

**Acceptance:** any promise used to judge paid delivery is covered by the consumed
authorization; Bazaar metadata remains evidence and cannot alter settlement terms.

---

## Phase 1 — Paid response evidence, delivery, timeline, and retry

### Task 3: Version the signer paid-response evidence contract

**Files:**

- Modify `src/settlediff/x402/client_contract.py`, `client.py`, and `adapter.py`.
- Update the independently installed signer and its metadata probe.
- Modify signer contract/client/adapter integration tests.

**Behavior:**

- Bump the external signer request/result and metadata probe only if required by the additive
  contract; do not silently reinterpret schema 2.
- `ServiceResponse` must report HTTP status, bounded media type, exact received byte count,
  truncation state, and a bounded parsed JSON value when available.
- The signer, which owns the paid HTTP exchange, captures this metadata. `x402/http.py` must
  not be used as though it observed the paid response.
- If exact-byte `content_sha256` is retained, classify it as local-only sensitive metadata;
  never publish it by default and never use a hash as a substitute for retained evidence.
- Oversized output records truncation and no parsed body; it must not be treated as valid
  delivery.
- Signer launch remains one-shot and all post-launch malformed evidence remains submission
  uncertain.

**Stop condition:** if the independently owned signer cannot be versioned and tested in the
same task, stop rather than fabricating paid response metadata in the adapter.

```bash
uv run pytest tests/unit/x402/test_client_contract.py tests/integration/x402/test_x402_client.py \
  tests/integration/x402/test_adapter_pipeline.py -q
```

### Task 4: Validate paid delivery deterministically

**Files:**

- Create `src/settlediff/domain/delivery.py`.
- Modify `src/settlediff/domain/checks.py`, `verdict.py`, and report construction.
- Add `tests/unit/domain/test_delivery.py` and delivery fixtures.

**Rules:**

- Distinguish HTTP success, structural delivery, settlement, and subjective usefulness.
- Validate only the accepted schema subset and explicit media requirements.
- A 2xx response with a violated advertised response contract is failed delivery.
- Truncated, missing, unsupported, or malformed evidence is `UNKNOWN`, not failed delivery.
- A proven settlement plus failed delivery must reach `PAID_FAILURE` through an explicit
  verdict rule. A generic new `FAIL` must not accidentally map to the wrong verdict.
- No response contract yields a limited “not assessed” result, not invented expectations.
- Persist only bounded redacted observations; do not publish paid bodies or local-only body
  digests.

**Required cases:** valid JSON, settled 500, empty body, wrong media type, invalid JSON,
schema mismatch, nested unsupported schema, truncation, missing contract, and unproven
settlement.

```bash
uv run pytest tests/unit/domain/test_delivery.py tests/unit/domain/test_checks.py \
  tests/unit/domain/test_verdict.py tests/integration/x402/test_adapter_pipeline.py \
  tests/fixtures -q
```

### Task 5: Persist an evidence timeline distinct from run transitions

**Files:**

- Create `src/settlediff/application/timeline.py`.
- Create `src/settlediff/storage/migrations/005_evidence_timeline.sql`.
- Modify `src/settlediff/storage/sqlite.py` and finalization flow.
- Add unit and migration tests.

**Interface:**

```python
build_evidence_timeline(report, run_events, artifacts) -> tuple[EvidenceTimelineEvent, ...]
```

Each event records source time when available, observation time, stable sequence, source,
artifact/finding IDs, and bounded redacted attributes. Do not manufacture
`REQUEST_TRANSMITTED`, receipt, or service times when the source did not provide them.
Ordering is a deterministic supported partial order: source time first when comparable, then
observation/sequence. Documentation must not claim exact chronology when timestamps are
missing or incomparable.

Timeline rows are immutable inserts. Do not reuse artifact upsert semantics. Final report and
final timeline persist in one transaction; partial runs continue to expose existing run-state
events and artifacts.

```bash
uv run pytest tests/unit/application/test_timeline.py tests/integration/storage/test_sqlite.py -q
```

### Task 6: Produce read-only retry-safety analysis

**Files:**

- Create `src/settlediff/domain/retry.py`.
- Modify report construction and `src/settlediff/cli.py`.
- Add retry and CLI tests.

**Interface:**

```python
analyze_retry(report, recovery_artifacts, run_state) -> RetryAssessment
```

`retry-analysis RUN_ID --database PATH [--json]` reads persisted evidence only and receives
no adapter, signer, wallet, or capability. It is distinct from the existing `recover` command:
`recover` may collect bounded read-only evidence, while `retry-analysis` only interprets what
is already persisted.

| Evidence | Classification |
|---|---|
| Explicit, uncontradicted proof of non-submission | `SAFE_TO_RETRY` |
| Confirmed/reverted receipt, confirmed transfer, or any conclusive transmission evidence | `DO_NOT_RETRY` |
| Provider Activity indicating a possible payment attempt | at least `REQUIRES_HUMAN_DECISION` |
| Timeout, missing/malformed/pending evidence, unavailable RPC, or contradictory proof | `REQUIRES_HUMAN_DECISION` |

Use a conservative lattice: `DO_NOT_RETRY` dominates contradictory evidence;
`REQUIRES_HUMAN_DECISION` dominates `SAFE_TO_RETRY`; safe is possible only with explicit
proof and no contrary evidence. Test evidence-order invariance and monotonicity.

```bash
uv run pytest tests/unit/domain/test_retry.py tests/unit/x402/test_recovery.py \
  tests/integration/test_cli.py -q
```

---

## Phase 2 — Historical contract truth and embedded Bazaar evidence

### Task 7: Snapshot contracts and detect semantic drift

**Files:**

- Create `src/settlediff/domain/drift.py`.
- Create `src/settlediff/storage/migrations/006_contract_snapshots.sql`.
- Modify repository and CLI; add unit, migration, and CLI tests.

Store two distinct digests:

- `semantic_fingerprint`: canonical settlement terms, input contract, response contract, and
  facilitator identity when authoritative;
- `source_digest`: bounded redacted source-contract evidence used to detect source changes
  not represented semantically.

Collection timestamps never enter either digest. Do not blindly exclude normalization notes:
material unsupported/malformed diagnostics must remain visible and prevent a false “no
change” result.

Use content-addressed immutable snapshot rows plus separate observation rows. Reobserving an
identical snapshot adds an observation; it never rewrites prior evidence.

Commands are inspection-only:

```text
snapshot URL --rail {perflo|x402} --database PATH
drift URL --rail {perflo|x402} --database PATH [--json]
```

They call adapter inspection but never issue a capability or invoke execution/signing.

Required change codes include price, network/chain, asset identity, recipient, scheme,
protocol version, input contract, response contract, facilitator, and media type.

```bash
uv run pytest tests/unit/domain/test_drift.py tests/integration/storage/test_contract_snapshots.py \
  tests/integration/test_cli.py -q
```

### Task 8: Compare embedded Bazaar claims with challenge and paid evidence

**Files:**

- Extend `src/settlediff/x402/bazaar.py` from Task 2.
- Add contract fixtures and domain comparison tests.
- Modify `docs/research/x402-v2-mapping.md`.

Start from captured embedded `PaymentRequired.extensions.bazaar` evidence. Do not add a
`metadata_url` client unless an official captured contract defines the URL, response schema,
redirect behavior, and ownership semantics; that would require a separate accepted task.

Compare Bazaar claims with the canonical challenge and, only when a persisted `run_id` is
supplied, paid evidence. Objective states are `MATCH`, `DIFF`, `UNAVAILABLE`, and
`UNSUPPORTED`. Never produce ranking or trust scores.

```text
bazaar-check ENDPOINT --database PATH [--run-id RUN_ID] [--json]
```

The command performs one bounded unsigned challenge observation and cannot sign or pay.
The embedded declaration schema is assessed against `info` as a `DECLARATION_SCHEMA`
check; it is never a paid-response schema. Tests cover absent metadata, exact match,
stale economic terms, stale declaration/media claims, unsupported version/keywords,
malformed/oversized extensions, and unsupported primary requirements.

---

## Phase 3 — Facilitator comparison, gated by provenance

### Task 9: Capture facilitator provenance and compare equivalent persisted runs

**Gate result (2026-09-21):** the provenance audit found no reliable per-run facilitator
identity source, so this task and Phase 3 are **deferred** by
[ADR 0009](docs/decisions/0009-defer-facilitator-comparison.md). Do not implement until the
reopening requirements there are met. The intended task text below is retained as future
requirements.

Do not begin this task until a captured, documented source establishes facilitator identity.
The ADR amendment must state whether identity comes from the challenge, signer configuration,
provider settlement, or another artifact and how conflicts are represented. If no reliable
source exists, defer this phase.

If facilitator selection can affect payment execution, bind its authoritative identifier into
`PaymentTerms` before authorization. Descriptive or provider-asserted identifiers remain
separate from independent settlement evidence.

```python
compare_facilitator_runs(reports, artifacts) -> FacilitatorDiff
```

Reject fewer than two runs, duplicate run IDs, or non-equivalent URL, method, body digest,
authorized economic requirement, response contract, and test environment. Compare persisted
quote, observed charge, network, asset, recipient, settlement, delivery, and bounded latency
only when timestamps support it. Missing data is `UNAVAILABLE`; do not rank fastest or
cheapest.

`facilitator-diff RUN_ID... --database PATH [--json]` is read-only. Every source run must
have been separately initiated and authorized through the existing run command.

---

## Phase 4 — Verifiable bundles and safe static publication

### Task 10: Upgrade bundles to an object-level logical manifest

Bundle schema 3 remains one canonical JSON document. Manifest paths such as `report.json`,
`artifacts/<encoded-id>.json`, `timeline.json`, and `snapshots/<digest>.json` are logical
object identifiers, not filesystem extraction paths.

Digest projection:

1. Each manifest entry hashes the canonical bytes of its referenced object.
2. The manifest never contains an entry for itself or `bundle_sha256`.
3. `bundle_sha256` hashes the canonical entire bundle with only `bundle_sha256` omitted; it
   includes all objects, compatibility metadata, and manifest entries.
4. Logical paths are generated internally, percent-encoded or otherwise constrained, unique,
   and rejected if absolute or containing traversal segments.
5. Schema-2 verification retains its exact existing projection and bytes.

Verify every entry, whole-bundle digest, run/report identity, verdict derivation, citations,
redaction, and compatibility metadata. Hashes prove integrity, not signer identity.

```bash
uv run pytest tests/unit/application/test_bundle.py tests/integration/test_offline_release.py -q
```

### Task 11: Export a public allowlisted HTML/JSON report

**Files:** publication module, dedicated public template, existing CSS, CLI, and tests.

`publish RUN_ID --database PATH --output DIR [--force]` writes atomically and refuses
symlinks, traversal, existing output without `--force`, and partial publication on failure.
No upload, hosting, JavaScript, tracking, or external assets.

The public JSON is a dedicated allowlist projection—not a renamed local evidence bundle. It
may contain verdict, objective findings, bounded delivery status without body, evidence
timeline metadata, retry classification, drift, facilitator comparison when available,
public manifest digests, schema versions, and masked identifiers.

It must exclude paid response bodies, request bodies, raw headers, signatures, payment
payloads, private/local URLs, query credentials, local paths, database paths, environment
values, model rejected output, and local-only content digests. Add low-entropy and licensed
output disclosure tests.

For reproducibility, canonical JSON uses a persisted source/finalization timestamp. A fresh
wall-clock generation time may appear only in unsigned HTML metadata and must not affect JSON
or bundle bytes.

Outputs:

```text
index.html
report.json
public-manifest.json
```

Do not emit the full local `evidence-bundle.json` by default. If a future option exports it,
label it local/private and require explicit opt-in.

---

## Phase 5 — Purchase investigation workflow

### Task 12: Add deterministic investigation over persisted evidence

```python
investigate_purchase(repository, run_id) -> PurchaseInvestigation
```

Stable sections answer: what failed, whether money may have moved, whether amount/recipient
agree, whether delivery satisfied the advertised contract, whether Activity agrees, what
changed, whether retry is safe, and which digest identifies the evidence bundle.

The projection formats existing report, timeline, snapshot, delivery, and retry evidence. It
cannot call a model, produce a second verdict, upgrade uncertainty, or initiate evidence
collection. JSON remains rail-neutral; human output may lead with Perflo-relevant operational
questions without assuming undocumented Perflo fields.

Add CLI and local run-detail rendering only after report, timeline, and snapshot schemas are
stable.

```bash
uv run pytest tests/unit/application/test_investigate.py tests/integration/test_cli.py \
  tests/integration/api/test_app.py -q
```

---

## Phase 6 — Release hardening

### Task 13: Cross-feature fixtures, migration proof, and release documentation

Create sanitized fixtures only from synthetic or explicitly authorized captures. Every
fixture labels provenance honestly. Include success, disagreement, unavailable evidence,
redaction, backward compatibility, and tampering cases for each implemented feature.

The fixture-first demo must cover delivery, evidence timeline, retry analysis, then/now
contract comparison, embedded Bazaar comparison, facilitator comparison only if Phase 3 was
accepted, bundle verification/tampering, static publication, and purchase investigation.
Optional live appendices require explicit gates, fresh authorization, test budget, and a
maximum spend.

Run:

```bash
uv lock --check
uv sync --locked --all-groups
uv run ruff format --check .
uv run ruff check .
uv run pyright
uv run pytest -m "not live and not paid"
uv run python scripts/check_docs.py
git diff --check
git status --short
```

Manually inspect CLI/JSON agreement, static HTML, masked identifiers, public allowlist output,
bundle tampering, and migration from a database-schema-4 copy through every new migration.

## Required test matrix

| Area | Required cases |
|---|---|
| Response promise | absent; valid subset; unsupported nested keyword; oversized/deep; pre-sign drift |
| Paid response | valid; wrong media; empty; malformed JSON; oversized/truncated; malformed signer result |
| Delivery | valid; settled 500; schema mismatch; missing contract; unknown evidence; unproven settlement |
| Timeline | supported order; equal times; missing/incomparable times; conflict; partial run; redaction |
| Retry | explicit non-submission; confirmed/reverted receipt; transfer; provider Activity; pending; unavailable; contradiction |
| Drift | semantic and source digest; price; network; asset; recipient; scheme; version; input/output; no change |
| Bazaar | absent; exact; stale terms/schema; unsupported; malformed/oversized; unsupported primary |
| Facilitators | provenance missing/conflict; equivalent runs; non-equivalent rejection; no execution side effect |
| Bundle | stable bytes; each object tamper; manifest tamper; traversal; redaction; schema-2 compatibility |
| Publication | HTML/JSON agreement; public allowlist; no body/secrets/private URLs; atomic overwrite refusal |
| Investigation | clean; paid failure; failed broadcast; ambiguity; drift; chain conflict; retry states |

## Milestone acceptance criteria

- Every delivery promise used in a finding was captured before payment and authorization-bound.
- Paid-response facts come from the signer-owned exchange and retain bounded provenance.
- Settled invalid delivery deterministically produces `PAID_FAILURE`; absent evidence remains
  `UNVERIFIABLE`.
- Evidence timeline ordering never invents unavailable event times.
- Retry analysis never labels uncertainty safe and cannot send a request.
- Contract history preserves prior truth and reports semantic/source changes independently.
- Bazaar comparison uses captured embedded metadata unless a separate external contract is
  accepted.
- Facilitator comparison ships only with captured provenance and compares equivalent persisted
  runs without execution.
- Bundle object and whole-payload tampering are detectable; schema 2 remains readable.
- Public output is an allowlisted projection, not merely a redacted local bundle.
- The purchase investigation view introduces no new truth or payment path.
- Full offline verification passes; live and paid tests remain explicit opt-ins.

## Guardrails and non-goals

- No scheduler; repeated snapshots may be triggered externally.
- No facilitator payment matrix.
- No subjective output-quality scoring.
- No hashing of secrets or publication of low-entropy/sensitive body digests.
- No singular trust in provider receipts, Bazaar, Perflo Activity, or RPC.
- No new network, asset, scheme, or signer custody support as a side effect.
- No LLM-derived delivery, retry, drift, or verdict state.
- No upload, hosting, analytics, badges, ranking, reputation, or numeric trust score.
- No wallet custody, facilitator operation, routing, automatic refunds, disputes, chargebacks,
  or retry execution.
- No unattended recurring probes, broad crawler, private Perflo API, or hosted multi-tenancy.
- No publisher signatures until a separate key-custody ADR is accepted.

## Recommended delivery order

Ship Tasks 1–4 first as one independently releasable delivery-assurance slice. Then ship the
evidence timeline and retry analysis. Contract history and embedded Bazaar comparison follow.
Facilitator work remains gated on captured provenance. Stabilize all persisted schemas before
bundle schema 3, public publication, UI expansion, and the composite investigation workflow.
