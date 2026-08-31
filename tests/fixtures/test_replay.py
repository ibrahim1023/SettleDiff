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
