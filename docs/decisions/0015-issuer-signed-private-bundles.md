# ADR 0015: Issuer-Signed Private Bundles

**Status:** Accepted  
**Date:** 2026-10-15

## Context

The owner approved Phase 1 of the cryptographic-assurance proposal. Default branch
`fb290f3` matches its October 15 baseline: report schema 4, bundle schemas 2/3,
integrity-only verification, and ephemeral seller signatures. Schema 3 is one JSON
document of logical objects, not extracted files. There is no issuer key provider.
The existing pinned `cryptography==50.0.1` provides Ed25519; no dependency is added.

An attacker can rewrite unsigned content and recompute checksums. Recipients need
issuer authentication against an independently obtained trust anchor. This does
not establish evidence truth, impartiality, financial independence, freshness, or
non-equivocation.

## Decision

### Wire format

Keep report schemas, bundle serializers, financial checks, and legacy verification
unchanged. Signing accepts only canonical schema-3 private bundles passing existing
integrity, redaction, and report-consistency checks. Canonical means byte equality
with existing `serialize_bundle` output, not RFC 8785 JCS. Schema-2 bundles retain
their legacy integrity-only path.

A detached JSON envelope has exactly `schema_id`, `algorithm`, `public_key`,
`payload`, and `signature`. Schema ID is `assurance-signature/v1`, algorithm is
`Ed25519`. The public key (32 bytes), payload, and signature (64 bytes) use standard
padded base64 with strict decode/re-encode equality. Reject duplicate keys,
unknown fields, non-finite numbers, and excessive size. Envelope whitespace is
transport-only; a self-provided public key never establishes trust.

The payload is exact signed UTF-8 bytes. Its strict manifest contains:

- `schema_id`, `domain` (`SettleDiff assurance manifest v1`), `algorithm`,
  `digest_algorithm` (`SHA-256`), and `issuer_fingerprint`;
- `bundle_schema_version` (3) and actual `report_schema_version`;
- sorted `artifacts`, each with `path`, `role`, `byte_length`, and `sha256`.

One `private_bundle` entry named `bundle.json` hashes the complete exact input.
Every schema-3 logical object has an entry using its existing path: `report.json`
has role `machine_report`, others `logical_object`. Object bytes are their existing
canonical JSON representation embedded in the canonical bundle. This binds report,
verdict, evidence, explanation, timeline, events, snapshots, compatibility metadata,
and bundle checksums, without mutable exclusions. Physical files may be renamed;
paths are logical names and are never extracted or joined to filesystem paths.
Reject non-ASCII, absolute, duplicate, traversal, empty-segment and encoded paths.

Manifest encoding is compact JSON with ASCII keys sorted lexicographically at
every level, no whitespace, standard JSON strings, and decimal integer lengths
and versions without leading zeros. Manifest strings are validated ASCII constants,
portable paths, or lowercase hex digests; numbers are bounded integers, not floats.
The verifier requires equality to this encoding after strict typed parsing. This
is a narrow specified raw-byte format, **not** a JCS implementation. Unicode and
Decimal values remain inside the unchanged bundle; their exact bytes are hashed.

Ed25519 signs `b"SettleDiff assurance manifest v1\x00" + payload_bytes` using
cryptography ([API](https://cryptography.io/en/50.0.0/hazmat/primitives/asymmetric/ed25519/),
[RFC 8032](https://www.rfc-editor.org/rfc/rfc8032)). Cross-language tests verify
the exact payload bytes using Node's standard crypto library when available;
verification does not depend on independently reserializing arbitrary JSON.

### Trust and custody

The issuer is separate from payer and seller. Operators must provision a dedicated
issuer key, never reuse a wallet/seller key. Key-role separation cannot be inferred
mathematically from public bytes; custody and trust distribution are operator duties.
No wallet, seller DID, environment credential, DB, model, RPC, or network enters
issuer signing or verification.

`issuer-keygen` explicitly creates an encrypted PKCS8 Ed25519 private PEM and a
SubjectPublicKeyInfo public PEM. Export/signing never generates keys. Passphrases
use hidden prompts, never arguments, environment, logs, telemetry, SQLite, or
bundles. Private key reads require owner-owned regular files without group/other
permissions; writes are exclusive 0600. Descriptor-relative no-follow operations
reject symlinks in files and ancestors and avoid component-replacement races.
No overwrite or directory creation is implicit. Keep encrypted keys outside the
repo, encrypted backups offline, and passphrases separate.

Authenticated verification requires a caller-supplied expected Ed25519 public key
or a previously trusted SHA-256 fingerprint of its raw 32 bytes. Caller-provided
revoked fingerprints override trust. Unknown/revoked keys fail closed. No online
key discovery, revocation service, or trusted timestamp exists. Rotate by separately
distributing the new anchor. On compromise revoke the old fingerprint and reassess
prior signatures; no timestamp proves they predate compromise.

### Consumers and outcomes

Add offline `sign-bundle`, `verify-assurance`, and `issuer-keygen` commands. Legacy
`export`, `verify-bundle`, and public publication remain unchanged. Authentication
outcomes (`AUTHENTIC`, `INVALID_SIGNATURE`, `CONTENT_MISMATCH`, `UNTRUSTED_ISSUER`,
`UNSUPPORTED_FORMAT`, `MISSING_ARTIFACT`) are separate from the immutable machine
verdict. Authenticated failures exit nonzero; missing/unsigned inputs never fall
back to checksum-only success.

## Consequences

A recipient needs only the private bundle, detached signature, and independently
trusted key/fingerprint, without the issuer's DB. Artifacts and hashes remain
private; this is not a public disclosure format. No financial semantics change.
Public signing, transparency logs, TLSNotary, ZK proofs, and Phases 2–5 are deferred.

## Verification

Use synthetic keys and fixtures at bundle/CLI/file boundaries. Cover round trips,
byte tampering, recomputed checksums, changed verdicts, additions/removals, unsafe
paths, duplicate/noncanonical JSON, key/signature/algorithm/version substitution,
revocation, cross-process/cross-language checks, secure key handling, secret
exclusion, and legacy compatibility. Default tests remain offline.
