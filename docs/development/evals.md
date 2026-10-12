# Offline agent evals

Run the dataset before and after changing the investigator, evidence tools, grounding,
or authorization. The cases use fixed synthetic evidence and scripted `FunctionModel`
responses. They measure enforcement and wiring; they do not estimate live model quality.

```bash
uv run pytest tests/evals tests/unit/agent -q
uv run python scripts/run_evals.py --out .local/evals/before.json
# After the change:
uv run python scripts/run_evals.py --out .local/evals/after.json --baseline .local/evals/before.json
```

The runner exits nonzero when any grader fails. Experiment records include UTC time,
code revision, dirty state, source/dataset/case/fixture digests, observed requested and
executed tools, completed token usage, fallback state, and separate grader results.
They exclude prompts, bodies, wallets, and live run identifiers. Outputs under `.local/`
are ignored by Git. Baseline comparison flags changed cases or evidence as incomparable
so changed expectations cannot silently masquerade as improvements.

## Cases and graders

The eight built-in cases cover independent success, missing evidence, ambiguous
Activity, limit exhaustion, authorization mismatch, uncertain-submission retry,
contradictory output, and unknown citations. Definitions live in `tests/evals/dataset.py`.
Each declares synthetic fixture, model stimulus, expected verdict/findings, required
tools, fallback expectation, and limits. `tests/evals/runner.py` calls the production
investigator and evidence-summary module and observes their actual tool executions.

The two payment cases also call the application authorization interface. Their write
is a local fake: mismatched authorization invokes it zero times, and uncertain
submission invokes it once before a second attempt is refused. The investigation
agent is separately challenged to request a payment tool, which it cannot execute.
No case can call Perflo, a signer, Context.dev, an RPC, or a live model.

Outcome, expected findings, citation validity, evidence coverage, allowed/required
trajectory, limits, completed usage, fallback, report immutability, authorization,
and intercepted-write counts are graded independently. A single failing safety
grader fails the case. Negative tests verify graders detect wrong expected verdicts
and excessive request counts.

## Promote a real failure safely

An exported operational span lacks the input needed for replay. Keep the original
local evidence bundle private. Reconstruct the relevant world state as a reviewed
synthetic fixture using the [fixture contract](../testing/strategy.md#fixture-contract).
Retain evidence classes and the economic relationships responsible for the failure.
Replace identifiers and secrets; fix timestamps and tool responses.

Write an `EvalCase` JSON definition with explicit expected behavior, independently
chosen verdict/check statuses, tool requirements, and fallback expectations. Use a
unique `case_id`; the `fixture` field names the copied fixture directory. The model
stimuli currently support grounded output, looping, a prohibited payment request,
contradictory output, and an unknown citation. New failure behaviors need an explicit
offline script and a regression test; arbitrary live prompts are not executed.

```bash
uv run python scripts/promote_eval.py \
  --bundle .local/flagged.sdbundle \
  --fixture .local/reviewed-synthetic-fixture \
  --criteria .local/reviewed-case.json \
  --reviewed
uv run pytest tests/evals -q
uv run python scripts/run_evals.py --out .local/evals/promoted.json
```

`--reviewed` attests that a human reviewed the sanitized evidence and expected
behavior. Promotion verifies bundle integrity and the synthetic fixture, refuses
unsafe filenames, symlinks, secrets, and existing destinations, and checks that the
fixture establishes the expected verdict/findings. It creates
`tests/evals/promoted/<case_id>/case.json`, a copied fixture, and `provenance.json`.
Provenance contains the original bundle's digest and a synthetic-reconstruction
marker; the source bundle and its payloads are not copied. This is a reviewed
reconstruction, not a claim to reproduce every live detail automatically.

Promoted cases run automatically in both pytest's existing offline CI gate and the
experiment runner. Review and commit a promoted directory only after reproducing
the failure with an appropriately failing grader and fixing it test-first. The bad
explanation is never the reference answer. Replaying these cases cannot issue a write.

## Live trials

Offline scripts give repeatable enforcement tests. Later live model evaluations
need explicit opt-in configuration and their own trial design: define repetitions,
all-of-k acceptance for safety, and an n-of-k bar for subjective quality before
running them. Passing k trials is evidence, not proof of universal reliability.
LLM judges require calibration against human labels and remain outside financial
findings and verdicts. This change adds no live trial or hosted evaluator.
