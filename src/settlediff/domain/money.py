"""Exact monetary values without implicit conversion."""

from __future__ import annotations

import re
from decimal import Decimal
from typing import Self, cast

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

DECIMAL_TEXT = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$")


class UnitMismatchError(ValueError):
    """Raised when values with different units are compared."""


class Money(BaseModel):
    """An exact amount in one currency or asset.

    When ``minor_units`` is supplied, ``amount`` is interpreted as an integer minor-unit
    quantity and normalized by that decimal exponent. For example, ``10000`` with six minor
    units represents ``0.01``. The exponent is consumed during validation so serialized values
    are always canonical and safe to validate again.
    """

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    amount: Decimal
    unit: str
    minor_units: int | None = Field(default=None, ge=0, le=30)

    @model_validator(mode="before")
    @classmethod
    def normalize_minor_amount(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value

        data = cast(dict[str, object], value)
        raw_amount = data.get("amount")
        if isinstance(raw_amount, str) and DECIMAL_TEXT.fullmatch(raw_amount):
            raw_amount = Decimal(raw_amount)
            data = {**data, "amount": raw_amount}

        minor_units = data.get("minor_units")
        if (
            not isinstance(raw_amount, Decimal)
            or isinstance(minor_units, bool)
            or not isinstance(minor_units, int)
            or not 0 <= minor_units <= 30
        ):
            return data
        if raw_amount != raw_amount.to_integral_value():
            raise ValueError("a minor-unit amount must be integral")

        normalized = dict(data)
        normalized["amount"] = raw_amount.scaleb(-minor_units)
        normalized["minor_units"] = None
        return normalized

    @field_validator("amount")
    @classmethod
    def require_finite_amount(cls, value: Decimal) -> Decimal:
        if not value.is_finite():
            raise ValueError("money amount must be finite")
        return value

    @field_validator("unit")
    @classmethod
    def normalize_unit(cls, value: str) -> str:
        normalized = value.strip().upper()
        if not normalized:
            raise ValueError("money unit must not be empty")
        return normalized

    def is_within(self, limit: Self) -> bool:
        """Return whether this amount is at or below a limit of the same unit."""
        self._require_same_unit(limit)
        return self.amount <= limit.amount

    def _require_same_unit(self, other: Self) -> None:
        if self.unit != other.unit:
            raise UnitMismatchError(f"cannot compare {self.unit} with {other.unit}")

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Money):
            return NotImplemented
        return self.unit == other.unit and self.amount == other.amount

    def __hash__(self) -> int:
        return hash((self.amount, self.unit))
