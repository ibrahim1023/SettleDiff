# Evidence transparency research

Reviewed 2026-10-15. Phase 2 design only; no service, submission, dependency,
or operating witness is established by this note.

## Verified protocol facts

**Merkle proofs.** RFC 9162 (December 2021, Experimental, not Internet
Standards Track) separates leaf hashes with `0x00` and internal-node hashes with
`0x01`. Inclusion proves membership under a particular root; consistency proves
that the earlier tree is a prefix of the later tree. Neither establishes that
every report was submitted or that its financial assertions are true. The RFC
explicitly warns that a dishonest log can serve different clients inconsistent
views; its mechanisms alone do not prevent that attack.
[RFC 9162, §§1, 2.1](https://www.rfc-editor.org/rfc/rfc9162.html)

**Witness state.** The versioned C2SP witness protocol currently resolves to
**v1.1.0**. A witness authenticates a configured log's checkpoint, verifies
consistency against its saved checkpoint, and atomically persists its new state
before returning a timestamped cosignature. Starting from size zero needs no
consistency proof. Consequently, trustworthy initialization and preservation of
state matter. The monitoring interface should expose recent cosigned checkpoints;
the specification warns that monitors can otherwise receive stale or partitioned
views. It does not select SettleDiff's witness quorum or trusted operators.
[tlog-witness v1.1.0](https://c2sp.org/tlog-witness@v1.1.0)

**Checkpoint and signature format.** C2SP checkpoints bind origin, decimal tree
size, and base64 root in a signed note. Optional extension lines are discouraged:
some algorithms do not cover them. Plain Ed25519 log signatures lack timestamps;
checkpoint cosignatures include them. Current guidance prefers ML-DSA-44 for new
cosigner deployments; this is a compatibility consideration, not a reason to
change Phase 1's Ed25519 issuer format.
[tlog-checkpoint v1.1.0](https://c2sp.org/tlog-checkpoint@v1.1.0),
[tlog-cosignature v1.1.0](https://c2sp.org/tlog-cosignature@v1.1.0)

Signed notes use precise UTF-8/newline framing and named signature lines.
Their four-byte key IDs are routing identifiers, not trust anchors; verifiers
must already possess the trusted public keys.
[signed-note v1.1.0](https://c2sp.org/signed-note@v1.1.0)

**Sigsum status and disclosure.** The official documentation index identifies log
protocol **v1**, linking release **v1.0.1**, while proof-bundle and trust-policy
formats remain work in progress. Its witness-protocol “release candidate” label
lags the versioned C2SP page above. The getting-started example pins tools to
**v0.14.1**, demonstrates offline verification, and shows monitors exposing leaf
index, signer-key hash, and checksum. Thus opaque checksums still reveal linkable
signing activity. These tutorial versions are not a verified latest software
release. The linked GitLab specification bodies were unavailable during this
review; their exact current encodings and implementation licenses remain
unverified.
[Sigsum documentation](https://www.sigsum.org/docs/),
[getting started](https://www.sigsum.org/getting-started/),
[log v1.0.1](https://git.glasklar.is/sigsum/project/documentation/-/blob/log.md-release-v1.0.1/log.md)

## SettleDiff recommendations and unresolved gates

Keep Phase 1 as the available assurance mechanism. Transparency needs a relying
party who wants shared issuance history, an independent witness arrangement,
and an accountable monitor; none is established here.

Separate roles: the **issuer** signs a report; the **log** orders commitments and
publishes checkpoints; **witnesses** check history consistency; the **monitor**
retrieves entries, obtains private openings through an authorized channel, and
investigates unexpected issuance. Witness consistency alone cannot perform the
monitor's semantic investigation or discover deliberately unsubmitted reports.

Before implementation, define quorum intersection and the tolerated collusion
model across genuinely independent administrators, keys, and state stores.
Multiple keys controlled by one operator do not provide that independence.
Require durable rollback-resistant witness/client checkpoints, explicit bootstrap
and recovery, bounded checkpoint age and clock skew, key rotation, and outage
behavior. An offline proof establishes inclusion under its authenticated
checkpoint; it cannot establish that this remains the newest global view.

Publish no raw bundle, financial identifier, wallet/transaction ID, or naked
low-entropy amount digest. Review a domain-separated commitment with fresh
high-entropy blinding, keeping its opening private. Even that leaves log order,
volume, signer linkage, submission timing, and operator-visible network metadata
to assess; hashing is not anonymity.

Compare C2SP-compatible infrastructure with Sigsum only after pinning interoperable
proof/policy formats and implementation licenses. C2SP specifications are CC BY
4.0 and repository code BSD-1-Clause; those licenses do not cover unrelated
implementations. No provider is selected. Any implementation benchmark, public
submission, or deployment requires a later approved scope.
[C2SP licensing](https://c2sp.org/tlog-witness@v1.1.0)
