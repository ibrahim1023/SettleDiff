# ADR 0016: Defer Evidence Transparency Pending an Independent Consumer

**Status:** Proposed — Phase 2 design only; implementation not authorized
**Date:** 2026-10-15

## Context

The owner approved the Phase 2 design review after issuer assurance shipped under
[ADR 0015](0015-issuer-signed-private-bundles.md). On 2026-10-15 the owner confirmed
there is **no committed external verifier** requiring shared report history.
The baseline is `fa9fb3f`: signed private bundles can authenticate issuance against
a separately trusted issuer key; they do not establish a shared append-only history.

SQLite timelines and snapshots support local investigation. They are not externally
witnessed transparency records; local run deletion remains supported. Current
public publication is an unsigned static allowlist projection and uploads nothing.
Operational Logfire traces are not a transparency log or a source of financial truth.

The useful additional claim would be that a particular signed issuance is included
in an authenticated history that extends a previously trusted checkpoint. This is
separate from proving settlement, delivery, complete issuance, or a globally current
view. Primary-source findings are recorded in the
[research note](../research/evidence-transparency-2026-10-15.md).

## Decision

**Recommend deferring implementation.** Complete Phase 2 with this proposed ADR
and its [conditional design](../development/evidence-transparency-design.md).
Keep Phase 1 available without any logging requirement. Do not add a service,
database migration, runtime dependency, key, command, environment setting, public
submission, or new financial/report field under this approval.

If a real consumer later justifies the work, use a reviewed Merkle transparency
protocol with domain-separated leaves/nodes, signed checkpoints, inclusion proofs,
and consistency proofs. A linked local hash chain is insufficient. Verifiers must
pin log origin/key, issuer trust, witness policy, and previously accepted state
through independent channels. At least one independently operated witness must
participate in the accepted policy, with a monitor and external checkpoint retention.
Keys under the same administrator are not independent witnesses.

A checkpoint controlled only by the issuer does not solve conflicting histories.
Neither do arbitrary witness counts: accepted sets must intersect in an honest,
state-preserving witness under an explicit compromise model. A network outage or
missing proof is not by itself evidence of log equivocation. Offline verification
cannot establish that its checkpoint is the latest worldwide.

Start any future integration outside the payment and verdict paths, after ordinary
export/signing. A detached private history receipt would bind the exact issuer
envelope to a committed entry; neither bundle schema 3 nor assurance-signature/v1
would change. History failure must never change a machine verdict, trigger a paid
retry, or turn provider assertions into independent settlement evidence.

Public entries must not contain reports, wallet/transaction/run identifiers,
wallet/payment signatures, seller JWS, private issuer-envelope contents/signatures,
private bundle digests, or unblinded low-entropy financial commitments.
The candidate design uses a fresh private 256-bit blinding value to commit to the
exact issuer envelope; only its opaque commitment is public. The private opening
is disclosed only to authorized recipients. This needs a separate disclosure review:
public order, counts, timing, and operator-visible metadata still leak information.
Deleting local evidence cannot erase an already published commitment. The selected
protocol's log/witness public keys and checkpoint authentication signatures are
separate public metadata, allowed only after the same disclosure review; this does
not authorize publishing private issuer or payment signatures.

C2SP-compatible checkpoints/witnessing and Sigsum are research candidates, not
selected infrastructure. Exact implementation versions, licenses, interoperable
formats, trust policy, and operating partners must be pinned before adoption.
Phase 1's issuer key must not be repurposed as a log or witness key.

### Reopening gate

Require all of the following before proposing implementation approval:

1. A named relying party, concrete cross-report history question, and acceptance test
   that individual signed bundles cannot satisfy.
2. An independently operated witness arrangement, monitor owner, externally retained
   checkpoints, compromise/quorum assumptions, and incident contacts.
3. An approved public disclosure contract, private-opening custody, deletion/retention
   explanation, and explicit publication consent.
4. A pinned protocol/backend and complete receipt encoding, parser bounds, algorithm
   allowlist, bootstrap, key rotation/revocation, and rollback recovery specification.
5. Consumer-agreed latency, freshness, availability, storage, and cost budgets, with
   a separately authorized synthetic benchmark and failure matrix.

The current gate fails at item 1; operating partners and budgets are also absent.
No runtime benchmark or deployment was performed. The design includes calculated
proof-size bounds rather than claiming measured performance.

## Consequences

SettleDiff retains portable issuer authentication without creating an unattended
public service or irreversible disclosure obligation. A recipient can trust a
specific signed bundle while remaining unable to infer global issuance history.

This ADR remains proposed: approving a design review does not approve its future
implementation or operating assumptions. It records a concrete path to reconsider
transparency without weakening the existing financial boundaries. TLSNotary, ZK
proofs, and subsequent phases remain outside this approval.

## Alternatives considered

- **Local Merkle root or hash chain:** cheap local consistency aid, but the same
  operator can replace history and its unauthenticated anchor.
- **Publish raw bundles or their existing manifest:** exposes data never approved
  for public disclosure; current redaction does not establish publication safety.
- **Post a root to the issuer's own repository:** useful distribution, but mutable
  operator-controlled hosting alone is not independent witnessing.
- **Use an external public log immediately:** introduces permanent metadata and
  operating trust without a current consumer or accepted privacy contract.
