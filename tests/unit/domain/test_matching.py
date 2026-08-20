from __future__ import annotations

import inspect
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from settlediff.domain.matching import (
    MatchConfidence,
    MatchStatus,
    MatchStrategy,
    match_activity,
)
from settlediff.domain.models import ExecutionRecord, LedgerRecord, LedgerStatus, SettlementStatus
from settlediff.domain.money import Money

NOW = datetime(2026, 8, 12, 12, tzinfo=UTC)


def execution(**overrides: object) -> ExecutionRecord:
    values: dict[str, object] = {
        "vendor_slug": "synthetic-search",
        "upstream_http_status": 200,
        "charge": Money(amount=Decimal("0.01"), unit="USDC"),
        "asset": "USDC",
        "protocol": "mpp",
        "chain": "tempo",
        "recipient": "syn_recipient",
        "settlement_status": SettlementStatus.SETTLED,
        "transaction_id": "syn_tx_001",
        "session_id": "syn_session_001",
        "transaction_hash": "syn_hash_001",
        "response_body": None,
        "executed_at": NOW,
    }
    return ExecutionRecord(**(values | overrides))  # type: ignore[arg-type]


def record(ledger_id: str, **overrides: object) -> LedgerRecord:
    values: dict[str, object] = {
        "ledger_id": ledger_id,
        "vendor_slug": "synthetic-search",
        "amount": Money(amount=Decimal("0.01"), unit="USDC"),
        "asset": "USDC",
        "protocol": "mpp",
        "chain": "tempo",
        "recipient": "syn_recipient",
        "status": LedgerStatus.CONFIRMED,
        "error_reason": None,
        "transaction_id": "syn_tx_001",
        "session_id": "syn_session_001",
        "transaction_hash": "syn_hash_001",
        "occurred_at": NOW,
    }
    return LedgerRecord(**(values | overrides))  # type: ignore[arg-type]


def test_exact_transaction_id_is_the_high_confidence_match() -> None:
    result = match_activity(
        execution(),
        (record("syn_ledger_other", transaction_id="syn_tx_other"), record("syn_ledger_match")),
    )

    assert result.status is MatchStatus.MATCHED
    assert result.strategy is MatchStrategy.TRANSACTION_ID
    assert result.confidence is MatchConfidence.HIGH
    assert result.matched_id == "syn_ledger_match"
    assert result.candidate_ids == ("syn_ledger_match",)


def test_session_and_vendor_match_when_transaction_id_is_unavailable() -> None:
    result = match_activity(
        execution(transaction_id=None),
        (
            record("syn_ledger_wrong_vendor", vendor_slug="other"),
            record("syn_ledger_match", transaction_id=None),
        ),
    )

    assert result.status is MatchStatus.MATCHED
    assert result.strategy is MatchStrategy.SESSION_VENDOR
    assert result.confidence is MatchConfidence.HIGH
    assert result.matched_id == "syn_ledger_match"


def test_transaction_hash_matches_after_session_vendor_is_unavailable() -> None:
    result = match_activity(
        execution(transaction_id=None, session_id=None),
        (record("syn_ledger_match", transaction_id=None, session_id=None),),
    )

    assert result.status is MatchStatus.MATCHED
    assert result.strategy is MatchStrategy.TRANSACTION_HASH
    assert result.confidence is MatchConfidence.HIGH
    assert result.matched_id == "syn_ledger_match"


def test_unique_vendor_amount_time_candidate_uses_bounded_low_confidence_fallback() -> None:
    result = match_activity(
        execution(transaction_id=None, session_id=None, transaction_hash=None),
        (
            record(
                "syn_ledger_match",
                transaction_id=None,
                session_id=None,
                transaction_hash=None,
                occurred_at=NOW + timedelta(seconds=15),
            ),
        ),
        window=timedelta(minutes=1),
    )

    assert result.status is MatchStatus.MATCHED
    assert result.strategy is MatchStrategy.VENDOR_AMOUNT_TIME
    assert result.confidence is MatchConfidence.LOW
    assert result.matched_id == "syn_ledger_match"


def test_missing_execution_time_disables_low_confidence_fallback() -> None:
    result = match_activity(
        execution(
            transaction_id=None,
            session_id=None,
            transaction_hash=None,
            executed_at=None,
        ),
        (record("syn_ledger_candidate"),),
    )

    assert result.status is MatchStatus.MISSING
    assert result.strategy is MatchStrategy.NONE


def test_equal_fallback_candidates_are_ambiguous() -> None:
    result = match_activity(
        execution(transaction_id=None, session_id=None, transaction_hash=None),
        (
            record(
                "syn_ledger_b",
                transaction_id=None,
                session_id=None,
                transaction_hash=None,
                occurred_at=NOW + timedelta(seconds=10),
            ),
            record(
                "syn_ledger_a",
                transaction_id=None,
                session_id=None,
                transaction_hash=None,
                occurred_at=NOW - timedelta(seconds=10),
            ),
        ),
    )

    assert result.status is MatchStatus.AMBIGUOUS
    assert result.strategy is MatchStrategy.VENDOR_AMOUNT_TIME
    assert result.confidence is MatchConfidence.NONE
    assert result.matched_id is None
    assert result.candidate_ids == ("syn_ledger_a", "syn_ledger_b")


def test_duplicate_transaction_id_is_ambiguous_without_falling_back() -> None:
    result = match_activity(
        execution(),
        (record("syn_ledger_b"), record("syn_ledger_a")),
    )

    assert result.status is MatchStatus.AMBIGUOUS
    assert result.strategy is MatchStrategy.TRANSACTION_ID
    assert result.confidence is MatchConfidence.NONE
    assert result.candidate_ids == ("syn_ledger_a", "syn_ledger_b")


def test_exact_transaction_id_has_precedence_over_conflicting_lower_priority_identifiers() -> None:
    result = match_activity(
        execution(),
        (
            record("syn_ledger_session", transaction_id="syn_tx_other"),
            record(
                "syn_ledger_tx", session_id="syn_session_other", transaction_hash="syn_hash_other"
            ),
        ),
    )

    assert result.status is MatchStatus.MATCHED
    assert result.strategy is MatchStrategy.TRANSACTION_ID
    assert result.matched_id == "syn_ledger_tx"


def test_out_of_window_vendor_amount_candidate_is_missing() -> None:
    result = match_activity(
        execution(transaction_id=None, session_id=None, transaction_hash=None),
        (
            record(
                "syn_ledger_late",
                transaction_id=None,
                session_id=None,
                transaction_hash=None,
                occurred_at=NOW + timedelta(minutes=2),
            ),
        ),
        window=timedelta(minutes=1),
    )

    assert result.status is MatchStatus.MISSING
    assert result.strategy is MatchStrategy.NONE
    assert result.confidence is MatchConfidence.NONE
    assert result.candidate_ids == ()


def test_matcher_accepts_no_caller_confidence_parameter() -> None:
    assert "confidence" not in inspect.signature(match_activity).parameters

    with pytest.raises(TypeError):
        match_activity(execution(), (), confidence="high")  # type: ignore[call-arg]
