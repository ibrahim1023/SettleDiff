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
TLSNotary and ZK proofs still need separate approval.
