from __future__ import annotations

from collections.abc import Awaitable, Callable

import pytest
from pydantic import ValidationError

from settlediff.application.budget import (
    InvestigationBudget,
    InvestigationBudgetExceeded,
    InvestigationBudgetState,
)

LIMITS: dict[str, int] = {
    "contextdev_calls": 2,
    "model_requests": 3,
    "tool_calls": 4,
    "input_tokens": 100,
    "output_tokens": 50,
}


def budget() -> InvestigationBudget:
    return InvestigationBudget.issue("syn_run_001", **LIMITS)  # type: ignore[arg-type]


def state() -> InvestigationBudgetState:
    return InvestigationBudgetState(budget())


def test_budget_is_frozen_and_bounded() -> None:
    issued = budget()

    assert issued.run_id == "syn_run_001"
    assert issued.contextdev_calls == 2
    assert issued.model_requests == 3
    assert issued.tool_calls == 4
    assert issued.input_tokens == 100
    assert issued.output_tokens == 50

    with pytest.raises(ValidationError):
        issued.contextdev_calls = 1  # type: ignore[misc]


@pytest.mark.parametrize(
    "override",
    [
        {"contextdev_calls": -1},
        {"contextdev_calls": 11},
        {"model_requests": -1},
        {"model_requests": 11},
        {"tool_calls": -1},
        {"tool_calls": 26},
        {"input_tokens": -1},
        {"input_tokens": 100001},
        {"output_tokens": -1},
        {"output_tokens": 10001},
    ],
)
def test_budget_rejects_out_of_bounds_limits(override: dict[str, int]) -> None:
    with pytest.raises(ValidationError):
        InvestigationBudget.issue("syn_run_001", **(LIMITS | override))  # type: ignore[arg-type]


def test_budget_issue_binds_one_run_id() -> None:
    first = InvestigationBudget.issue("syn_run_001", **LIMITS)  # type: ignore[arg-type]
    second = InvestigationBudget.issue("syn_run_002", **LIMITS)  # type: ignore[arg-type]

    first.check_run("syn_run_001")
    second.check_run("syn_run_002")
    with pytest.raises(InvestigationBudgetExceeded, match="run"):
        first.check_run("syn_run_002")


@pytest.mark.asyncio
async def test_consume_succeeds_up_to_exact_limit() -> None:
    active = state()

    await active.consume_contextdev_call()
    await active.consume_contextdev_call()
    await active.consume_tokens(60, 30)
    await active.consume_tokens(40, 20)

    remaining = active.remaining()
    assert remaining.contextdev_calls == 0
    assert remaining.model_requests == 3
    assert remaining.input_tokens == 0
    assert remaining.output_tokens == 0


@pytest.mark.asyncio
async def test_exhaustion_raises_and_consumes_nothing() -> None:
    active = state()

    await active.consume_contextdev_call()
    await active.consume_contextdev_call()
    with pytest.raises(InvestigationBudgetExceeded, match="contextdev"):
        await active.consume_contextdev_call()

    assert active.remaining().contextdev_calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("consume", "limit_name"),
    [
        (InvestigationBudgetState.consume_contextdev_call, "contextdev_calls"),
        (InvestigationBudgetState.consume_model_request, "model_requests"),
        (InvestigationBudgetState.consume_tool_call, "tool_calls"),
    ],
)
async def test_exact_limit_then_exhaustion_per_counter(
    consume: Callable[[InvestigationBudgetState], Awaitable[None]], limit_name: str
) -> None:
    active = state()

    for _ in range(LIMITS[limit_name]):
        await consume(active)

    with pytest.raises(InvestigationBudgetExceeded):
        await consume(active)

    assert getattr(active.remaining(), limit_name) == 0


@pytest.mark.asyncio
async def test_token_consume_is_atomic_on_partial_overflow() -> None:
    active = state()

    await active.consume_tokens(80, 40)

    with pytest.raises(InvestigationBudgetExceeded, match="input_tokens"):
        await active.consume_tokens(21, 5)
    with pytest.raises(InvestigationBudgetExceeded, match="output_tokens"):
        await active.consume_tokens(10, 11)

    remaining = active.remaining()
    assert remaining.input_tokens == 20
    assert remaining.output_tokens == 10

    await active.consume_tokens(20, 10)
    assert active.remaining().input_tokens == 0
    assert active.remaining().output_tokens == 0


@pytest.mark.asyncio
async def test_negative_token_consumption_rejected() -> None:
    active = state()

    with pytest.raises(InvestigationBudgetExceeded):
        await active.consume_tokens(-1, 0)
    with pytest.raises(InvestigationBudgetExceeded):
        await active.consume_tokens(0, -1)

    assert active.remaining().input_tokens == 100
    assert active.remaining().output_tokens == 50


@pytest.mark.asyncio
async def test_cross_run_consumption_rejected_without_consuming() -> None:
    active = state()

    with pytest.raises(InvestigationBudgetExceeded, match="run"):
        await active.consume_model_request(run_id="syn_run_other")

    assert active.remaining().model_requests == 3
    await active.consume_model_request(run_id="syn_run_001")
    assert active.remaining().model_requests == 2


def test_remaining_returns_frozen_snapshot() -> None:
    active = state()

    snapshot = active.remaining()
    assert snapshot.contextdev_calls == 2
    assert snapshot.tool_calls == 4

    with pytest.raises(ValidationError):
        snapshot.contextdev_calls = 99  # type: ignore[misc]

    assert active.remaining().contextdev_calls == 2
    assert snapshot is not active.remaining()


def test_budget_has_no_mutation_methods() -> None:
    issued = budget()

    assert not hasattr(issued, "consume")
    assert not hasattr(issued, "increase")
    assert not hasattr(issued, "extend")


@pytest.mark.asyncio
async def test_consumed_budget_cannot_be_replenished() -> None:
    active = state()

    await active.consume_tokens(100, 50)
    with pytest.raises(InvestigationBudgetExceeded):
        await active.consume_tokens(1, 0)

    assert active.remaining().input_tokens == 0
    assert active.budget.input_tokens == 100
