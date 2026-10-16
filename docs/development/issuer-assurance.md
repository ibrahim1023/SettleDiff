# Offline issuer assurance

Issuer assurance authenticates a private bundle against a separately trusted
Ed25519 public key. `AUTHENTIC` means that issuer signed these exact bytes and
existing bundle integrity checks passed. It does not establish impartiality,
evidence truth, actual payment, delivery, freshness, or financial correctness.
It is a signature, not a zero-knowledge proof. The machine verdict is a separate
field and retains its original report-schema meaning.

## Explicit key setup

Use a dedicated issuer identity, separate from wallet/payer and seller DID keys.
Create an owner-only directory **outside the repository** first. On macOS/Linux:

```bash
mkdir -m 700 "$HOME/.settlediff-issuer"
uv run settlediff issuer-keygen \
  --private-key "$HOME/.settlediff-issuer/issuer.pem" \
  --public-key "$HOME/.settlediff-issuer/issuer.pub.pem"
```

The command prompts twice for a hidden, nonempty passphrase. Choose a strong one.
Only this explicit command creates keys. Private keys use encrypted PKCS8 PEM;
public keys use SubjectPublicKeyInfo PEM. Encryption uses cryptography's
[BestAvailableEncryption](https://cryptography.io/en/50.0.0/hazmat/primitives/asymmetric/serialization/#serialization-encryption-types).
No password/key bytes belong in command arguments, environment files, SQLite,
telemetry, Git, or bundles. Paths are arguments; passwords are hidden input.

Files are created exclusively with mode 0600; existing files are never overwritten.
Private reads require the current owner, no group/other access, and one hard link.
File and ancestor symlinks, nonregular files, oversized inputs, and parent traversal
are refused. Use real directory paths; macOS `/tmp` is a symlink to `/private/tmp`.
No directories are created implicitly. If creation fails partway, inspect any
output before retrying with fresh paths; a partial file is never silently replaced.

Keep encrypted backups offline and the passphrase separately. Loss of either may
make signing impossible. Python does not promise secret-memory zeroization; this
local-file provider assumes a trusted OS/user account and is not an HSM.

## Sign an exported private bundle

Export normally, then sign the immutable file. Replace `RUN_ID` with a saved run
whose cited artifacts were retained. `verify-fixture --database` alone stores a
report without those artifacts and cannot supply an exportable evidence bundle:

```bash
uv run settlediff export RUN_ID --database .local/settlediff.sqlite3 --output .local/bundle.json
uv run settlediff sign-bundle .local/bundle.json \
  --issuer-key "$HOME/.settlediff-issuer/issuer.pem" \
  --output .local/bundle.assurance.json
```

Signing prompts for the passphrase, performs existing bundle integrity/redaction
checks, and creates a detached `assurance-signature/v1` envelope. It never changes
the bundle or verdict and never contacts a model, RPC, provider, or telemetry.
Schema-3 canonical private bundles are supported. Historical schema-2 bundles
remain supported by `verify-bundle`, with integrity-only meaning.

The signature binds the complete bundle bytes, including compatibility/checksum
metadata, plus every logical object's bytes, digest, length, path, and role.
`report.json` binds the deterministic report and its verdict; evidence, timeline,
events, explanation, and snapshots are included when present. There are no mutable
exclusions. Logical objects live inside one JSON file; verification extracts none.
There is no signed public projection. Raw seller JWS and payment signatures stay
excluded by existing bundle rules; issuer signing cannot replay seller crypto.

## Verify on a recipient's machine

Obtain the issuer's public key through a separately trusted channel. A key received
beside the bundle, without independent authentication, does not establish trust.
Only the bundle, detached envelope, and trusted public key are needed:

```bash
uv run settlediff verify-assurance bundle.json \
  --signature bundle.assurance.json --issuer-public-key trusted-issuer.pub.pem
```

Alternatively, supply the previously trusted SHA-256 fingerprint printed during
key setup using `--issuer-fingerprint LOWERCASE_HEX_DIGEST`. This is SHA-256 of the
raw 32-byte public key, not its PEM file. The embedded key is accepted only when
its fingerprint matches. If both anchors are given, both must match.

Output is JSON: `status`, `issuer_fingerprint`, and `machine_verdict`. Only
`AUTHENTIC` exits zero. Failures withhold the machine verdict:

| Status | Meaning |
|---|---|
| `INVALID_SIGNATURE` | Signature fails under the trusted issuer key |
| `CONTENT_MISMATCH` | Bundle differs, fails existing checks, or disagrees with signed manifest |
| `UNTRUSTED_ISSUER` | No anchor, anchor mismatch, or explicitly revoked fingerprint |
| `UNSUPPORTED_FORMAT` | Unsupported/malformed envelope, key file, encoding, or unsafe file access |
| `MISSING_ARTIFACT` | A required bundle, envelope, or caller-supplied key file is absent |

There is no fallback to checksum-only success. `verify-bundle` remains the
explicit integrity-only command; it cannot authenticate issuance.

## Automated recipient check

The [CI workflow](../../.github/workflows/ci.yml) runs two additional Linux jobs
after the offline quality matrix passes:

1. **Build synthetic issuer delivery** builds the wheel and exports hash-locked
   runtime requirements from `uv.lock`. The portability script replays only the
   tracked `x402-independent-confirmed` fixture, exports its redacted bundle and a
   legacy schema-2 counterpart, and signs the schema-3 bundle with a freshly
   generated in-memory test key. Its temporary SQLite database is removed before
   transfer; the key is never serialized. Only this explicit synthetic test creates
   an ephemeral key; normal bundle signing still requires a provisioned issuer key.
2. **Verify in a fresh recipient environment** runs on another hosted runner with
   no repository checkout. It downloads the wheel, requirements, check script and
   three synthetic evidence files, creates a new virtual environment, installs
   hash-checked runtime dependencies and the wheel without editable source, and
   executes the recipient checks from outside the checkout using Python `-I`.

The expected issuer fingerprint travels through GitHub's job-output channel,
separately from the artifact's embedded key. The recipient does not derive its pin
from the evidence. The workflow and both runners remain under this repository's
control: GitHub's workflow channel is the test's trust anchor, not an independent
auditor or production key-distribution service.

| Recipient case | Required result |
|---|---|
| Original bundle and separately supplied fingerprint | `AUTHENTIC`, existing `VERIFIED` fixture verdict |
| Changed bundle bytes | `CONTENT_MISMATCH`, no machine verdict |
| Wrong external fingerprint | `UNTRUSTED_ISSUER`, no machine verdict |
| Explicitly revoked fingerprint | `UNTRUSTED_ISSUER`, no machine verdict |
| Missing detached signature | `MISSING_ARTIFACT`, no machine verdict |
| Legacy schema-2 checksum verification | Success explicitly labeled integrity-only |
| Legacy bundle presented with the schema-3 signature | `CONTENT_MISMATCH`, no authentication fallback |

Each CLI verification runs in a subprocess with an empty working directory,
minimal environment and a Python audit hook blocking socket connections and DNS
lookups. This catches accidental network use; it is not an OS sandbox against
malicious native code. Package installation, action setup and artifact transport
use the network **before** verification. No production issuer/wallet key, database,
credentials, raw seller signature or live evidence is transferred. The synthetic
artifact has one-day retention. Public-repository logs/artifacts must never be used
for private customer bundles.

The integration test runs the same seven CLI cases locally without downloading
packages:

```bash
uv run pytest tests/integration/test_assurance_portability.py -q
```

To reproduce the wheel installation and recipient check locally on macOS/Linux:

```bash
recipient_tmp="$(mktemp -d)"
uv build --wheel --out-dir "$recipient_tmp/dist"
uv export --locked --no-dev --no-emit-project --quiet \
  --output-file "$recipient_tmp/requirements.txt"
fingerprint="$(uv run --locked python scripts/assurance_portability.py issue "$recipient_tmp/evidence")"
cp scripts/assurance_portability.py "$recipient_tmp/"
uv venv --python 3.12 "$recipient_tmp/venv"
uv pip install --python "$recipient_tmp/venv/bin/python" --require-hashes \
  --requirements "$recipient_tmp/requirements.txt"
uv pip install --python "$recipient_tmp/venv/bin/python" --no-deps "$recipient_tmp"/dist/*.whl
(
  cd "$recipient_tmp"
  "$recipient_tmp/venv/bin/python" -I "$recipient_tmp/assurance_portability.py" verify \
    "$recipient_tmp/evidence" --issuer-fingerprint "$fingerprint"
)
```

The command prints a JSON map of the seven expected outcomes and exits nonzero
if a case differs. Use a new temporary directory for each trial; existing delivery
directories are refused. The helper is a synthetic release check, not a general
bundle ingestion or key-management command. For real evidence, use `verify-assurance`
with an independently authenticated production issuer key as documented above.

The workflow uses standard hosted runners with read-only repository permissions,
immutable action references and no repository secrets. Standard hosted runner usage
is free for public repositories; private repositories consume their plan's included
allowance and may incur charges. Artifact storage has separate limits; one-day
retention keeps this small test short-lived. See
[GitHub Actions billing](https://docs.github.com/en/billing/concepts/product-billing/github-actions)
and [job outputs](https://docs.github.com/en/actions/how-tos/write-workflows/choose-what-workflows-do/pass-job-outputs).

## Rotation and compromise

Distribute each new public key/fingerprint independently before accepting it.
Supply `--revoked-issuer FINGERPRINT` (repeatable) to reject a known compromised key,
including one that otherwise matches the trusted anchor. Revocation is local caller
policy, not a network service. On compromise stop using the old key, notify relying
parties through the established trust channel, revoke it, and reassess old reports.
No trusted timestamp establishes that a signature predates compromise.

The exact wire format, domain separation, trust assumptions, and compatibility are
specified in [ADR 0015](../decisions/0015-issuer-signed-private-bundles.md). Its narrow
raw-byte JSON encoding is not advertised as RFC 8785 JCS. The existing maintained
cryptography dependency supplies Ed25519; no new dependency or identity service is
introduced. [Phase 2's design](evidence-transparency-design.md) recommends deferring
transparency-log implementation until an independent history consumer and operating
witness/monitor arrangement exist. It adds no logging requirement to this guide.
[Synthetic TLSNotary research](../testing/tlsnotary-synthetic-2026-10-16.md) now has
a separate reproducible experiment. Paid TLSNotary integration and ZK policy proof
work still need separate approval and do not affect issuer authentication.
