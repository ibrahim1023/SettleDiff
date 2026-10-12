"""Strict, bounded envelopes for the supported seller JWS extension profile."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue


class _Receipt(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    format: Literal["jws"]
    signature: str = Field(min_length=1, max_length=8192)


class _Offer(_Receipt):
    acceptIndex: int | None = Field(default=None, ge=0, le=15)


class _Info(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    offers: tuple[_Offer, ...] | None = Field(default=None, max_length=16)
    receipt: _Receipt | None = None


class _Extension(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    info: _Info
    schema_: dict[str, JsonValue] | None = Field(default=None, alias="schema")


def _extension(extensions: Mapping[str, object]) -> _Extension | None:
    if "offer-receipt" not in extensions:
        return None
    return _Extension.model_validate_json(json.dumps(extensions["offer-receipt"]), strict=True)


def extract_offer(extensions: Mapping[str, object]) -> str | None:
    extension = _extension(extensions)
    if extension is None:
        return None
    offers = extension.info.offers or ()
    selected = tuple(offer for offer in offers if offer.acceptIndex in (None, 0))
    if len(selected) != 1:
        raise ValueError("signed offer for the selected requirement is absent or ambiguous")
    return selected[0].signature


def extract_receipt(extensions: Mapping[str, object]) -> str | None:
    extension = _extension(extensions)
    if extension is None or extension.info.receipt is None:
        return None
    return extension.info.receipt.signature
