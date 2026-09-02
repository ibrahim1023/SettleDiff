# Agent Evaluation Strategy

## Scope

The current eval layer checks that the Investigation Agent remains evidence-only and that fallback explanations stay aligned with deterministic reports. It does not grade or replace financial checks.

## Current offline harness

`tests/evals/` is a small pytest harness with six named synthetic cases:

- success;
- missing evidence;
- ambiguous activity;
- limit exhaustion;
- unauthorized execution;
- prohibited retry.

Four deterministic graders are implemented:

- `outcome_correct` compares explanation and machine verdicts;
- `citations_valid` limits cited findings and artifacts to known IDs;
- `safe_trajectory` permits only the three evidence-reading tools;
- `trajectory_satisfies` checks required and forbidden tool names.

The regression tests also prove that payment, retry, shell, filesystem, and network tools are absent from accepted scripted trajectories. This harness uses pytest and project models; it does not use Pydantic Evals or an LLM judge.

## Agent contract coverage

Separate unit tests use PydanticAI `FunctionModel` and `TestModel` to exercise tool calls, structured output, grounding, request/tool/token limits, and deterministic fallback behavior. The opt-in Hyperfusion contract checks provider mechanics against an owner-configured model. It is a compatibility test, not a capability benchmark.

## Promotion policy

A production failure may become an offline eval only after its inputs are sanitized and its expected trajectory is reviewed. Add a grader only when a committed case consumes it. Do not publish pass@k, percentile, recall, cost, or quality claims until repeated trials and a versioned result artifact actually exist.

## Release gate

- all offline agent, grounding, and eval tests pass;
- accepted trajectories contain no mutation tool;
- explanations cite only known findings and artifacts;
- model or grounding failure cannot alter the deterministic report;
- live model compatibility remains separately authorized and opt-in.
