# ADR 0017: Keep TLSNotary in Synthetic Research

**Status:** Proposed — synthetic research complete; paid integration not authorized
**Date:** 2026-10-16

## Context

The owner approved Phase 3 of the cryptographic-assurance plan: investigate
TLSNotary and try a new, non-paid HTTPS session without private application data.
The [primary-source review](../research/tlsnotary-2026-10-15.md) selected the pinned
`v0.1.0-alpha.15` release for evaluation. Its upstream warning excludes production
use, and its supported MPC-TLS path remains TLS 1.2.

The [synthetic validation](../testing/tlsnotary-synthetic-2026-10-16.md) succeeded:
a local HTTPS fixture, fresh co-hosted notary key, and strict offline verifier bound
server identity and the exact HTTP/body contract. Ten negative cases were rejected.
This is evidence that the selected research workflow executes, not an independent
notary deployment or proof of a real purchase.

The current x402 signer owns its HTTP connection and emits a bounded response
summary through result schema 3. Neither that contract nor static inspection of
the local Node transports establishes a TLSNotary-capable connection. Authenticating
already completed arbitrary responses after the fact is not supported by this model.

## Decision

**Retain the isolated experiment; defer production and paid integration.** The
research code belongs under `experiments/tlsnotary-alpha15`, outside application
imports, Python dependencies, report models, and default CI network execution.
The runner requires explicit loopback opt-in and keeps generated data outside the
checkout. The verifier result is research-only and cannot modify financial findings.

The research verifier requires a separately supplied local notary-key pin, test-CA
certificate verification, expected server, TLS 1.2, bounded session age, complete
authenticated transcript, and one exact request/response/body digest. The fixture
uses public test certificates; the patched notary uses a fresh ephemeral key, not
a wallet, issuer, seller, or production notary key. Full synthetic disclosure is
intentional; no hidden-body ZK claim is made.

The production gate remains closed because upstream is prerelease/non-production,
independent notary custody and trust distribution are absent, signer transport
compatibility is unimplemented, and realistic overhead/privacy budgets are unknown.
Single loopback measurements cannot establish WAN costs or production suitability.

A future integration proposal must establish all of:

1. A reviewed, pinned release and dependency/security/license assessment appropriate
   to the intended deployment.
2. An independent notary, authenticated key distribution and rotation/revocation,
   certificate roots, time/freshness policy, and explicit collusion assumptions.
3. A signer-owned TLS client design that participates before the HTTP exchange,
   preserves exact authorization, and never retries after possible payment.
4. Target TLS/HTTP compatibility, bounded transcript/commitment sizes, response
   framing/compression rules, and disclosed ranges sufficient for every claim.
5. Disclosure and retention rules excluding wallet signatures, credentials, raw
   private requests/responses, and prover secrets from reports, telemetry, and
   public artifacts; an accepted portable proof format with offline negative tests.
6. Measured baseline/WAN resource budgets and a concrete relying party who needs
   source-authenticated delivery evidence.
7. Separate owner approval before any paid experiment, including the exact request,
   digest, budget, and signer changes where applicable.

These are gates for a later proposal, not permission to implement a bridge now.
Existing signed seller assessments and issuer-signed bundles retain their stated
provenance limits; a notary assertion would not replace independent settlement.

## Consequences

Phase 3 completes with a reproducible synthetic session, strict verifier, negative
matrix, cost observations, and a documented no-go for production adoption. No
financial/report/authorization semantics, paid flow, retry handling, public projection,
or exported issuer/bundle wire format changes.

TLSNotary can bind server bytes only under its protocol and trust assumptions.
It does not establish actual funds transfer, truth of server claims, or subjective
fulfillment. ZK policy proofs and later phases remain outside this approval.
