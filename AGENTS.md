# AGENTS.md

## Mission

Build SettleDiff as a local developer tool that keeps agentic investigation separate from deterministic financial truth. Read the tracked foundation design and relevant ADRs before changing behavior. `Product-spec.md` and `task.md` are local-only inputs and must never be staged or committed.

## Non-negotiable boundaries

- Only deterministic code may produce findings or verdicts.
- The agent may select evidence and explain existing findings; it may not mutate them.
- A paid execution requires explicit authorization for one exact target, body digest, and budget.
- Never automatically retry a money-moving command after any uncertain submission.
- Default tests and evals must not make live model, Perflo, Context.dev, or paid calls.
- Use `Decimal` for money, aware UTC timestamps, strict Pydantic boundary models, and redaction before persistence or telemetry.

## Workflow

1. Work test-first: failing test, minimal change, passing focused test, then broader verification.
2. Commit implementation and fixes in small, independently reviewable bits. Each commit must be coherent and pass the checks relevant to its scope; do not accumulate the entire feature into one commit.
3. Preserve public contracts or update their tests, ADRs, and docs in the same change.
4. Run the verification loop in `docs/development/verification-loops.md` before claiming completion.

Do not use Superpowers skills to execute implementation or fixes. Follow the tracked implementation plan, repository tests, ADRs, and verification loops directly. The files under `docs/superpowers/` are planning records, not an implementation workflow dependency.

## No AI slop

Avoid AI slop throughout development. Do not add speculative abstractions, duplicate wrappers, generic helper modules, placeholder/TODO prose, fake data presented as real, needless dependencies, verbose comments that restate code, broad exception swallowing, invented API fields, or generic dashboard styling. Prefer exact domain names, small focused files, evidence-backed behavior, purposeful UI copy, and deletion of code that does not serve an accepted requirement. If a generated artifact cannot explain its consumer and test, remove it.

## Commands

Use `uv run` for Python tools. Use exact test paths while iterating, then run the full offline gate. Live checks require explicit opt-in environment flags and must never run in ordinary CI.
