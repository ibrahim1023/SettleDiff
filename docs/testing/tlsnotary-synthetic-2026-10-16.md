# TLSNotary synthetic validation — 2026-10-16

## Result and scope

**Synthetic research passed; production paid integration remains no-go.** A new
TLS 1.2 session against a loopback HTTPS fixture produced an attestation and a
portable presentation. The strict verifier accepted its independently supplied
local notary-key pin and exact authenticated HTTP contract after the server stopped.
It also verified again after the prover secret file was deleted. Ten negative
presentation/policy cases were rejected. No payment, wallet operation, model call,
external service request, or financial verdict change occurred.

This is controlled synthetic evidence, not an independently operated notary trial.
The server CA/client credentials are upstream public test fixtures. The patched
notary generates a fresh ephemeral key in memory and writes only its public key;
the prover/notary still share a process and host. A recipient must obtain a real
notary trust anchor independently in any future deployment. The existing issuer
and wallet keys were neither used nor read.

## Reproducibility and artifacts

Upstream: TLSNotary `v0.1.0-alpha.15`, full commit
`47aee45b53e06648c1b2ad3689b367b8c923fdec`. The upstream Cargo.lock was retained;
no TLSNotary dependency entered SettleDiff's Python package or lockfile. The build
used isolated temporary Cargo/Rustup directories, Rust 1.99.0
(`b940084d7`, aarch64-apple-darwin), release optimization, four build jobs, and
no inherited application credentials or insecure flags. No global Rust default
was configured. The release examples initially built in 1m 47s, excluding earlier
toolchain setup; this is a local build observation, not a proving benchmark.

The [retained experiment](../../experiments/tlsnotary-alpha15/README.md) includes
the reviewed fixture patch, standalone verifier, opt-in runner, and commands.
[Sanitized measurements](tlsnotary-synthetic-2026-10-16.json) record the final
session and negative-case exits; no proof bytes, public-key values, transcript,
or secret openings are committed. Earlier temporary prover-secret files were
removed. Other disposable build/public test artifacts can be discarded after use.

The runner requires `--allow-loopback`, refuses outputs inside this checkout,
checks the upstream commit, and creates a new private output directory. Its child
process environment contains no application credentials. The producer refuses
non-loopback hosts and non-JSON examples; the presenter refuses non-JSON examples.
Only a synthetic GET to `/formats/json` is exercised. This runner is not a sandbox
for arbitrary upstream executables or a production proof-upload endpoint.

## Authenticated contract

The verifier checks the notary algorithm/key pin before accepting its signature,
validates the fixture certificate/server identity, requires TLS 1.2 and a signed
session time within the caller's age policy, and requires complete authenticated
request/response bytes. It then parses exactly one HTTP exchange:

- GET `/formats/json`, exactly one expected Host, no request body;
- HTTP 200, exactly one `application/json` Content-Type and Content-Length;
- no transfer/content encoding under this deliberately narrow fixture contract;
- 722 body bytes and SHA-256
  `f28f20173ca63f3693b01ac50dbbbfb214b1ddb7a40f8c2c3b7e1fbd2e036a38`.

The expected body digest was calculated from the pinned public fixture before
notarization, rather than learned from the returned proof. The JSON success result
is `SYNTHETIC_TLS_CONTRACT_PASS`, expressly separate from `MachineReport.verdict`.
This demonstrates hashing fully disclosed authenticated bytes, not proving a digest
of a hidden body. The existing public/seller/issuer evidence formats are unchanged.

## Executed negative checks

| Case | Observed result |
|---|---|
| Wrong notary public-key pin | Rejected |
| Wrong expected server | Rejected |
| Wrong expected body digest | Rejected |
| Session outside a zero-second age policy | Rejected |
| Altered presentation byte | Rejected |
| Trailing presentation bytes | Rejected |
| Omitted server identity proof | Rejected |
| Omitted transcript proof | Rejected |
| Redacted body | Rejected |
| Redacted HTTP status | Rejected |

The expiry harness waits 1.1 seconds because signed session time has whole-second
resolution. An initial same-second expiry check correctly passed that age policy;
waiting past the boundary made the intended stale case deterministic.

One Rust contract test also passed: the known `{}` response is accepted, while
nine changed HTTP inputs covering status, media type, duplicate framing,
transfer/content encoding, body, method, target, and Host are rejected. It first
failed to compile before the contract implementation existed, then passed.
Non-JSON producer/presenter modes and a non-loopback producer host were separately
refused. The runner without its opt-in flag exited before directory/socket creation.
A `cargo rustc ... -- --cfg tlsn_insecure` check failed with the verifier's expected
compile-time prohibition; no insecure executable was produced.

## Measured local costs

One final recorded run, with the server/notary co-hosted and dependencies built:

| Stage/artifact | Observation |
|---|---:|
| New session and notarization, process wall time | 0.343308 seconds |
| Full presentation generation, process wall time | 0.501387 seconds |
| First standalone contract verification, process wall time | 0.006496 seconds |
| Repeated verification after deleting prover secrets | 0.003262 seconds |
| Synthetic attestation | 7,184 bytes |
| Full-disclosure presentation | 9,908 bytes |

These are single local measurements, including process startup and OS variability,
not percentiles, production capacity, or a comparison with ordinary HTTPS. Memory,
WAN bandwidth/latency, independent-notary availability, large-body costs, and paid
signer overhead were not measured. Justified production overhead is therefore not
established by this experiment.

## Actual signer compatibility assessment

The tracked `X402ExternalClient` launches an independently owned signer once and
accepts strict result schema 3. `SignerServiceResponse` returns status, media type,
byte count, truncation, and optional parsed JSON; it carries no TLS session proof.
Static inspection of the local reference signer sources found Node/x402 SDK code,
a raw `node:https` transport branch, and no TLSNotary integration. Only import/
transport metadata was inspected; no signer was launched. No wallet key files,
ignored environment configuration, or live payload captures were read. Local sources
are not a portable supported adapter.

TLSNotary's MPC participant must help establish the original TLS session. Copying
an already received response, request log, or saved bundle into Rust cannot create
that missing participation. A future bridge would need to replace or cooperate
with the signer's HTTP transport, preserve exact preauthorized request binding,
redact payment headers before disclosure, and specify the treatment of notarization
failure after a possible payment. It must not retry that payment or reinterpret
settlement. No such bridge or signer-protocol change was implemented.

## Sources and go/no-go

The [primary-source research](../research/tlsnotary-2026-10-15.md) documents TLS 1.2,
optional proof components, key trust, selective disclosure, and upstream security
status. The pinned upstream release explicitly warns against production use.

**Go:** retain a reproducible synthetic session and strict offline recipient check.
**No-go:** production/paid integration. Reopen only with a reviewed maintained
release, independent notary/key distribution, accepted disclosure/retention rules,
a signer-owned client design, compatible target TLS/HTTP behavior, measured resource
budgets, and separate exact paid authorization where needed. Even then, authenticated
server bytes do not establish actual settlement or subjective service fulfillment.

## Repository verification

The full Python offline gate passed **1,535 tests**, with **2 skipped**,
**25 deselected**, **92% application coverage**, in 35.38 seconds:

```bash
uv lock --check
uv run ruff format --check .
uv run ruff check .
uv run pyright
uv run pyright experiments/tlsnotary-alpha15/trial.py
uv run pytest -m 'not live and not paid' --cov=settlediff --cov-report=term-missing
uv run python scripts/check_docs.py
git diff --check
```

Rust's separate contract test and the explicit loopback experiment are not included
in that Python test/coverage count. The fixture patch was applied to pristine
pinned source in a separate temporary directory and reproduced both tested files
byte for byte. Rustfmt checked the retained verifier. Initial network downloads
were toolchain/source/dependency setup; the session itself used only loopback.

Separate read-only standards and specification reviews found no actionable issues
in the research changes. Both confirmed that production integration and financial
semantics remain outside this experiment.
