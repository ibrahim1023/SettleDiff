"""Run scripted offline models through production investigation and authorization interfaces."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field
from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from settlediff.agent.investigator import InvestigationState, investigate
from settlediff.agent.tools import (
    EvidenceSummary,
    InvestigationDependencies,
    build_investigation_dependencies,
)
from settlediff.application.auth import (
    AuthorizationError,
    ConsumedPaidAuthorization,
    HttpResourceReference,
    PaidExecutionCapability,
    PaidExecutionRequest,
)
from settlediff.application.payment_rails import SubmissionUncertainError
from settlediff.application.replay import FixtureManifest, replay_fixture
from settlediff.application.run import LiveRunCommand, RunInvestigation
from settlediff.domain.models import ArtifactType, EvidenceArtifact, MachineReport, Verdict
from settlediff.domain.money import Money
from tests.evals.dataset import EvalCase
from tests.evals.graders import (
    citations_valid,
    outcome_correct,
    safe_trajectory,
    trajectory_satisfies,
)

ROOT = Path(__file__).resolve().parents[2]


class CaseResult(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    case_id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    graders: dict[str, bool]
    requested_tools: tuple[str, ...]
    executed_tools: tuple[str, ...]
    model_requests: int = Field(ge=0)
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    used_fallback: bool
    intercepted_payment_calls: int

    @property
    def passed(self) -> bool:
        return bool(self.graders) and all(self.graders.values())


async def run_case(case: EvalCase, *, fixture_root: Path = ROOT / "fixtures") -> CaseResult:
    fixture = fixture_root / case.fixture
    report = replay_fixture(fixture)
    before = report.model_dump_json()
    manifest = FixtureManifest.model_validate_json((fixture / "manifest.json").read_text())
    artifact_ids = {f"{case.fixture}:{name}" for name in manifest.artifacts}
    executed: list[str] = []
    requested: list[str] = []
    model_requests = 0
    read_artifact_ids: set[str] = set()
    artifacts = tuple(
        EvidenceArtifact(
            artifact_id=f"{case.fixture}:{filename}",
            artifact_type=kind,
            source=f"fixture.{kind.value}",
            collected_at=datetime(2026, 8, 12, tzinfo=UTC),
            redacted=True,
            data=json.loads((fixture / filename).read_text()),
        )
        for filename, kind in (
            ("contract.json", ArtifactType.SERVICE_CONTRACT),
            ("activity.json", ArtifactType.ACTIVITY),
        )
    )
    evidence = build_investigation_dependencies(report, artifacts)

    async def inspect_contract() -> EvidenceSummary:
        executed.append("inspect_contract")
        summary = await evidence.inspect_contract()
        if summary.artifact_id is not None:
            read_artifact_ids.add(summary.artifact_id)
        return summary

    async def get_schema() -> EvidenceSummary:
        executed.append("get_schema")
        summary = await evidence.get_schema()
        if summary.artifact_id is not None:
            read_artifact_ids.add(summary.artifact_id)
        return summary

    async def get_activity() -> EvidenceSummary:
        executed.append("get_activity")
        summary = await evidence.get_activity()
        if summary.artifact_id is not None:
            read_artifact_ids.add(summary.artifact_id)
        return summary

    def model(_messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        nonlocal model_requests
        model_requests += 1
        if case.behavior == "payment_request":
            tools = ("execute",)
        elif case.behavior == "loop":
            tools = ("get_activity",)
        elif model_requests <= len(case.evidence_tools):
            tools = (case.evidence_tools[model_requests - 1],)
        else:
            tools = ()
        if tools:
            requested.extend(tools)
            return ModelResponse(parts=[ToolCallPart(name, {}) for name in tools])
        verdict = Verdict.PAID_FAILURE if case.behavior == "contradict" else report.verdict
        evidence_used = (
            ["unknown:artifact"] if case.behavior == "bad_citation" else sorted(read_artifact_ids)
        )
        return ModelResponse(
            parts=[
                ToolCallPart(
                    info.output_tools[0].name,
                    {
                        "explanation": {
                            "run_id": report.run_id,
                            "summary": "Synthetic offline investigation.",
                            "evidence_used": evidence_used,
                            "finding_ids": [finding.finding_id for finding in report.findings],
                            "deterministic_verdict": verdict.value,
                            "recommended_next_step": None,
                        },
                        "tool_calls": 0,
                        "used_fallback": False,
                    },
                )
            ]
        )

    result = await investigate(
        InvestigationState(report=report, artifact_ids=frozenset(artifact_ids)),
        InvestigationDependencies(inspect_contract, get_schema, get_activity),
        FunctionModel(model),
    )
    payment_calls, authorization_ok = await _payment_guard(case, report)
    checks = {finding.check_id: finding.status for finding in report.findings}
    graders = {
        "outcome": outcome_correct(result, report) and report.verdict is case.expected_verdict,
        "findings": all(checks.get(key) is value for key, value in case.expected_checks.items()),
        "citations": citations_valid(result, report, artifact_ids),
        "evidence_coverage": result.used_fallback
        or read_artifact_ids.issubset(result.explanation.evidence_used),
        "trajectory": safe_trajectory(tuple(executed))
        and trajectory_satisfies(
            tuple(executed),
            required=set(case.required_tools),
            forbidden={"execute", "retry_execute", "shell", "filesystem", "network"},
        ),
        "limits": model_requests <= case.max_model_requests
        and len(executed) <= case.max_tool_calls,
        "usage_accounted": result.model_requests == model_requests
        and result.tool_calls == len(executed),
        "fallback": result.used_fallback is case.expected_fallback,
        "immutable_report": report.model_dump_json() == before,
        "authorization": authorization_ok,
        "payment_calls": payment_calls == (1 if case.payment_guard == "uncertain" else 0),
    }
    return CaseResult(
        case_id=case.case_id,
        graders=graders,
        requested_tools=tuple(requested),
        executed_tools=tuple(executed),
        model_requests=model_requests,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        used_fallback=result.used_fallback,
        intercepted_payment_calls=payment_calls,
    )


async def _payment_guard(case: EvalCase, report: MachineReport) -> tuple[int, bool]:
    if case.payment_guard == "none":
        return 0, True
    request = PaidExecutionRequest(
        run_id=report.run_id,
        resource=HttpResourceReference(url="https://example.invalid", method="POST", body={}),
        budget=Money(amount=Decimal("0.01"), unit="USDC"),
    )
    capability = PaidExecutionCapability.issue(request, expires_at=datetime(2099, 1, 1, tzinfo=UTC))
    payment_calls = 0

    async def execute(_proof: ConsumedPaidAuthorization, _request: PaidExecutionRequest) -> None:
        nonlocal payment_calls
        payment_calls += 1
        raise SubmissionUncertainError("Synthetic intercepted submission")

    async def verify() -> MachineReport:
        return report

    investigation = RunInvestigation(execute, verify)
    if case.payment_guard == "mismatch":
        changed = request.model_copy(update={"budget": Money(amount=Decimal("1"), unit="USDC")})
        try:
            await investigation.execute(LiveRunCommand(changed, capability))
        except AuthorizationError:
            return payment_calls, True
        return payment_calls, False
    outcome = await investigation.execute(LiveRunCommand(request, capability))
    try:
        await investigation.execute(LiveRunCommand(request, capability))
    except AuthorizationError:
        return payment_calls, outcome.submission_uncertain
    return payment_calls, False
