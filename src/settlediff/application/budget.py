"""Bounded investigation-cost budget for one run, separate from paid execution."""

from __future__ import annotations

import asyncio

from pydantic import BaseModel, ConfigDict, Field


class InvestigationBudgetExceeded(ValueError):
    """An investigation step would exceed its run-scoped budget."""


class InvestigationBudget(BaseModel):
    """Frozen per-run limits; immutable once issued."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    run_id: str
    contextdev_calls: int = Field(ge=0, le=10)
    model_requests: int = Field(ge=0, le=10)
    tool_calls: int = Field(ge=0, le=25)
    input_tokens: int = Field(ge=0, le=100000)
    output_tokens: int = Field(ge=0, le=10000)

    @classmethod
    def issue(
        cls,
        run_id: str,
        *,
        contextdev_calls: int,
        model_requests: int,
        tool_calls: int,
        input_tokens: int,
        output_tokens: int,
    ) -> InvestigationBudget:
        return cls(
            run_id=run_id,
            contextdev_calls=contextdev_calls,
            model_requests=model_requests,
            tool_calls=tool_calls,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )

    def check_run(self, run_id: str) -> None:
        """Reject use of this budget by any other run."""
        if run_id != self.run_id:
            raise InvestigationBudgetExceeded("budget does not cover this run")


class InvestigationBudgetRemaining(BaseModel):
    """Frozen snapshot of the per-limit headroom at one point in time."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    contextdev_calls: int
    model_requests: int
    tool_calls: int
    input_tokens: int
    output_tokens: int


class InvestigationBudgetState:
    """Mutable consumption counters kept behind an async lock."""

    def __init__(self, budget: InvestigationBudget) -> None:
        self._budget = budget
        self._contextdev_calls = 0
        self._model_requests = 0
        self._tool_calls = 0
        self._input_tokens = 0
        self._output_tokens = 0
        self._lock = asyncio.Lock()

    @property
    def budget(self) -> InvestigationBudget:
        return self._budget

    def remaining(self) -> InvestigationBudgetRemaining:
        budget = self._budget
        return InvestigationBudgetRemaining(
            contextdev_calls=budget.contextdev_calls - self._contextdev_calls,
            model_requests=budget.model_requests - self._model_requests,
            tool_calls=budget.tool_calls - self._tool_calls,
            input_tokens=budget.input_tokens - self._input_tokens,
            output_tokens=budget.output_tokens - self._output_tokens,
        )

    async def consume_contextdev_call(self, *, run_id: str | None = None) -> None:
        async with self._lock:
            self._check_run(run_id)
            if self._contextdev_calls >= self._budget.contextdev_calls:
                raise InvestigationBudgetExceeded("contextdev_calls budget exhausted")
            self._contextdev_calls += 1

    async def consume_model_request(self, *, run_id: str | None = None) -> None:
        async with self._lock:
            self._check_run(run_id)
            if self._model_requests >= self._budget.model_requests:
                raise InvestigationBudgetExceeded("model_requests budget exhausted")
            self._model_requests += 1

    async def consume_tool_call(self, *, run_id: str | None = None) -> None:
        async with self._lock:
            self._check_run(run_id)
            if self._tool_calls >= self._budget.tool_calls:
                raise InvestigationBudgetExceeded("tool_calls budget exhausted")
            self._tool_calls += 1

    async def consume_tokens(
        self, input_tokens: int, output_tokens: int, *, run_id: str | None = None
    ) -> None:
        async with self._lock:
            self._check_run(run_id)
            if input_tokens < 0 or output_tokens < 0:
                raise InvestigationBudgetExceeded("token consumption cannot be negative")
            if self._input_tokens + input_tokens > self._budget.input_tokens:
                raise InvestigationBudgetExceeded("input_tokens budget exhausted")
            if self._output_tokens + output_tokens > self._budget.output_tokens:
                raise InvestigationBudgetExceeded("output_tokens budget exhausted")
            self._input_tokens += input_tokens
            self._output_tokens += output_tokens

    def _check_run(self, run_id: str | None) -> None:
        if run_id is not None:
            self._budget.check_run(run_id)
