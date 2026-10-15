# Synthetic TLSNotary experiment

Research only, pinned to TLSNotary `v0.1.0-alpha.15`, commit
`47aee45b53e06648c1b2ad3689b367b8c923fdec`. This is not part of SettleDiff's
runtime or normal CI and must never receive wallet, payment, or private service data.
The upstream library is prerelease software and warns against production use.

## What is retained

- `fixture.patch` modifies the upstream synthetic producer/presenter: fresh ephemeral
  notary key, separate public-key pin, JSON-only/loopback-only producer, complete
  disclosure by default, and switches for missing/hidden evidence tests.
- `verify.rs` replaces the example verifier. It requires a caller-provided notary
  key, fixture certificate validation, server identity, TLS 1.2, bounded freshness,
  a complete authenticated transcript, and one exact HTTP exchange/body digest.
- `trial.py` runs one new loopback HTTPS session, stops its server, then checks
  portable verification and ten negative cases. It deletes prover secrets before
  its final replay. Output files must be outside this checkout; directories are
  exclusive and private. Failure leaves private diagnostics for inspection.

The fixture CA is deliberately a test CA. Fresh notary keys remain co-hosted with
the prover, so pinning here tests verifier policy rather than independent custody.
Full synthetic disclosure is required; this does not prove a digest of a hidden
body. The attestation, presentation, and secrets are not financial findings.

## Reproduce on macOS

Requires `rustup`, Git, a native C toolchain, Python 3.12/3.13, and the project's
locked `uv` environment. Initial source/toolchain/dependency downloads use the
network. The actual experiment connects only to `127.0.0.1`; no public notary or
service is contacted. Linux users can choose a suitable real temporary directory.

Run from the SettleDiff root in a disposable shell/subshell. Rust state and build
artifacts stay isolated from the Python project and global toolchain defaults:

```bash
(
  settlediff_repo="$PWD"
  tlsn_trial_root="$(mktemp -d /private/tmp/settlediff-tlsn.XXXXXX)"
  export RUSTUP_HOME="$tlsn_trial_root/rustup"
  export CARGO_HOME="$tlsn_trial_root/cargo"
  export RUSTUP_TOOLCHAIN=1.99.0
  rustup toolchain install 1.99.0 --profile minimal --no-self-update
  git clone --depth 1 --branch v0.1.0-alpha.15 \
    https://github.com/tlsnotary/tlsn.git "$tlsn_trial_root/upstream"
  git -C "$tlsn_trial_root/upstream" rev-parse HEAD
  git -C "$tlsn_trial_root/upstream" apply --unidiff-zero \
    "$settlediff_repo/experiments/tlsnotary-alpha15/fixture.patch"
  cp "$settlediff_repo/experiments/tlsnotary-alpha15/verify.rs" \
    "$tlsn_trial_root/upstream/crates/examples/attestation/verify.rs"

  cd "$tlsn_trial_root/upstream"
  env -i PATH="$PATH" RUSTUP_HOME="$RUSTUP_HOME" CARGO_HOME="$CARGO_HOME" \
    RUSTUP_TOOLCHAIN=1.99.0 TMPDIR="$tlsn_trial_root" \
    cargo build --locked --release --jobs 4 \
    -p tlsn-examples --example attestation_prove --example attestation_present \
    --example attestation_verify -p tlsn-server-fixture --bin tlsn-server-fixture
  env -i PATH="$PATH" RUSTUP_HOME="$RUSTUP_HOME" CARGO_HOME="$CARGO_HOME" \
    RUSTUP_TOOLCHAIN=1.99.0 TMPDIR="$tlsn_trial_root" \
    cargo test --offline --locked --release -p tlsn-examples --example attestation_verify

  cd "$settlediff_repo"
  uv run python experiments/tlsnotary-alpha15/trial.py \
    --upstream "$tlsn_trial_root/upstream" \
    --workdir "$tlsn_trial_root/run" --allow-loopback
)
```

Never set `RUSTFLAGS`/`CARGO_ENCODED_RUSTFLAGS` to enable `tlsn_insecure`. The build
commands above inherit no such flags; the retained verifier also refuses to
compile with that configuration. The producer/presenter reject non-JSON modes;
the producer rejects a non-loopback host. The runner passes a minimal child
process environment without application credentials.

Without `--allow-loopback`, the runner exits before creating output or opening a
socket. A different upstream commit or existing work directory is refused.
The runner expects the patched, reviewed examples built above; it is not a
sandbox for arbitrary third-party binaries or untrusted proof uploads.

## Offline recipient verification

After the run, only `example-json.presentation.tlsn`, `research-notary-key.bin`,
and the expected synthetic body digest are required. The separately provided
public key is a trust anchor for this local test, not a production notary identity.
The executable uses only files/embedded fixture CA; the server/notary need not run:

```bash
/path/to/upstream/target/release/examples/attestation_verify \
  --presentation /path/to/run/example-json.presentation.tlsn \
  --notary-key /path/to/run/research-notary-key.bin \
  --body-sha256 f28f20173ca63f3693b01ac50dbbbfb214b1ddb7a40f8c2c3b7e1fbd2e036a38
```

Expected result is JSON `SYNTHETIC_TLS_CONTRACT_PASS`, not a SettleDiff financial
verdict. Unknown key, wrong server/body, stale session, altered/trailing proof,
missing identity/transcript, and hidden body/status all exit nonzero. Session time
has whole-second resolution, so the expiry test waits over one second before
using its zero-second age policy. Raw proof parsing is bounded to 2 MiB; this
small research verifier is not a hardened production ingestion API.

See the [validation record](../../docs/testing/tlsnotary-synthetic-2026-10-16.md)
for measured local results and compatibility limits. Retain only reviewed,
sanitized summaries in Git. Remove the disposable directory when it is no longer
needed. Do not upload `.secrets.tlsn`, raw transcripts, or real private responses.

Upstream patch context originates from TLSNotary's MIT OR Apache-2.0 repository;
see the pinned upstream README and dependency licenses. No TLSNotary dependency
has been added to SettleDiff's package or lockfile.
