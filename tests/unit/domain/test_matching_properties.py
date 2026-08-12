from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from hypothesis import given
from hypothesis import strategies as st

from settlediff.domain.matching import match_activity
from settlediff.domain.models import ExecutionRecord, LedgerRecord, LedgerStatus, SettlementStatus
from settlediff.domain.money import Money

NOW = datetime(2026, 8, 12, 12, tzinfo=UTC)


def execution() -> ExecutionRecord:
    return ExecutionRecord(
        vendor_slug="synthetic-search",
        upstream_http_status=200,
        charge=Money(amount=Decimal("0.01"), unit="USDC"),
        asset="USDC",
        protocol="mpp",
        chain="tempo",
        recipient=None,
        settlement_status=SettlementStatus.SETTLED,
        transaction_id=None,
        session_id=None,
        transaction_hash=None,
        response_body=None,
        executed_at=NOW,
    )


def record(ledger_id: str, timestamp_offset: int) -> LedgerRecord:
    return LedgerRecord(
        ledger_id=ledger_id,
        vendor_slug="synthetic-search",
        amount=Money(amount=Decimal("0.01"), unit="USDC"),
        asset="USDC",
        protocol="mpp",
        chain="tempo",
        recipient=None,
        status=LedgerStatus.CONFIRMED,
        error_reason=None,
        transaction_id=None,
        session_id=None,
        transaction_hash=None,
        occurred_at=NOW + timedelta(seconds=timestamp_offset),
    )


@given(st.permutations((record("syn_a", -10), record("syn_b", 0), record("syn_c", 10))))
def test_candidate_order_does_not_change_match_result(candidates: tuple[LedgerRecord, ...]) -> None:
    expected = match_activity(
        execution(), (record("syn_a", -10), record("syn_b", 0), record("syn_c", 10))
    )

    assert match_activity(execution(), candidates) == expected
