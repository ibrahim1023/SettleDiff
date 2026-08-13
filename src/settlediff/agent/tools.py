"""Small evidence-only tool surface for the investigation model."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict


class EvidenceSummary(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    artifact_id: str
    summary: str


@dataclass(frozen=True)
class InvestigationDependencies:
    inspect_contract: Callable[[], Awaitable[EvidenceSummary]]
    get_schema: Callable[[], Awaitable[EvidenceSummary]]
    get_activity: Callable[[], Awaitable[EvidenceSummary]]
