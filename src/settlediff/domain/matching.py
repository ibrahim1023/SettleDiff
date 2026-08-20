"""Deterministic matching of an execution to persisted Activity records."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum

from settlediff.domain.models import ExecutionRecord, LedgerRecord


class MatchStatus(StrEnum):
    MATCHED = "matched"
    MISSING = "missing"
    AMBIGUOUS = "ambiguous"


class MatchStrategy(StrEnum):
    TRANSACTION_ID = "transaction_id"
    SESSION_VENDOR = "session_vendor"
    TRANSACTION_HASH = "transaction_hash"
    VENDOR_AMOUNT_TIME = "vendor_amount_time"
    NONE = "none"


class MatchConfidence(StrEnum):
    HIGH = "high"
    LOW = "low"
    NONE = "none"


@dataclass(frozen=True, slots=True)
class MatchResult:
    """The deterministic outcome of one ordered Activity matching attempt."""

    status: MatchStatus
    strategy: MatchStrategy
    confidence: MatchConfidence
    matched: LedgerRecord | None
    candidate_ids: tuple[str, ...]

    @property
    def matched_id(self) -> str | None:
        """Expose the stable matched record identifier without duplicating the record."""
        return self.matched.ledger_id if self.matched is not None else None


def match_activity(
    execution: ExecutionRecord | None,
    candidates: tuple[LedgerRecord, ...],
    *,
    window: timedelta = timedelta(minutes=5),
) -> MatchResult:
    """Apply documented matching strategies in fixed priority order.

    The caller supplies evidence only. Confidence is derived from the selected strategy and cannot
    be promoted by an agent or API caller.
    """
    if window < timedelta(0):
        raise ValueError("Activity matching window must not be negative")
    if execution is None:
        return _missing_result()

    for strategy, matches in (
        (
            MatchStrategy.TRANSACTION_ID,
            _matching_transaction_id(execution, candidates),
        ),
        (
            MatchStrategy.SESSION_VENDOR,
            _matching_session_vendor(execution, candidates),
        ),
        (
            MatchStrategy.TRANSACTION_HASH,
            _matching_transaction_hash(execution, candidates),
        ),
    ):
        if matches:
            return _strong_result(strategy, matches)

    return _fallback_result(execution, candidates, window)


def _matching_transaction_id(
    execution: ExecutionRecord, candidates: tuple[LedgerRecord, ...]
) -> tuple[LedgerRecord, ...]:
    if execution.transaction_id is None:
        return ()
    return tuple(
        record for record in candidates if record.transaction_id == execution.transaction_id
    )


def _matching_session_vendor(
    execution: ExecutionRecord, candidates: tuple[LedgerRecord, ...]
) -> tuple[LedgerRecord, ...]:
    if execution.session_id is None or execution.vendor_slug is None:
        return ()
    return tuple(
        record
        for record in candidates
        if record.session_id == execution.session_id and record.vendor_slug == execution.vendor_slug
    )


def _matching_transaction_hash(
    execution: ExecutionRecord, candidates: tuple[LedgerRecord, ...]
) -> tuple[LedgerRecord, ...]:
    if execution.transaction_hash is None:
        return ()
    return tuple(
        record for record in candidates if record.transaction_hash == execution.transaction_hash
    )


def _strong_result(strategy: MatchStrategy, matches: tuple[LedgerRecord, ...]) -> MatchResult:
    ordered = _ordered(matches)
    if len(ordered) == 1:
        return MatchResult(
            status=MatchStatus.MATCHED,
            strategy=strategy,
            confidence=MatchConfidence.HIGH,
            matched=ordered[0],
            candidate_ids=(ordered[0].ledger_id,),
        )
    return MatchResult(
        status=MatchStatus.AMBIGUOUS,
        strategy=strategy,
        confidence=MatchConfidence.NONE,
        matched=None,
        candidate_ids=tuple(record.ledger_id for record in ordered),
    )


def _fallback_result(
    execution: ExecutionRecord,
    candidates: tuple[LedgerRecord, ...],
    window: timedelta,
) -> MatchResult:
    if execution.vendor_slug is None or execution.charge is None or execution.executed_at is None:
        return _missing_result()

    matching = tuple(
        record
        for record in candidates
        if record.vendor_slug == execution.vendor_slug
        and record.amount == execution.charge
        and abs(record.occurred_at - execution.executed_at) <= window
    )
    if not matching:
        return _missing_result()

    shortest_distance = min(abs(record.occurred_at - execution.executed_at) for record in matching)
    best = _ordered(
        tuple(
            record
            for record in matching
            if abs(record.occurred_at - execution.executed_at) == shortest_distance
        )
    )
    if len(best) == 1:
        return MatchResult(
            status=MatchStatus.MATCHED,
            strategy=MatchStrategy.VENDOR_AMOUNT_TIME,
            confidence=MatchConfidence.LOW,
            matched=best[0],
            candidate_ids=(best[0].ledger_id,),
        )
    return MatchResult(
        status=MatchStatus.AMBIGUOUS,
        strategy=MatchStrategy.VENDOR_AMOUNT_TIME,
        confidence=MatchConfidence.NONE,
        matched=None,
        candidate_ids=tuple(record.ledger_id for record in best),
    )


def _missing_result() -> MatchResult:
    return MatchResult(
        status=MatchStatus.MISSING,
        strategy=MatchStrategy.NONE,
        confidence=MatchConfidence.NONE,
        matched=None,
        candidate_ids=(),
    )


def _ordered(records: tuple[LedgerRecord, ...]) -> tuple[LedgerRecord, ...]:
    return tuple(sorted(records, key=lambda record: record.ledger_id))
