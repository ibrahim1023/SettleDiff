# Project health review — 2026-10-12

Scope: the architecture scan covered investigation, evidence summaries, storage,
and telemetry. The two-axis diff review covered this turn's changes from `5364e67`
plus the new eval files. Pre-existing signed-offer implementation and test edits,
dependency changes, and the plan rename were excluded from that diff review.

Skills applied: Matt Pocock's architecture survey, codebase-design vocabulary,
session retrospective, and parallel Standards/Spec code review. The architecture
survey is available in the shared project skill directory for Codex and Devin.

## Architecture

Three confirmed defects were fixed with regressions:

- The CLI supplied an empty artifact tuple to evidence tools, which also selected
  Perflo-specific source strings. Returned citation handles failed strict grounding.
  Collected artifacts now reach the summary module; cross-rail selection preserves
  actual handles and represents missing handles as `null`. A CLI model trajectory
  proves a returned handle survives grounding and persistence.
- Failed investigation paths discarded completed usage. A run-scoped usage
  accumulator now survives fallback, with request/tool/token accounting regressions.
- OTLP exporters received the same explicit URL. The telemetry module now derives
  separate standard trace/metric paths. Tests inspect HTTP requests from the real
  exporters while intercepting transport.

The remaining candidate is **worth exploring**, rather than a correctness fix:
durable coordination is split between the CLI and the callback-heavy application
run module. Moving that coordination into the application module could improve
locality while leaving interactive consent in the CLI. Retain the real payment
adapter seam and deterministic verifier; the findings do not justify a new agent
framework or generic orchestration infrastructure. This refactor is not implemented.

## Standards

The independent review found two documented-standard breaches in the initial changes:

- Promotion could copy ordinary keyed account identifiers. Promotion now recursively
  requires synthetic values under recognized identifier keys and checks secret-field
  redaction separately. A negative `accountId` case proves refusal before output.
- Excessive provider token usage could exceed result-model ceilings and prevent
  fallback. Observed counters now retain truthful nonnegative overruns in both the
  investigation result and persisted explanation record; execution budgets remain
  bounded. A 100,001-input/10,001-output-token response proves fallback remains valid.

Both findings were re-reviewed and closed. No additional baseline smell was reported.

## Spec

The independent review found two experiment-comparison defects:

- Saving to the baseline path could replace it before comparison. Baselines are now
  validated before execution or writes; identical paths, symlink aliases, and existing
  hardlink aliases are refused. The regression preserves the original baseline.
- Changed case definitions could still receive improvement/regression labels.
  Comparison now labels only unchanged cases and evidence; changed IDs are reported
  separately. Tests cover both changed criteria and unchanged outcome flips.

Both findings were re-reviewed and closed. The review otherwise found the runnable
cases, offline interfaces, intercepted writes, metadata, reviewed synthetic promotion,
CI discovery, and live-trial documentation implemented within scope.

Standards: two findings resolved. Spec: two findings resolved.

## Retrospective

The former dataset checked scenario names instead of executing each scenario. Keep
the [eval guide](evals.md) beside the verification loops so future changes exercise
observed tools and graders. Existing offline CI already runs pytest evals; promoted
cases join it automatically. Experiment records remain local and contain digests
and bounded scenario vocabulary instead of live payloads.

Tracker setup is separate from installing skills; preserve `docs/decisions/` as the
ADR location rather than introducing another documentation tree. No tracker writes
were needed for this review.

Incremental commits were initially blocked by sandboxed Git writes. The follow-up
integration uses approved Git operations. It also repairs the pre-existing signed
test's misplaced parametrization and the seller-origin binding defect that the
corrected test exposed. That work is preserved as a separate signed-evidence increment.
