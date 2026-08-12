from __future__ import annotations

from decimal import Decimal

import pytest
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
    assert normalized.unit == "USDC"
    assert normalized == canonical
    assert hash(normalized) == hash(canonical)


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
