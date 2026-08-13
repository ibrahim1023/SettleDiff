# Decisions Requiring Owner Input

These choices are intentionally unresolved. None blocks the documentation foundation; the first three must be decided before their named implementation gate.

| Decision | Recommended default | Needed by |
|---|---|---|
| Hyperfusion endpoint and model ID | The configured model was probed on 2026-08-13: it returned structured output but did not call a required tool. Select a tool-capable model, then rerun the opt-in compatibility contract; keep both values in environment configuration. | Implementation Task 8 |
| Live CLI confirmation behavior | Require an interactive confirmation showing target, canonical body digest, and budget. Add non-interactive authorization only when a concrete automation use case defines an equally explicit approval mechanism. | Implementation Task 10 |
| Local artifact retention | Keep reports until the developer deletes them, provide explicit per-run deletion, and do not collect raw sensitive payloads by default. Revisit time-based retention only if real usage requires it. | Implementation Task 11 |
| Context.dev timing | Include the single narrow evidence path after the offline core, CLI, and debugger UI pass. Treat it as bonus acceptance, not a release blocker. | Implementation Task 12 |
| Repository license | Choose the intended open-source or proprietary license before public launch; do not infer ownership terms. | Before external contributions or launch |
| Hosted deployment target | Keep the MVP loopback-only. Choose hosting, authentication, retention, and secret management together in a new threat model and ADR. | Post-MVP deployment |
| ElevenLabs voice demo | Defer until the core acceptance suite passes; it must remain a thin interface over the same run and verdict. | Post-core demo |

No input is required for PydanticAI, Hyperfusion as the provider, the single-agent boundary, deterministic verdict ownership, fixture-first tests, optional OpenTelemetry, SQLite, or the server-rendered UI; those decisions are accepted in the existing ADRs.
