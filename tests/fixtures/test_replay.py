from __future__ import annotations

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
