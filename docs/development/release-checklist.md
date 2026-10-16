# Release Checklist

SettleDiff is MIT-licensed and versioned (`0.1.0rc1`). This release candidate is prepared as a draft GitHub release for recipient trials; it is not published to a package registry. This gate builds and installs test artifacts; it does not publish, use production issuer keys, pay, or invoke live providers. The recipient portability check signs only synthetic evidence with an ephemeral test key.

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
- [ ] Both [recipient portability jobs](issuer-assurance.md#automated-recipient-check) pass: a fresh runner installs the wheel without a checkout and verifies the synthetic bundle against the separately supplied fingerprint. Tampered/wrong-key/revoked/missing-signature cases fail; legacy checksum verification remains integrity-only.
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

Run the synthetic issuer/recipient checks directly:

```bash
uv run pytest tests/integration/test_assurance_portability.py -q
```

This local test exercises the CLI cases; the separate CI jobs additionally check
wheel installation and runner isolation. The [issuer guide](issuer-assurance.md#automated-recipient-check)
includes a local wheel-based reproduction, trust limits and artifact handling.

## Build and isolated install

```bash
release_tmp="$(mktemp -d)"
uv build --out-dir "$release_tmp/dist"
uv venv "$release_tmp/venv"
uv pip install --python "$release_tmp/venv/bin/python" "$release_tmp"/dist/*.whl
"$release_tmp/venv/bin/settlediff" --version
```

- [ ] The wheel and source distribution contain only intended tracked package content.
- [ ] The isolated command prints `settlediff 0.1.0rc1`.

The source distribution deliberately includes only `src/`, README, license,
`pyproject.toml`, `uv.lock` and builder-generated metadata/ignore rules. It is
buildable package source; the full repository supplies development tests, fixtures,
skills and research. Build release assets from a clean archive of the selected
commit, not a working directory containing ignored keys or local evidence.

## Recipient release-candidate handoff

Use the [0.1.0rc1 release notes](../releases/0.1.0rc1.md) and
[standalone recipient guide](../releases/0.1.0rc1-recipient.md).

- [ ] Build the wheel from the focused source distribution and inspect both archives.
- [ ] Export only runtime requirements with hashes, without local-path header comments.
- [ ] Generate the synthetic example with `scripts/assurance_portability.py issue`.
- [ ] Include only its three evidence files, the recipient check script and derived public test key in the example ZIP. Never serialize the ephemeral private key or retain its temporary SQLite database.
- [ ] Record the exact source commit/version and public fingerprint in `BUILD-INFO.json`; do not present this self-reported metadata as signed provenance.
- [ ] Copy the standalone guide to `RECIPIENT.md` and checksum every other asset in `SHA256SUMS`.
- [ ] Install the final wheel in a new environment outside the checkout using the exported hash-locked requirements, then pass all seven recipient checks.
- [ ] Verify uploaded draft assets match their local checksums. Keep the draft unpublished until the owner chooses how to share it.
- [ ] Give the chosen tester the issuer fingerprint through a separately authenticated channel. A public key/fingerprint in the same archive is not an independent trust anchor.
- [ ] Record actual external feedback separately; local or CI passes are not an external trial.

No external tester or contact is selected yet. Preparation does not authorize
outreach, release publication, package-registry upload or production issuer keys.

## Security and evidence review

- [ ] Fixture sanitization rejects credentials, private keys, unmasked identifiers, email addresses, and unexpected entropy.
- [ ] Tracked files and Git history have been scanned for credentials and private-key material with no finding.
- [ ] `git ls-files Product-spec.md task.md settlediff-x402-implementation-plan.md .local/x402-captures` prints nothing.
- [ ] No `.sdbundle`, SQLite database/WAL, signer material, raw payment authorization, or raw live capture is tracked.
- [ ] Exported artifacts are redacted and retain provider settlement separately from independent settlement.
- [ ] README live claims match committed sanitized evidence.

## Explicit live gates

Groq, Context.dev, and paid smoke tests remain opt-in and are never part of the offline gate. Run one only when the release makes the corresponding compatibility claim, with fresh owner authorization for every credit-bearing or money-moving request. The 2026-09-02 Context.dev and x402 claims are bounded by their committed reports under `docs/testing/`.
