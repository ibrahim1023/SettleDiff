from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from settlediff.application.auth import (
    AuthorizationError,
    PaidExecutionCapability,
    PaidExecutionRequest,
    PaymentTerms,
)
from settlediff.domain.models import AssetIdentity
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


def payment_terms(**overrides: object) -> PaymentTerms:
    values: dict[str, object] = {
        "adapter_id": "x402",
        "protocol_version": "2",
        "scheme": "exact",
        "network": "eip155:84532",
        "chain": None,
        "asset": AssetIdentity(
            symbol="USDC",
            network="eip155:84532",
            reference="syn_usdc_base_sepolia",
            decimals=6,
        ),
        "asset_symbol": "USDC",
        "recipient": "syn_recipient",
        "quoted_price": Money(amount=Decimal("0.001"), unit="USDC"),
        "max_timeout_seconds": 300,
        "resource_url": "https://example.invalid/search",
        "method": "POST",
        "body_digest": PaidExecutionCapability.body_digest_for({"query": "synthetic"}),
    }
    return PaymentTerms.model_validate(values | overrides)


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


@pytest.mark.parametrize(
    "updates",
    [
        {"resource_url": "https://example.invalid/other"},
        {"method": "GET"},
        {"body_digest": "b" * 64},
        {"quoted_price": Money(amount=Decimal("0.06"), unit="USDC")},
    ],
)
def test_capability_rejects_payment_terms_inconsistent_with_request(
    updates: dict[str, object],
) -> None:
    with pytest.raises(AuthorizationError, match="payment terms"):
        PaidExecutionCapability.issue(
            request(),
            payment_terms=PaymentTerms.model_validate({**payment_terms().model_dump(), **updates}),
            expires_at=NOW + timedelta(minutes=5),
        )


def test_payment_terms_digest_is_canonical_and_covers_all_selected_terms() -> None:
    first = payment_terms()
    same = PaymentTerms.model_validate_json(first.model_dump_json())

    assert first.digest == same.digest
    assert len(first.digest) == 64


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "updates",
    [
        {"adapter_id": "other"},
        {"protocol_version": "1"},
        {"scheme": "upto"},
        {
            "network": "eip155:8453",
            "asset": AssetIdentity(
                symbol="USDC",
                network="eip155:8453",
                reference="syn_usdc_base",
                decimals=6,
            ),
        },
        {"chain": "tempo"},
        {
            "asset": AssetIdentity(
                symbol="USDC",
                network="eip155:84532",
                reference="syn_other_usdc",
                decimals=6,
            )
        },
        {
            "asset": AssetIdentity(
                symbol="USDT",
                network="eip155:84532",
                reference="syn_usdt_base_sepolia",
                decimals=6,
            ),
            "asset_symbol": "USDT",
            "quoted_price": Money(amount=Decimal("0.001"), unit="USDT"),
        },
        {"recipient": "syn_other_recipient"},
        {"quoted_price": Money(amount=Decimal("0.002"), unit="USDC")},
        {"max_timeout_seconds": 301},
        {"resource_url": "https://example.invalid/other"},
        {"method": "GET"},
        {"body_digest": "b" * 64},
    ],
)
async def test_payment_terms_drift_fails_without_consuming(
    updates: dict[str, object],
) -> None:
    exact_request = request()
    exact_terms = payment_terms()
    authorized = PaidExecutionCapability.issue(
        exact_request,
        payment_terms=exact_terms,
        expires_at=NOW + timedelta(minutes=5),
    )

    with pytest.raises(AuthorizationError, match="payment terms"):
        await authorized.consume(
            exact_request,
            payment_terms=PaymentTerms.model_validate({**exact_terms.model_dump(), **updates}),
            now=NOW,
        )

    token = await authorized.consume(exact_request, payment_terms=exact_terms, now=NOW)
    token.require_exact_payment_terms(exact_terms)


@pytest.mark.asyncio
async def test_bound_terms_cannot_be_omitted_during_consumption() -> None:
    exact_request = request()
    authorized = PaidExecutionCapability.issue(
        exact_request,
        payment_terms=payment_terms(),
        expires_at=NOW + timedelta(minutes=5),
    )

    with pytest.raises(AuthorizationError, match="payment terms"):
        await authorized.consume(exact_request, now=NOW)


@pytest.mark.asyncio
async def test_consumed_authorization_rejects_changed_payment_terms() -> None:
    exact_request = request()
    exact_terms = payment_terms()
    token = await PaidExecutionCapability.issue(
        exact_request,
        payment_terms=exact_terms,
        expires_at=NOW + timedelta(minutes=5),
    ).consume(exact_request, payment_terms=exact_terms, now=NOW)

    with pytest.raises(AuthorizationError, match="payment terms"):
        token.require_exact_payment_terms(
            PaymentTerms.model_validate(
                {**exact_terms.model_dump(), "recipient": "syn_other_recipient"}
            )
        )


def test_get_request_with_absent_body_has_stable_digest() -> None:
    get_request = request(method="GET", body=None)

    assert PaidExecutionCapability.body_digest_for(
        get_request.body
    ) == PaidExecutionCapability.body_digest_for(None)
