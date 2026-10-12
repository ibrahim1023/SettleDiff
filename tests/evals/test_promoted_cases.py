from __future__ import annotations

from pathlib import Path

import pytest

from tests.evals.dataset import CASES, EvalCase
from tests.evals.runner import run_case

PROMOTED = tuple(sorted((Path(__file__).parent / "promoted").glob("*/case.json")))


def test_promoted_case_ids_are_unique_and_do_not_shadow_builtin_cases() -> None:
    ids = [case.case_id for case in CASES]
    ids.extend(EvalCase.model_validate_json(path.read_text()).case_id for path in PROMOTED)
    assert len(ids) == len(set(ids))


@pytest.mark.asyncio
@pytest.mark.parametrize("path", PROMOTED, ids=lambda path: path.parent.name)
async def test_promoted_case_runs_in_the_offline_gate(path: Path) -> None:
    case = EvalCase.model_validate_json(path.read_text())
    result = await run_case(case, fixture_root=path.parent)
    assert result.passed, result.model_dump_json(indent=2)
