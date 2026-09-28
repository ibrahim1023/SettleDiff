# Release Checklist

SettleDiff is MIT-licensed and versioned (`0.1.0`). No public distribution channel is selected. This gate builds and installs local artifacts only; it does not publish, sign, pay, or invoke live providers.

## Offline quality gate

- [ ] `uv lock --check`
- [ ] `uv sync --locked --all-groups`
- [ ] `uv run ruff format --check .`
- [ ] `uv run ruff check .`
- [ ] `uv run pyright`
- [ ] `uv run pytest -m "not live and not paid"`
- [ ] `uv run python scripts/check_docs.py`
- [ ] `git diff --check`

## Compatibility and demo gate

- [ ] Every original Perflo fixture retains its accepted verdict.
- [ ] Every x402 fixture replays with its expected findings and verdict.
- [ ] Cross-rail provenance and adapter anti-coupling tests pass: provider-only Perflo success is `UNVERIFIABLE`, while an exact independently confirmed x402 transfer may be `VERIFIED`.
- [ ] Report schemas 1–3 retain their historical meaning; new live and provenance fixtures use report schema 4. Schema-2 bundles verify unchanged while exports emit bundle schema 3 with report compatibility metadata.
- [ ] A schema-4 database migrates forward through migrations 5 (evidence timeline) and 6 (immutable contract snapshots and observations), preserving report, events, artifacts, and explanation with idempotent timeline backfill on open.
- [ ] The cohesive offline release tests cover delivery, timeline, retry, persisted drift, embedded Bazaar comparison, purchase investigation, and public publication without external calls.
- [ ] Bundle checksum changes and internal inconsistencies are rejected; authenticated provenance is not claimed.
- [ ] Public schema-1 reports remain unchanged; public schema 2 exposes only settlement statuses/diagnostics, verification dimensions, and `REQUIRED` payer policy. Publication still emits exactly three static files.
- [ ] Facilitator comparison remains deferred under ADR 0009 (per-run provenance is absent); it is not claimed.
- [ ] The synthetic Perflo v8 corpus under `tests/contract/perflo/` matches the locally inspected `@perflo/cli@8.0.0` package declarations whose npm integrity is recorded in the testing strategy; no fixture is represented as captured live evidence.
- [ ] Catalog authorization binds the exact resource digest, canonical vendor contract digest, advertised price, required `maxChargePerCall`, and authorized maximum; the second vendor observation runs after confirmation and before `pay`.
- [ ] Perflo `pay`, agent Activity, and `tx status` are described as one provider trust domain; current Perflo schema-4 reports use `SETTLEMENT_PROFILE_UNAVAILABLE`, and no live Perflo v8 payment validation is claimed.
- [ ] Legacy Perflo fixtures and schema-1/2 HTTP payment terms retain their accepted behavior.
- [ ] x402 signer metadata is probed before authorization; payer policy is `REQUIRED`; profile drift before signer launch fails closed; observer tests under `tests/unit/observers/` and `tests/integration/observers/` pass.
- [ ] Provider `savedTo` local paths are redacted before persistence.

Run the cohesive release-hardening test directly:

```bash
uv run pytest tests/integration/test_offline_release.py tests/integration/storage/test_sqlite.py -q
```

Run the demonstrated cross-rail pairs directly:

```bash
uv run settlediff verify-fixture fixtures/perflo-v8-provider-only-success --json
uv run settlediff verify-fixture fixtures/x402-independent-confirmed --json
```

The focused release-hardening test persists complete cited artifacts before exercising byte-stable bundle export, verification, and deliberate tamper rejection.

## Build and isolated install

```bash
release_tmp="$(mktemp -d)"
uv build --out-dir "$release_tmp/dist"
uv venv "$release_tmp/venv"
uv pip install --python "$release_tmp/venv/bin/python" "$release_tmp/dist/settlediff-0.1.0-py3-none-any.whl"
"$release_tmp/venv/bin/settlediff" --version
```

- [ ] The wheel and source distribution contain only intended tracked package content.
- [ ] The isolated command prints `settlediff 0.1.0`.

## Security and evidence review

- [ ] Fixture sanitization rejects credentials, private keys, unmasked identifiers, email addresses, and unexpected entropy.
- [ ] Tracked files and Git history have been scanned for credentials and private-key material with no finding.
- [ ] `git ls-files Product-spec.md task.md settlediff-x402-implementation-plan.md .local/x402-captures` prints nothing.
- [ ] No `.sdbundle`, SQLite database/WAL, signer material, raw payment authorization, or raw live capture is tracked.
- [ ] Exported artifacts are redacted and retain provider settlement separately from independent settlement.
- [ ] README live claims match committed sanitized evidence.

## Explicit live gates

Hyperfusion, Context.dev, and paid smoke tests remain opt-in and are never part of the offline gate. Run one only when the release makes the corresponding compatibility claim, with fresh owner authorization for every credit-bearing or money-moving request. The 2026-09-02 Context.dev and x402 claims are bounded by their committed reports under `docs/testing/`.
