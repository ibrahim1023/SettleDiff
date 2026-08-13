from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from settlediff.application.auth import (
    AuthorizationError,
    PaidExecutionCapability,
    PaidExecutionRequest,
)
from settlediff.domain.money import Money

NOW = datetime(2026, 8, 13, 10, tzinfo=UTC)


def request(**overrides: object) -> PaidExecutionRequest:
    values: dict[str, object] = {
        "run_id": "syn_run_001",
        "target": "https://example.invalid/search",
        "body": {"query": "synthetic"},
        "budget": Money(amount=Decimal("0.05"), unit="USDC"),
    }
    return PaidExecutionRequest(**(values | overrides))  # type: ignore[arg-type]


def capability() -> PaidExecutionCapability:
    return PaidExecutionCapability.issue(request(), expires_at=NOW + timedelta(minutes=5))


@pytest.mark.asyncio
async def test_exact_authorization_consumes_once() -> None:
    authorized = capability()

    token = await authorized.consume(request(), now=NOW)

    assert token.run_id == "syn_run_001"
    assert token.target == "https://example.invalid/search"
    with pytest.raises(AuthorizationError, match="already consumed"):
        await authorized.consume(request(), now=NOW)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"run_id": "syn_run_other"}, "run"),
        ({"target": "https://example.invalid/other"}, "target"),
        ({"body": {"query": "changed"}}, "body"),
        ({"budget": Money(amount=Decimal("0.06"), unit="USDC")}, "budget"),
        ({"budget": Money(amount=Decimal("0.04"), unit="USDC")}, "budget"),
        ({"budget": Money(amount=Decimal("0.05"), unit="USD")}, "budget"),
    ],
)
async def test_mismatch_fails_without_consuming(override: dict[str, object], message: str) -> None:
    authorized = capability()

    with pytest.raises(AuthorizationError, match=message):
        await authorized.consume(request(**override), now=NOW)

    assert (await authorized.consume(request(), now=NOW)).run_id == "syn_run_001"


@pytest.mark.asyncio
async def test_expired_authorization_fails_closed() -> None:
    authorized = capability()

    with pytest.raises(AuthorizationError, match="expired"):
        await authorized.consume(request(), now=NOW + timedelta(minutes=6))


def test_canonical_body_digest_ignores_object_key_order() -> None:
    first = request(body={"query": "synthetic", "limit": 3})
    reordered = request(body={"limit": 3, "query": "synthetic"})
    authorized = PaidExecutionCapability.issue(first, expires_at=NOW + timedelta(minutes=5))

    assert authorized.body_digest == PaidExecutionCapability.body_digest_for(reordered.body)


@pytest.mark.asyncio
async def test_consumed_authorization_rejects_changed_request() -> None:
    exact_request = request()
    token = await capability().consume(exact_request, now=NOW)

    with pytest.raises(AuthorizationError, match="exact budget"):
        token.require_exact_request(
            exact_request.model_copy(update={"budget": Money(amount=Decimal("0.04"), unit="USDC")})
        )
