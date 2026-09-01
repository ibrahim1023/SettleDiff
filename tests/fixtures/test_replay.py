from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from settlediff.application.replay import replay_fixture
from settlediff.domain.models import Verdict

FIXTURES = Path(__file__).parents[2] / "fixtures"


@pytest.mark.parametrize(
    ("scenario", "verdict", "expected_statuses"),
    [
        ("clean-success", Verdict.VERIFIED, {"chain": "PASS", "activity_persistence": "PASS"}),
        ("chain-diff", Verdict.VERIFIED_WITH_WARNINGS, {"chain": "DIFF"}),
        ("paid-failure", Verdict.PAID_FAILURE, {"paid_failure": "FAIL"}),
        (
            "failed-broadcast",
            Verdict.UNVERIFIABLE,
            {
                "chain": "DIFF",
                "service_execution": "FAIL",
                "budget": "UNKNOWN",
                "price": "UNKNOWN",
                "activity_persistence": "PASS",
            },
        ),
        ("recipient-diff", Verdict.VERIFIED_WITH_WARNINGS, {"recipient": "WARN"}),
        ("missing-activity", Verdict.UNVERIFIABLE, {"activity_persistence": "WARN"}),
        ("ambiguous-activity", Verdict.UNVERIFIABLE, {"activity_persistence": "WARN"}),
        (
            "x402-clean-success",
            Verdict.VERIFIED,
            {"network": "PASS", "recipient": "PASS", "activity_persistence": "PASS"},
        ),
        ("x402-paid-failure", Verdict.PAID_FAILURE, {"paid_failure": "FAIL"}),
        (
            "x402-uncertain-submission",
            Verdict.UNVERIFIABLE,
            {"service_execution": "UNKNOWN", "activity_persistence": "WARN"},
        ),
        (
            "x402-provider-success-independent-failure",
            Verdict.UNVERIFIABLE,
            {"settlement": "UNKNOWN"},
        ),
        (
            "x402-provider-failure-independent-confirmation",
            Verdict.UNVERIFIABLE,
            {"settlement": "UNKNOWN"},
        ),
        ("x402-wrong-recipient", Verdict.VERIFIED_WITH_WARNINGS, {"recipient": "WARN"}),
        ("x402-wrong-amount", Verdict.VERIFIED_WITH_WARNINGS, {"price": "DIFF"}),
        ("x402-wrong-asset", Verdict.VERIFIED_WITH_WARNINGS, {"asset": "DIFF"}),
        ("x402-wrong-network", Verdict.VERIFIED_WITH_WARNINGS, {"network": "DIFF"}),
    ],
)
def test_replay_returns_expected_deterministic_report(
    scenario: str, verdict: Verdict, expected_statuses: dict[str, str]
) -> None:
    report = replay_fixture(FIXTURES / scenario)
    statuses = {finding.check_id: finding.status.value for finding in report.findings}

    assert report.verdict is verdict
    assert all(finding.finding_id.startswith("check:") for finding in report.findings)
    assert statuses.items() >= expected_statuses.items()


@pytest.mark.parametrize(
    "scenario",
    [
        "x402-clean-success",
        "x402-paid-failure",
        "x402-uncertain-submission",
        "x402-provider-success-independent-failure",
        "x402-provider-failure-independent-confirmation",
        "x402-wrong-recipient",
        "x402-wrong-amount",
        "x402-wrong-asset",
        "x402-wrong-network",
    ],
)
def test_x402_corpus_uses_complete_v2_canonical_reports(scenario: str) -> None:
    report = replay_fixture(FIXTURES / scenario)

    contract = report.contract
    execution = report.execution
    assert contract is not None
    assert execution is not None
    assert contract.schema_version == 2
    assert contract.protocol == "x402"
    assert contract.scheme == "exact"
    assert contract.network == "eip155:84532"
    assert execution.schema_version == 2
    assert execution.protocol == "x402"


@pytest.mark.parametrize(
    ("first", "second"),
    [("clean-success", "x402-clean-success"), ("paid-failure", "x402-paid-failure")],
)
def test_canonical_outcomes_do_not_depend_on_payment_rail(first: str, second: str) -> None:
    first_report = replay_fixture(FIXTURES / first)
    second_report = replay_fixture(FIXTURES / second)
    selected_checks = {
        "budget",
        "price",
        "asset",
        "settlement",
        "service_execution",
        "paid_failure",
        "ledger_outcome",
        "activity_persistence",
    }

    assert first_report.verdict is second_report.verdict
    assert {
        finding.check_id: finding.status
        for finding in first_report.findings
        if finding.check_id in selected_checks
    } == {
        finding.check_id: finding.status
        for finding in second_report.findings
        if finding.check_id in selected_checks
    }


@pytest.mark.parametrize(
    ("scenario", "provider_status", "independent_status"),
    [
        ("x402-clean-success", "settled", "confirmed"),
        ("x402-provider-success-independent-failure", "settled", "failed"),
        ("x402-provider-failure-independent-confirmation", "failed", "confirmed"),
    ],
)
def test_x402_provider_and_independent_settlement_remain_separate(
    scenario: str, provider_status: str, independent_status: str
) -> None:
    report = replay_fixture(FIXTURES / scenario)

    assert report.receipt is not None
    assert report.receipt.settlement_status.value == provider_status
    assert report.ledger is not None
    assert report.ledger.status.value == independent_status


def test_x402_clean_success_allows_provider_receipt_without_amount() -> None:
    report = replay_fixture(FIXTURES / "x402-clean-success")

    assert report.verdict is Verdict.VERIFIED
    assert report.receipt is not None
    assert report.receipt.amount is None
    assert report.receipt.asset_identity is None
    assert report.ledger is not None
    assert report.ledger.amount is not None
    assert str(report.ledger.amount.amount) == "0.001"


def test_replay_includes_provider_receipt_when_declared(
    tmp_path: Path,
) -> None:
    scenario = tmp_path / "receipt-success"
    shutil.copytree(FIXTURES / "clean-success", scenario)
    manifest = json.loads((scenario / "manifest.json").read_text())
    manifest["scenario"] = "receipt-success"
    manifest["artifacts"].append("receipt.json")
    (scenario / "manifest.json").write_text(json.dumps(manifest))
    execution = json.loads((scenario / "execution.json").read_text())
    (scenario / "receipt.json").write_text(
        json.dumps(
            {
                "amount": execution["charge"],
                "asset": execution["asset"],
                "protocol": execution["protocol"],
                "chain": execution["chain"],
                "recipient": execution["recipient"],
                "settlement_status": "settled",
                "transaction_id": execution["transaction_id"],
                "session_id": execution["session_id"],
                "transaction_hash": execution["transaction_hash"],
                "issued_at": execution["executed_at"],
            }
        )
    )

    report = replay_fixture(scenario)

    assert report.receipt is not None
    assert report.receipt.settlement_status.value == "settled"
    assert report.verdict is Verdict.VERIFIED
