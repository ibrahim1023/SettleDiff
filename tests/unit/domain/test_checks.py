from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from settlediff.domain.checks import run_checks
from settlediff.domain.matching import MatchConfidence, MatchResult, MatchStatus, MatchStrategy
from settlediff.domain.models import (
    ExecutionRecord,
    ExpectedContract,
    LedgerRecord,
    LedgerStatus,
    PurchaseIntent,
    SettlementStatus,
)
from settlediff.domain.money import Money

NOW = datetime(2026, 8, 12, 12, tzinfo=UTC)


def test_paid_failure_is_independent_of_confirmed_activity() -> None:
    intent = PurchaseIntent(
        run_id="syn_run",
        task="synthetic",
        max_budget=Money(amount=Decimal("0.05"), unit="USDC"),
        requested_service=None,
        created_at=NOW,
    )
    contract = ExpectedContract(
        vendor_slug="synthetic",
        url="https://example.invalid",
        price=Money(amount=Decimal("0.01"), unit="USDC"),
        asset="USDC",
        protocol="mpp",
        chain="base",
        request_schema={},
    )
    execution = ExecutionRecord(
        vendor_slug="synthetic",
        upstream_http_status=400,
        charge=Money(amount=Decimal("0.01"), unit="USDC"),
        asset="USDC",
        protocol="mpp",
        chain="tempo",
        recipient="syn_execution",
        settlement_status=SettlementStatus.SETTLED,
        transaction_id="syn_tx",
        session_id=None,
        transaction_hash=None,
        response_body=None,
        executed_at=NOW,
    )
    ledger = LedgerRecord(
        ledger_id="syn_ledger",
        vendor_slug="synthetic",
        amount=Money(amount=Decimal("0.01"), unit="USDC"),
        asset="USDC",
        protocol="mpp",
        chain="tempo",
        recipient="syn_ledger_recipient",
        status=LedgerStatus.CONFIRMED,
        error_reason=None,
        transaction_id="syn_tx",
        session_id=None,
        transaction_hash=None,
        occurred_at=NOW,
    )
    match = MatchResult(
        MatchStatus.MATCHED,
        MatchStrategy.TRANSACTION_ID,
        MatchConfidence.HIGH,
        ledger,
        (ledger.ledger_id,),
    )

    findings = {
        finding.check_id: finding for finding in run_checks(intent, contract, execution, match)
    }

    assert findings["paid_failure"].status.value == "FAIL"
    assert findings["chain"].status.value == "DIFF"
    assert findings["recipient"].status.value == "WARN"
    assert findings["ledger_outcome"].status.value == "WARN"


def test_matched_activity_amount_verifies_missing_execution_charge() -> None:
    amount = Money(amount=Decimal("0.02"), unit="USDC")
    quoted_price = Money(amount=Decimal("0.01"), unit="USDC")
    intent = PurchaseIntent(
        run_id="syn_run",
        task="synthetic",
        max_budget=amount,
        requested_service=None,
        created_at=NOW,
    )
    contract = ExpectedContract(
        vendor_slug=None,
        url="https://example.invalid",
        price=quoted_price,
        asset="USDC",
        protocol="mpp",
        chain="tempo",
        request_schema={},
    )
    execution = ExecutionRecord(
        vendor_slug=None,
        upstream_http_status=200,
        charge=None,
        asset="USDC",
        protocol="mpp",
        chain="tempo",
        recipient="syn_recipient",
        settlement_status=SettlementStatus.SETTLED,
        transaction_id=None,
        session_id=None,
        transaction_hash="syn_hash",
        response_body=None,
        executed_at=None,
    )
    ledger = LedgerRecord(
        ledger_id="syn_ledger",
        vendor_slug=None,
        amount=amount,
        asset="USDC",
        protocol="mpp",
        chain="tempo",
        recipient="syn_recipient",
        status=LedgerStatus.CONFIRMED,
        error_reason=None,
        transaction_id=None,
        session_id=None,
        transaction_hash="syn_hash",
        occurred_at=NOW,
    )
    match = MatchResult(
        MatchStatus.MATCHED,
        MatchStrategy.TRANSACTION_HASH,
        MatchConfidence.HIGH,
        ledger,
        (ledger.ledger_id,),
    )

    findings = {
        finding.check_id: finding for finding in run_checks(intent, contract, execution, match)
    }

    assert findings["budget"].status.value == "PASS"
    assert findings["budget"].observed == amount
    assert findings["budget"].artifact_ids == ("syn_run:intent", "activity")
    assert findings["budget"].field_paths == ("intent.max_budget", "activity.amount")
    assert findings["price"].status.value == "DIFF"
    assert findings["price"].observed == amount
    assert findings["price"].artifact_ids == ("contract", "activity")
    assert findings["price"].field_paths == ("contract.price", "activity.amount")

    low_confidence = MatchResult(
        MatchStatus.MATCHED,
        MatchStrategy.VENDOR_AMOUNT_TIME,
        MatchConfidence.LOW,
        ledger,
        (ledger.ledger_id,),
    )
    low_confidence_findings = {
        finding.check_id: finding
        for finding in run_checks(intent, contract, execution, low_confidence)
    }

    assert low_confidence_findings["budget"].status.value == "UNKNOWN"
    assert low_confidence_findings["price"].status.value == "UNKNOWN"

    pending_ledger = ledger.model_copy(update={"status": LedgerStatus.PENDING})
    pending_match = MatchResult(
        MatchStatus.MATCHED,
        MatchStrategy.TRANSACTION_HASH,
        MatchConfidence.HIGH,
        pending_ledger,
        (pending_ledger.ledger_id,),
    )
    pending_findings = {
        finding.check_id: finding
        for finding in run_checks(intent, contract, execution, pending_match)
    }

    assert pending_findings["budget"].status.value == "UNKNOWN"
    assert pending_findings["price"].status.value == "UNKNOWN"


def test_missing_evidence_is_unknown_not_a_pass() -> None:
    intent = PurchaseIntent(
        run_id="syn_run",
        task="synthetic",
        max_budget=Money(amount=Decimal("0.05"), unit="USDC"),
        requested_service=None,
        created_at=NOW,
    )
    missing = MatchResult(MatchStatus.MISSING, MatchStrategy.NONE, MatchConfidence.NONE, None, ())

    findings = run_checks(intent, None, None, missing)

    assert all(finding.status.value == "UNKNOWN" for finding in findings[:-1])
    assert findings[-1].check_id == "activity_persistence"
    assert findings[-1].status.value == "WARN"
