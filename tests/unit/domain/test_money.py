from __future__ import annotations

from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError

from settlediff.domain.money import Money, UnitMismatchError


def test_money_rejects_float() -> None:
    with pytest.raises(ValidationError):
        Money(amount=0.01, unit="USD")  # type: ignore[arg-type]


@pytest.mark.parametrize("amount", [Decimal("NaN"), Decimal("Infinity"), Decimal("-Infinity")])
def test_money_rejects_non_finite_amount(amount: Decimal) -> None:
    with pytest.raises(ValidationError):
        Money(amount=amount, unit="USD")


def test_money_normalizes_unit_and_minor_amount() -> None:
    normalized = Money(amount=Decimal("10000"), unit=" usdc ", minor_units=6)
    canonical = Money(amount=Decimal("0.01"), unit="USDC")

    assert normalized.amount == Decimal("0.010000")
    assert normalized.minor_units is None
    assert normalized.unit == "USDC"
    assert normalized == canonical
    assert hash(normalized) == hash(canonical)


def test_normalized_minor_amount_round_trips_through_canonical_dump() -> None:
    normalized = Money(amount=Decimal("10000"), unit="USDC", minor_units=6)

    assert Money.model_validate(normalized.model_dump()) == normalized


@given(raw_amount=st.integers(min_value=0, max_value=10**18), exponent=st.integers(0, 18))
def test_minor_amount_normalization_is_exact_and_idempotent(raw_amount: int, exponent: int) -> None:
    money = Money(amount=Decimal(raw_amount), unit="USDC", minor_units=exponent)

    assert money.amount == Decimal(raw_amount).scaleb(-exponent)
    assert Money.model_validate(money.model_dump()) == money


def test_minor_amount_must_be_integral() -> None:
    with pytest.raises(ValidationError):
        Money(amount=Decimal("1.5"), unit="USD", minor_units=2)


def test_money_is_within_equal_unit_limit() -> None:
    charge = Money(amount=Decimal("0.01"), unit="usd")
    limit = Money(amount=Decimal("0.05"), unit="USD")

    assert charge.is_within(limit)
    assert limit.is_within(limit)


def test_money_refuses_cross_unit_comparison() -> None:
    charge = Money(amount=Decimal("0.01"), unit="USD")
    limit = Money(amount=Decimal("0.05"), unit="USDC")

    with pytest.raises(UnitMismatchError, match="USD.*USDC"):
        charge.is_within(limit)


def test_money_is_frozen_and_rejects_unknown_fields() -> None:
    money = Money(amount=Decimal("1"), unit="USD")

    with pytest.raises(ValidationError):
        money.amount = Decimal("2")
    with pytest.raises(ValidationError):
        Money(amount=Decimal("1"), unit="USD", precision=2)  # type: ignore[call-arg]
