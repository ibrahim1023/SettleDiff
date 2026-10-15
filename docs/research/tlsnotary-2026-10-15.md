# TLSNotary delivery provenance research

Reviewed 2026-10-15. Scope: Phase 3 research and a new synthetic HTTPS session;
no payment, wallet, production integration, or financial verdict change.

## Version and compatibility findings

The latest release listed during review is **v0.1.0-alpha.15**, at abbreviated
commit `47aee45`, explicitly a prerelease. The official Rust quick start still
clones alpha.14; use a pinned tag and record its resolved full commit instead of
combining examples from `main` with older dependencies. Alpha.15 introduces HTTP
chunked-response parsing and a proxy protocol. Alpha.14 introduced the
`tlsn_insecure` configuration that bypasses expensive cryptography: it must be
absent from any meaningful validation build. These are upstream release facts,
not results measured by SettleDiff.
([releases](https://github.com/tlsnotary/tlsn/releases),
[quick start](https://tlsnotary.org/docs/quick_start/rust/))

The October 15 proposal's TLS 1.2 boundary still holds: current introductory
documentation supports TLS 1.2 and places TLS 1.3 on the roadmap. MPC-TLS is the
recommended default. Proxy mode changes assumptions because the verifier
forwards encrypted traffic and relies on its network path to the server. Neither
mode can retroactively authenticate a completed arbitrary HTTP response: the
verifier participates in the original session.
([introduction](https://tlsnotary.org/docs/intro/))

The pinned repository warns against production use and expects breaking changes;
its crates are MIT OR Apache-2.0. This review does **not** establish an audit of
the complete pinned dependency graph. Its workspace includes Git-pinned MPZ and
TLSNotary utility dependencies, so a future integration must retain the lockfile
and review transitive licensing/security rather than assuming a crates.io version
alone identifies the protocol.
([pinned README](https://github.com/tlsnotary/tlsn/tree/v0.1.0-alpha.15),
[workspace dependencies](https://raw.githubusercontent.com/tlsnotary/tlsn/v0.1.0-alpha.15/Cargo.toml))

## What an application must verify

Direct verification requires the relying party to join the session. In the
portable workflow, a notary signs an attestation and the prover later creates a
selective presentation. The recipient must independently trust the notary key;
an embedded key is insufficient. TLS certificate identity and notary trust are
separate checks, and notary/prover collusion remains relevant. The FAQ also
describes bandwidth overhead and metadata exposure; its older performance
estimates are not a benchmark for this research.
([FAQ](https://tlsnotary.org/docs/faq/))

Importantly, `Presentation::verify` allows absent identity and transcript proofs.
A successful library call alone does not establish the required server or HTTP
contract. The application must require both, validate the expected server, and
check freshness separately: certificate verification uses the attestation's
signed connection time, not a policy saying the session happened recently.
([presentation implementation](https://raw.githubusercontent.com/tlsnotary/tlsn/v0.1.0-alpha.15/crates/attestation/src/presentation.rs))

Authenticated ranges still need an application parser. Require the exact request
method/target/Host, response status, Content-Type, framing, and all bytes needed
for the body claim. Redacted bytes cannot be treated as absent fields or trusted
literal text. A body commitment must identify exact authenticated bytes and its
opening; hashing an unrelated local response is insufficient. Choose commitment
ranges before presentation: later disclosure is limited to committed ranges.
([verification](https://tlsnotary.org/docs/protocol/verification/),
[commit strategy](https://tlsnotary.org/docs/protocol/commit_strategy/))

## Reproducible synthetic candidate

The pinned attestation example supplies a local HTTPS fixture and three stages:

```sh
PORT=4000 cargo run --locked --bin tlsn-server-fixture
SERVER_PORT=4000 cargo run --locked --release --example attestation_prove
cargo run --locked --release --example attestation_present
cargo run --locked --release --example attestation_verify
```

Run the server separately. Defaults request synthetic JSON, producing attestation,
secret, and presentation files; keep secrets private and outside tracked files.
The example is expressly a local demonstration, not independent third-party
attestation.
([example instructions](https://raw.githubusercontent.com/tlsnotary/tlsn/v0.1.0-alpha.15/crates/examples/attestation/README.md))

The prover embeds a public dummy notary signing key, fixture CA/client material,
and logs the synthetic response. It requests identity encoding because its
tooling does not support compression. The presentation reveals selected JSON
fields by default; adapt the experiment to authenticate the full body when
testing its digest. The sample verifier prints its embedded key rather than
enforcing a separate pin. These examples require explicit research-only trust
checks before their success is evidence for the proposed contract.
([prover](https://raw.githubusercontent.com/tlsnotary/tlsn/v0.1.0-alpha.15/crates/examples/attestation/prove.rs),
[presenter](https://raw.githubusercontent.com/tlsnotary/tlsn/v0.1.0-alpha.15/crates/examples/attestation/present.rs),
[verifier](https://raw.githubusercontent.com/tlsnotary/tlsn/v0.1.0-alpha.15/crates/examples/attestation/verify.rs))

## Decision boundary

**Go for isolated synthetic validation; no-go for production paid integration.**
Require negative checks for an unknown notary, altered proof, wrong server,
missing identity/transcript, hidden status, and mismatched body. Record actual
timings and artifact sizes separately. A future paid integration additionally
needs an independent notary, trusted key distribution, signer-owned client
compatibility, privacy review, acceptable measured cost, and separate approval.
Even a valid delivery transcript proves server bytes under protocol assumptions;
it does not prove settlement or subjective fulfillment. Do not upgrade a machine
verdict from this experiment.
