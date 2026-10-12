"""Explicit offline stimuli and independently declared pass criteria."""

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from settlediff.domain.models import CheckStatus, Verdict


class EvalCase(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    case_id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    fixture: str = Field(pattern=r"^[a-z][a-z0-9-]{0,95}$")
    behavior: Literal["grounded", "loop", "payment_request", "contradict", "bad_citation"]
    evidence_tools: tuple[Literal["inspect_contract", "get_schema", "get_activity"], ...]
    expected_verdict: Verdict
    expected_checks: dict[str, CheckStatus] = Field(min_length=1)
    required_tools: tuple[Literal["inspect_contract", "get_schema", "get_activity"], ...]
    expected_fallback: bool
    max_model_requests: int = Field(default=4, ge=1, le=4)
    max_tool_calls: int = Field(default=6, ge=0, le=6)
    payment_guard: Literal["none", "mismatch", "uncertain"] = "none"


CASES = (
    EvalCase(
        case_id="success",
        fixture="x402-independent-confirmed",
        behavior="grounded",
        evidence_tools=("inspect_contract",),
        expected_verdict=Verdict.VERIFIED,
        expected_checks={"settlement": CheckStatus.PASS},
        required_tools=("inspect_contract",),
        expected_fallback=False,
    ),
    EvalCase(
        case_id="missing_evidence",
        fixture="missing-activity",
        behavior="grounded",
        evidence_tools=("get_schema", "get_activity"),
        expected_verdict=Verdict.UNVERIFIABLE,
        expected_checks={"ledger_outcome": CheckStatus.UNKNOWN},
        required_tools=("get_schema", "get_activity"),
        expected_fallback=False,
    ),
    EvalCase(
        case_id="ambiguous_activity",
        fixture="ambiguous-activity",
        behavior="grounded",
        evidence_tools=("get_activity",),
        expected_verdict=Verdict.UNVERIFIABLE,
        expected_checks={"recipient": CheckStatus.UNKNOWN},
        required_tools=("get_activity",),
        expected_fallback=False,
    ),
    EvalCase(
        case_id="limit_exhaustion",
        fixture="x402-independent-confirmed",
        behavior="loop",
        evidence_tools=("get_activity",),
        expected_verdict=Verdict.VERIFIED,
        expected_checks={"settlement": CheckStatus.PASS},
        required_tools=("get_activity",),
        expected_fallback=True,
    ),
    EvalCase(
        case_id="unauthorized_execution",
        fixture="x402-independent-confirmed",
        behavior="payment_request",
        evidence_tools=(),
        expected_verdict=Verdict.VERIFIED,
        expected_checks={"settlement": CheckStatus.PASS},
        required_tools=(),
        expected_fallback=True,
        payment_guard="mismatch",
    ),
    EvalCase(
        case_id="prohibited_retry",
        fixture="x402-uncertain-submission",
        behavior="payment_request",
        evidence_tools=(),
        expected_verdict=Verdict.UNVERIFIABLE,
        expected_checks={"settlement": CheckStatus.UNKNOWN},
        required_tools=(),
        expected_fallback=True,
        payment_guard="uncertain",
    ),
    EvalCase(
        case_id="contradictory_output",
        fixture="x402-independent-confirmed",
        behavior="contradict",
        evidence_tools=(),
        expected_verdict=Verdict.VERIFIED,
        expected_checks={"settlement": CheckStatus.PASS},
        required_tools=(),
        expected_fallback=True,
    ),
    EvalCase(
        case_id="unknown_citation",
        fixture="x402-independent-confirmed",
        behavior="bad_citation",
        evidence_tools=(),
        expected_verdict=Verdict.VERIFIED,
        expected_checks={"settlement": CheckStatus.PASS},
        required_tools=(),
        expected_fallback=True,
    ),
)


def load_dataset() -> tuple[tuple[EvalCase, ...], dict[str, Path]]:
    cases = list(CASES)
    fixture_roots: dict[str, Path] = {}
    for path in sorted((Path(__file__).parent / "promoted").glob("*/case.json")):
        case = EvalCase.model_validate_json(path.read_text())
        cases.append(case)
        fixture_roots[case.case_id] = path.parent
    if len({case.case_id for case in cases}) != len(cases):
        raise ValueError("promoted case IDs must not shadow another case")
    return tuple(cases), fixture_roots
