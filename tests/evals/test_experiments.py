from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest

from tests.evals.dataset import CASES
from tests.evals.experiments import compare_runs, run_experiment


@pytest.mark.asyncio
async def test_experiment_records_version_stimuli_and_results_without_payloads() -> None:
    run = await run_experiment(CASES[:1], revision="test-revision", dirty=True)

    assert run.revision == "test-revision"
    assert run.dirty
    assert run.model == "scripted-function-model"
    assert run.results[0].passed
    assert len(run.dataset_digest) == 64
    assert run.started_at.utcoffset() == timedelta(0)
    text = run.model_dump_json()
    assert "request_body" not in text
    assert "syn_recipient" not in text
    assert "example.invalid" not in text


@pytest.mark.asyncio
async def test_comparison_marks_changed_expectations_incomparable() -> None:
    from settlediff.domain.models import Verdict

    previous = await run_experiment(CASES[:1], revision="before", dirty=False)
    changed = CASES[0].model_copy(update={"expected_verdict": Verdict.UNVERIFIABLE})
    current = await run_experiment((changed,), revision="after", dirty=False)

    comparison = compare_runs(previous, current)
    assert comparison.regressions == ()
    assert comparison.changed_cases == ("success",)
    assert not comparison.comparable


def test_changed_case_definitions_never_receive_improvement_labels() -> None:
    from datetime import UTC, datetime

    from tests.evals.experiments import Experiment
    from tests.evals.runner import CaseResult

    result = CaseResult(
        case_id="success",
        graders={"outcome": False},
        requested_tools=(),
        executed_tools=(),
        model_requests=1,
        input_tokens=1,
        output_tokens=1,
        used_fallback=False,
        intercepted_payment_calls=0,
    )
    before = Experiment(
        started_at=datetime(2026, 10, 12, tzinfo=UTC),
        revision="before",
        dirty=False,
        source_digest="0" * 64,
        dataset_digest="1" * 64,
        case_digests={"success": "2" * 64},
        fixture_digests={"success": "3" * 64},
        results=(result,),
    )
    after = before.model_copy(
        update={
            "case_digests": {"success": "4" * 64},
            "results": (result.model_copy(update={"graders": {"outcome": True}}),),
        }
    )
    comparison = compare_runs(before, after)
    assert comparison.changed_cases == ("success",)
    assert comparison.improvements == ()
    unchanged = after.model_copy(update={"case_digests": before.case_digests})
    comparable = compare_runs(before, unchanged)
    assert comparable.comparable
    assert comparable.improvements == ("success",)
    assert compare_runs(unchanged, before).regressions == ("success",)


@pytest.mark.asyncio
async def test_runner_refuses_overwriting_baseline_even_through_a_path_alias(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import sys

    from scripts.run_evals import main

    baseline = tmp_path / "baseline.json"
    baseline.write_text("preserve this baseline")
    alias = tmp_path / "alias.json"
    alias.symlink_to(baseline)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_evals",
            "--baseline",
            str(baseline),
            "--out",
            str(alias),
        ],
    )
    with pytest.raises(ValueError, match="baseline"):
        await main()
    assert baseline.read_text() == "preserve this baseline"


@pytest.mark.asyncio
async def test_promoted_case_can_run_against_its_copied_fixture(tmp_path: Path) -> None:
    import shutil

    shutil.copytree(Path("fixtures/x402-independent-confirmed"), tmp_path / CASES[0].fixture)
    run = await run_experiment(
        CASES[:1], revision="test-revision", dirty=True, fixture_root=tmp_path
    )
    assert run.results[0].passed
