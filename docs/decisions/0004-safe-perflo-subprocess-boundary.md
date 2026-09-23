# ADR 0004: Safe Perflo Subprocess and Mutation Boundary

**Status:** Accepted  
**Date:** 2026-08-12

**Updated:** 2026-09-23 for Perflo v8; the original decision and its date are unchanged.

## Context

Perflo is integrated through its CLI. Commands handle sensitive financial data, and a timed-out mutation may have succeeded even when no clean response reached SettleDiff.

## Decision

Invoke an allowlisted executable with `asyncio.create_subprocess_exec` and argument arrays. Never construct a shell command. Bound runtime and captured output, preserve stdout/stderr separately, parse the uniform JSON envelope, and redact diagnostics.

Expose paid execution only through a one-use authorization capability bound to target, request-body digest, and budget. Mutations have no automatic retry path. Submission uncertainty transitions the run into evidence-only recovery using transaction status, activity, or history.

## Consequences

- Shell injection risk is reduced.
- Command behavior can be contract-tested from captured envelopes.
- A paid operation cannot be repeated by the model loop.
- Recovery is slower but prioritizes preventing double spend.

## Update — Perflo v8 (2026-09-23)

Perflo v8 addresses vendors by catalog slug rather than HTTP target. The boundary is
narrower and more explicit:

- The allowlisted read commands are `vendor <slug> --json`, `activity --json`, and
  `tx status <hash> --json`. The single mutation is `pay <slug>` with conditional
  `--input`, `--query`, and `--sub-account` arguments and a major-unit USD `--max-charge`.
  SettleDiff never passes `--out`, `--full`, or `--no-wait`.
- Authorization binds a discriminated exact resource digest — slug, canonical `input`,
  canonical `query`, and optional sub-account — plus the authorized budget, the canonical
  vendor contract digest, and three distinct economic values: the advertised `price`, the
  vendor's required minimum `maxChargePerCall`, and the user-authorized maximum charge.
- The vendor declaration is re-read after interactive confirmation and before the
  one-use capability is consumed; contract drift or malformed evidence fails before
  `pay` launches.
- Mutations still have no automatic retry path; submission uncertainty enters
  evidence-only recovery exactly as before.
- Bounded CLI output projections are evidence, not delivery truth: capped previews keep
  their truncation metadata, and provider `savedTo` file projections keep byte counts
  with local paths redacted before persistence.
- Credit-funded pay results expose no canonical on-chain transaction hash even when
  additive provider data mentions one.
- Perflo `pay`, agent Activity, and `tx status` remain one provider trust domain; their
  agreement is consistency, not independent settlement verification.

## Rejected

- Shell strings or `shell=True`.
- Generic arbitrary-command tools.
- Retrying mutations on timeouts or network errors.
- Reimplementing Perflo's internals.
- Passing Perflo output-file flags (`--out`, `--full`, `--no-wait`) or treating provider
  local file paths as canonical delivery evidence.
