# ADR 0002: PydanticAI with Hyperfusion

**Status:** Accepted  
**Date:** 2026-08-12

## Context

The investigator needs typed tools, structured output, hard usage limits, offline loop tests, and portable tracing. Hyperfusion is the selected model provider and exposes an OpenAI-compatible API.

## Decision

Use PydanticAI for the single investigator. Configure an OpenAI-compatible Chat Completions model through a provider factory using Hyperfusion's injected base URL, API key, and model ID. Validate the exact model with an unpaid compatibility contract before agent implementation is accepted.

Use current PydanticAI APIs (`output_type`, `result.output`, typed dependencies, `UsageLimits`, `TestModel`, and `FunctionModel`). Provider-specific compatibility belongs in a model profile, not prompts or domain code.

## Consequences

- Agent code shares Pydantic conventions with FastAPI and domain boundaries.
- The model provider remains replaceable behind one factory.
- CI can block real model requests.
- Tool/request/token/cost limits are enforced by the SDK and application deadline.
- Hyperfusion edge compatibility remains a tracked risk until the contract passes.

## Rejected

- OpenAI Agents SDK: credible and free, but less aligned with the selected testing/eval stack.
- Hand-written loop: unnecessary recreation of limits, validation, and instrumentation.
- LangGraph: durable graph features do not serve the short MVP loop.
