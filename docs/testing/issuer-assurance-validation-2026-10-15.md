# Issuer assurance validation — 2026-10-15

## Scope and baseline

Phase 1 only, against main baseline `fb290f3`. The baseline matches the proposal's
report-schema-4 and seller-evidence description. One clarification: private bundle
schema 3 is a single JSON document of logical objects, not a directory/archive.
The additive detached format is specified in accepted
[ADR 0015](../decisions/0015-issuer-signed-private-bundles.md), committed before
implementation. No report, bundle, paid-boundary, settlement, seller-verification,
retry, or public-publication semantics changed. No dependency was added.

The changes are `application/assurance.py` (typed envelope and offline crypto),
`application/issuer_files.py` (bounded no-follow files and encrypted keys),
`assurance_cli.py` and three CLI registrations, their tests, README, architecture,
and security/setup documentation. `issuer-keygen`, `sign-bundle`, and
`verify-assurance` are explicit opt-in commands; defaults remain unchanged.

## Executed verification

On macOS, Python 3.12.11, with the locked environment:

```bash
UV_CACHE_DIR=/private/tmp/settlediff-uv-cache uv lock --check
UV_CACHE_DIR=/private/tmp/settlediff-uv-cache uv run ruff format --check .
UV_CACHE_DIR=/private/tmp/settlediff-uv-cache uv run ruff check .
UV_CACHE_DIR=/private/tmp/settlediff-uv-cache uv run pyright
UV_CACHE_DIR=/private/tmp/settlediff-uv-cache uv run pytest tests/unit/test_assurance_cli.py tests/unit/application/test_assurance.py tests/unit/application/test_issuer_files.py -q
UV_CACHE_DIR=/private/tmp/settlediff-uv-cache uv run pytest -m 'not live and not paid' --cov=settlediff --cov-report=term-missing
UV_CACHE_DIR=/private/tmp/settlediff-uv-cache uv run python scripts/check_docs.py
git diff --check
```

Focused tests: **97 passed**. Full offline gate: **1,530 passed, 2 skipped,
25 deselected**, 36.99 seconds, **92% overall coverage**. New core authentication
coverage is 97%, key/file handling 96%, CLI 90%; these are coverage measurements,
not a security audit. Formatting, lint, types, lock, docs, and whitespace checks
passed. The first sandboxed gate could not bind the existing loopback UI test;
the complete rerun with local socket permission passed. No live/paid test ran.

## Positive and negative evidence

The matrix covers historical schema-1 and current schema-4 reports inside schema-3
bundles, plus schema-2 integrity-only compatibility. It checks:

- trusted public key and separately pinned raw-key fingerprint;
- no anchor, wrong issuer, revoked issuer, swapped signature and substituted key;
- changed verdict, changed bytes, missing/added objects, and recomputed checksums;
- invalid signature, unsigned input, unsupported versions/algorithms, unknown fields;
- duplicate JSON keys, nonfinite/floating numbers, malformed UTF-8/base64, deep/oversized JSON;
- signed false lengths/digests/roles, unsafe/duplicate paths, noncanonical payloads;
- encrypted-key round trip, wrong passphrase, plaintext and wrong-type key rejection;
- private permissions/hard links, file/ancestor symlinks, FIFO/directory rejection,
  no overwrite, file bounds, missing files, and hidden password output;
- generated envelopes constrained to the verifier's size limit.

A fresh interpreter verifies with only bundle/envelope/trusted public key after
removing the synthetic private key and database. Network connections are blocked
in that interpreter. Node v24.1.0 independently verifies Ed25519 using its standard
crypto implementation, checks exact manifest encoding, key fingerprint, and every
artifact length/SHA-256. This exercises the portable wire format across runtimes;
a separate physical machine was not used. The Node check is skipped when Node is
unavailable; it ran here for both report fixtures.

A separate CLI demonstration used the synthetic `x402-independent-confirmed`
fixture, explicitly retained fixture artifacts in temporary SQLite, exported it,
created an ephemeral test-only encrypted issuer key, and signed it. After deleting
the database and private key, verification returned:

```json
{"status":"AUTHENTIC","machine_verdict":"VERIFIED"}
```

After appending one whitespace byte to the bundle:

```json
{"status":"CONTENT_MISMATCH","issuer_fingerprint":null,"machine_verdict":null}
```

The successful result above omits the ephemeral test fingerprint for readability.
No real issuer key was created. Temporary test keys/artifacts were removed. No
wallet, paid request, model request, RPC call, or telemetry export was involved.

The initial manual replay/export attempt also confirmed a preexisting limitation:
`verify-fixture --database` stores the report without its cited artifacts, so export
refuses it. This behavior was left unchanged; the demonstration explicitly retained
the synthetic artifacts, as the unit tests do. The setup guide explains this limit.

## Trust assumptions and deferred work

Issuer authentication proves issuance of exact bytes against a separately trusted
key. The issuer can sign false evidence. A compromised local account can access
unlocked key material; Python cannot guarantee memory zeroization. Key-role
separation and independent anchor distribution are operator duties. Caller-local
revocation has no trusted timestamp or online historical status. Raw seller proofs
remain excluded, so bundles cannot replay seller crypto verification.

Keep the bundle and envelope private; public report signing is not implemented.
No financial verdict is upgraded by authentication. The
[issuer guide](../development/issuer-assurance.md) covers custody, backups,
partial-write recovery, rotation, compromise response, and offline verification.

**Phase 2 recommendation: defer.** No accepted external relying party currently
requires independently witnessed shared history. A local log alone would not
provide non-equivocation. TLSNotary and ZK phases remain unimplemented and require
separate approval, authenticated inputs, and a concrete verifier need.

## Follow-up review — 2026-10-15

The shared two-axis review compared `fb290f3...8a40c16` against repository standards
and the approved Phase 1 proposal/ADR. The Standards axis found no documented
violations or actionable code smells. The Spec axis found one format-contract gap:
missing envelope `schema_id` or `algorithm` fields were supplied by model defaults,
so an otherwise valid envelope was accepted. This was not an issuer-authentication
bypass: the signed payload still bound the version and algorithm.

Four regression cases failed before the fix, covering both fields and historical/
current report fixtures. Both fields are now required, and signing supplies them
explicitly. Missing either returns `UNSUPPORTED_FORMAT` without a machine verdict.
A separate malformed-Unicode probe exposed an unhandled bundle encoding exception
while signing. A failing-then-passing regression now ensures it becomes the existing
sanitized `AssuranceError`. Legacy bundle behavior and financial semantics remain
unchanged. The Spec reviewer rechecked the fixes and confirmed resolution without
new deviations.

Executed focused checks:

```bash
UV_CACHE_DIR=/private/tmp/settlediff-uv-cache uv run pytest tests/unit/application/test_assurance.py tests/unit/application/test_bundle.py tests/unit/test_assurance_cli.py -q
UV_CACHE_DIR=/private/tmp/settlediff-uv-cache uv run pytest tests/unit/application/test_assurance.py tests/unit/test_assurance_cli.py -q
```

The first run passed **151 tests** after the discriminator fix; the second passed
**94 tests** after adding the Unicode fix. The complete offline gate was rerun
with the same coverage command above: **1,535 passed, 2 skipped, 25 deselected**,
36.91 seconds, **92% overall coverage**. Lock, format, lint, typing, docs, and
whitespace checks passed. Manual CLI inspection confirmed malformed envelopes
return JSON `UNSUPPORTED_FORMAT`, null issuer/verdict fields, and exit code 2.
No live/paid operations or later-phase implementation occurred.
