# Release Checklist

SettleDiff is MIT-licensed and versioned (`0.1.0`). No public distribution channel is selected. This gate builds and installs local artifacts only; it does not publish, sign, pay, or invoke live providers.

## Offline quality gate

- [ ] `uv lock --check`
- [ ] `uv run ruff format --check .`
- [ ] `uv run ruff check .`
- [ ] `uv run pyright`
- [ ] `uv run pytest -m "not live and not paid"`
- [ ] `uv run python scripts/check_docs.py`
- [ ] `git diff --check`

## Compatibility and demo gate

- [ ] Every original Perflo fixture retains its accepted verdict.
- [ ] Every x402 fixture replays with its expected findings and verdict.
- [ ] Cross-rail semantic-equivalence and adapter anti-coupling tests pass.
- [ ] Schema-v1 reports, pre-x402 bundle metadata, and existing SQLite migrations remain readable.
- [ ] Bundle checksum changes and internal inconsistencies are rejected; authenticated provenance is not claimed.

Run the demonstrated cross-rail pairs directly:

```bash
uv run settlediff verify-fixture fixtures/clean-success --json
uv run settlediff verify-fixture fixtures/x402-clean-success --json
uv run settlediff verify-fixture fixtures/paid-failure --json
uv run settlediff verify-fixture fixtures/x402-paid-failure --json
```

Exercise export and integrity verification in a temporary directory:

```bash
release_tmp="$(mktemp -d)"
uv run settlediff verify-fixture fixtures/x402-clean-success --database "$release_tmp/reports.sqlite3"
uv run settlediff export syn_x402_clean --database "$release_tmp/reports.sqlite3" --output "$release_tmp/x402-clean.sdbundle"
uv run settlediff verify-bundle "$release_tmp/x402-clean.sdbundle"
```

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
