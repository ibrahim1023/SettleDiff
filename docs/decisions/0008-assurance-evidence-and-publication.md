# ADR 0008: Assurance Evidence and Publication

**Status:** Accepted  
**Date:** 2026-09-17

## Context

SettleDiff can reconstruct a paid request and compare provider settlement with independent evidence, but its persisted contract does not yet represent an advertised paid-response promise, an objective delivery assessment, or a conservative retry classification. Its run timeline records application transitions rather than source-attributed evidence. Bundle hashes protect one local payload projection but do not establish publisher identity, and ordinary persistence redaction does not define what is safe to publish.

These concerns must be defined before delivery findings, retry analysis, contract history, comparison, or public reports are implemented. They must preserve the boundary that only deterministic code creates financial truth and that no uncertainty can authorize an automatic retry.

## Decision

### Evidence classes

SettleDiff keeps four evidence classes distinct:

1. A **provider assertion** is a bounded statement supplied by the resource server, facilitator, Perflo, Bazaar metadata, or another investigated provider. It is evidence but is not independently verified truth.
2. An **independent observation** is a bounded fact collected from a source outside the asserted provider path, such as a validated chain receipt and transfer log. Its source and limits remain explicit.
3. A **derived deterministic assessment** is reproducible output from accepted code over cited persisted evidence. Findings, verdicts, delivery states, retry classifications, drift states, and facilitator comparisons belong here.
4. A **publication projection** is an explicit allowlisted representation of existing persisted evidence and assessments. It cannot add truth, recover omitted evidence, or weaken uncertainty.

Provider and independent evidence remain separate even when they agree. Missing, malformed, unsupported, unavailable, or contradictory evidence remains explicit.

### Response promises

`ResponseContract` is a strict persisted promise containing only an explicitly advertised media type and/or the accepted JSON Schema subset, together with bounded source-field codes. Descriptions, examples, paid output, and MIME guesses cannot create a response promise.

A response promise captured during preflight is part of the exact payment terms. PaymentTerms schema 2 carries its canonical SHA-256 digest. The same promise is rebuilt from the second unsigned challenge immediately before signer launch. Any difference fails before the signer can run.

The accepted JSON Schema subset is `type`, `required`, `properties`, and `items`. Unsupported keywords anywhere in a consumed schema produce `SCHEMA_UNSUPPORTED`; malformed or excessive shapes fail closed. Raw bounded x402 extensions remain provider evidence separate from the canonical response promise.

### Delivery evidence and assessment

`DeliveryObservation` records bounded signer-owned paid-response facts: observation time, HTTP status, media type, received byte count, truncation state, bounded parsed body when available, and evidence identifiers. The unsigned challenge client cannot produce these facts.

`DeliveryAssessment` records one of `SATISFIED`, `FAILED`, `UNKNOWN`, or `NOT_ASSESSED`, a stable reason code, cited evidence identifiers, and the response-contract digest when assessed. A satisfied or failed assessment requires both an observation and an advertised contract. A missing contract is not assessed. Missing, malformed, unsupported, or truncated response evidence is unknown rather than failed.

HTTP success, structural delivery, settlement, and subjective usefulness remain distinct. Only deterministic delivery code may create delivery findings. Proven settlement plus objectively failed delivery maps through an explicit verdict rule to `PAID_FAILURE`.

### Retry semantics

`RetrySafety` contains `SAFE_TO_RETRY`, `DO_NOT_RETRY`, and `REQUIRES_HUMAN_DECISION`. `RetryAssessment` requires stable reason codes and cited evidence identifiers.

The classifier is conservative and read-only:

- explicit uncontradicted proof of non-submission may be safe;
- a confirmed or reverted receipt, validated transfer, or other conclusive transmission evidence is do not retry;
- provider Activity suggesting an attempt, timeout, pending, missing, malformed, unavailable, or contradictory evidence requires a human decision at minimum;
- do not retry dominates contradictory evidence, and human decision dominates safe.

The classifier receives no signer, wallet, paid capability, or mutation port. It cannot execute a retry. Every later paid request requires fresh exact authorization even when non-submission was proven.

### Schema policy

Report schema 3 adds optional `delivery` and `retry` fields. Schema-1 and schema-2 reports remain readable when those keys are absent and must reject them when present, including explicit null values. Serialization of an old-schema report omits future keys so its existing representation remains readable.

`ExpectedContract` schema 3 adds an optional `response_contract`. Older contract schemas reject that key. Existing reports and fixtures without assurance fields retain their original schema versions and meaning.

All persisted models are strict, frozen, bounded, and reject unknown fields. Money remains `Decimal`; timestamps remain aware UTC. Incompatible reinterpretation requires a later schema version and explicit compatibility handling.

### Integrity

One dependency-free domain module owns the lowercase SHA-256 digest type, canonical UTF-8 JSON encoding, and canonical digest operation used by authorization and bundles. Existing bundle schema-2 digest projection and bytes remain unchanged.

SHA-256 establishes reproducibility and tamper evidence for the specified projection. It does not authenticate a publisher, prove custody, establish source truth, or make low-entropy or sensitive content safe to disclose. Publisher signatures require a separate key-custody decision.

### Public publication

Public JSON is a dedicated allowlist projection, not a renamed local evidence bundle and not the result of recursively applying the persistence redactor. The allowlist may contain:

- schema and compatibility versions;
- masked run and public-safe source identifiers;
- the deterministic verdict and objective findings;
- bounded delivery status without paid body content or local-only body digests;
- evidence timeline metadata;
- retry, drift, and facilitator-comparison assessments when available;
- public manifest object digests.

The public projection excludes paid and request bodies, raw headers, signatures, payment authorizations and payloads, private or local URLs, query credentials, local and database paths, environment values, rejected model output, local-only content digests, and content whose low entropy or licensing makes disclosure unsafe.

Persistence redaction protects known sensitive patterns in local storage. It is insufficient publication evidence because it does not establish purpose, licensing, URL locality, low-entropy disclosure risk, or whether a field belongs in a public contract. Publication therefore starts from an empty allowlist and copies only accepted fields.

## Consequences

- Later delivery, retry, drift, comparison, bundle, and publication tasks share strict persisted contracts without circular imports.
- Advertised output promises cannot drift between authorization and signer launch.
- Missing response evidence and uncertain submission remain conservative rather than becoming failure, success, or retry permission.
- Old report and bundle behavior remains readable while new assurance data uses explicit versions.
- Public output requires a separate projection and disclosure tests even when local artifacts were already redacted.
- Hash verification can detect changed bytes but must not be described as publisher authentication.

## Rejected

- Infer response requirements from descriptions, examples, paid output, or guessed media types: creates a contract after authorization or without authoritative evidence.
- Treat provider settlement, Bazaar metadata, Perflo Activity, or RPC as singular truth: erases provenance and disagreement.
- Reuse the run-state timeline as an evidence timeline: invents source chronology and conflates application progress with observed events.
- Mark timeout or absent settlement evidence safe to retry: can duplicate a money-moving request.
- Publish the local evidence bundle after generic redaction: exposes fields that were never accepted for public disclosure.
- Describe a SHA-256 digest as a signature or authenticity proof: no publisher key or custody contract exists.
- Add a remote Bazaar client, scheduler, crawler, score, ranking, or facilitator matrix: no captured accepted contract or current consumer requires them.
