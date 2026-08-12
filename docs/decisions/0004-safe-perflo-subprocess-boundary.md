# ADR 0004: Safe Perflo Subprocess and Mutation Boundary

**Status:** Accepted  
**Date:** 2026-08-12

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

## Rejected

- Shell strings or `shell=True`.
- Generic arbitrary-command tools.
- Retrying mutations on timeouts or network errors.
- Reimplementing Perflo's internals.
