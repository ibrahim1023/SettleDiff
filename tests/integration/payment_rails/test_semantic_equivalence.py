from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest
from pydantic import ValidationError

from settlediff.application.replay import replay_fixture
from settlediff.domain.checks import run_checks
from settlediff.domain.matching import match_activity
from settlediff.domain.models import (
    ExecutionRecord,
    ExpectedContract,
    LedgerStatus,
    MachineReport,
    PaymentReceipt,
    SettlementStatus,
    Verdict,
)
from settlediff.domain.verdict import derive_verdict

FIXTURES = Path(__file__).parents[3] / "fixtures"
FINANCIAL_CHECKS = (
    "budget",
    "price",
    "asset",
    "recipient",
    "settlement",
    "service_execution",
    "paid_failure",
    "ledger_outcome",
    "activity_persistence",
)


@dataclass(frozen=True)
class SemanticOutcome:
    verdict: Verdict
    budget_unit: str
    quoted_price_unit: str | None
    actual_charge_unit: str | None
    charge_present: bool
    settlement_status: SettlementStatus
    ledger_status: LedgerStatus | None
    findings: tuple[tuple[str, str], ...]


def semantics(report: MachineReport) -> SemanticOutcome:
    contract = report.contract
    execution = report.execution
    assert contract is not None
    assert execution is not None
    statuses = {finding.check_id: finding.status.value for finding in report.findings}
    return SemanticOutcome(
        verdict=report.verdict,
        budget_unit=report.intent.max_budget.unit,
        quoted_price_unit=contract.price.unit if contract.price is not None else None,
        actual_charge_unit=execution.charge.unit if execution.charge is not None else None,
        charge_present=execution.charge is not None,
        settlement_status=execution.settlement_status,
        ledger_status=report.ledger.status if report.ledger is not None else None,
        findings=tuple((check_id, statuses[check_id]) for check_id in FINANCIAL_CHECKS),
    )


@pytest.mark.parametrize(
    ("perflo_fixture", "x402_fixture", "verdict"),
    [
        ("clean-success", "x402-clean-success", Verdict.VERIFIED),
        ("paid-failure", "x402-paid-failure", Verdict.PAID_FAILURE),
    ],
)
def test_equivalent_economic_outcomes_have_rail_neutral_semantics(
    perflo_fixture: str, x402_fixture: str, verdict: Verdict
) -> None:
    perflo = replay_fixture(FIXTURES / perflo_fixture)
    x402 = replay_fixture(FIXTURES / x402_fixture)

    assert perflo.execution is not None
    assert x402.execution is not None
    assert perflo.execution.transaction_hash != x402.execution.transaction_hash
    assert semantics(perflo) == semantics(x402)
    assert perflo.verdict is x402.verdict is verdict


def test_equivalent_insufficient_settlement_evidence_is_unverifiable_on_both_rails() -> None:
    x402 = replay_fixture(FIXTURES / "x402-uncertain-submission")
    x402_contract = x402.contract
    x402_execution = x402.execution
    assert x402_contract is not None
    assert x402_execution is not None
    perflo_contract = ExpectedContract(
        schema_version=2,
        vendor_slug=None,
        url=x402_contract.url,
        price=x402_contract.price,
        asset="USDC",
        protocol="mpp",
        chain="tempo",
        request_schema={},
        recipient=x402_execution.recipient,
    )
    perflo_execution = ExecutionRecord(
        schema_version=1,
        vendor_slug=None,
        upstream_http_status=None,
        charge=None,
        asset="USDC",
        protocol="mpp",
        chain="tempo",
        recipient=x402_execution.recipient,
        settlement_status=SettlementStatus.UNKNOWN,
        transaction_id=None,
        session_id=None,
        transaction_hash="syn_perflo_uncertain",
        response_body=x402_execution.response_body,
        executed_at=None,
    )
    match = match_activity(perflo_execution, ())
    findings = run_checks(x402.intent, perflo_contract, perflo_execution, match)
    perflo = MachineReport(
        run_id=x402.run_id,
        intent=x402.intent,
        contract=perflo_contract,
        execution=perflo_execution,
        ledger=None,
        findings=findings,
        verdict=derive_verdict(findings),
    )

    assert semantics(perflo) == semantics(x402)
    assert perflo.verdict is x402.verdict is Verdict.UNVERIFIABLE


def test_provider_assertion_without_independent_evidence_cannot_improve_verdict() -> None:
    uncertain = replay_fixture(FIXTURES / "x402-uncertain-submission")
    settled = replay_fixture(FIXTURES / "x402-clean-success")
    contract = uncertain.contract
    execution = uncertain.execution
    receipt = settled.receipt
    assert contract is not None
    assert execution is not None
    assert receipt is not None
    match = match_activity(execution, ())
    findings = run_checks(uncertain.intent, contract, execution, match, receipt=receipt)

    assert {finding.check_id: finding.status.value for finding in findings}[
        "settlement"
    ] == "UNKNOWN"
    assert derive_verdict(findings) is Verdict.UNVERIFIABLE


def test_malformed_provider_evidence_is_rejected_before_financial_checks() -> None:
    receipt = replay_fixture(FIXTURES / "x402-clean-success").receipt
    assert receipt is not None

    with pytest.raises(ValidationError):
        PaymentReceipt.model_validate(
            {
                **receipt.model_dump(),
                "settlement_status": "settled",
                "amount": {"amount": "NaN", "unit": "USDC"},
            }
        )
