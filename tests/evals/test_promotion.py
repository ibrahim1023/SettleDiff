from __future__ import annotations

import json
import shutil
from datetime import UTC, datetime
from pathlib import Path

import pytest

from settlediff.application.bundle import export_bundle, serialize_bundle
from settlediff.application.replay import replay_fixture
from settlediff.domain.models import ArtifactType, EvidenceArtifact
from settlediff.storage.sqlite import SQLiteReportRepository
from tests.evals.dataset import CASES, EvalCase
from tests.evals.promotion import promote_case
from tests.evals.runner import run_case


def failure_bundle(tmp_path: Path) -> Path:
    fixture = Path("fixtures/missing-activity")
    report = replay_fixture(fixture)
    repository = SQLiteReportRepository(tmp_path / "source.sqlite3")
    artifacts = tuple(
        EvidenceArtifact(
            artifact_id=f"{report.run_id}:{kind.value}",
            artifact_type=kind,
            source="fixture",
            collected_at=datetime(2026, 8, 12, tzinfo=UTC),
            redacted=False,
            data=json.loads((fixture / filename).read_text()),
        )
        for filename, kind in (
            ("contract.json", ArtifactType.SERVICE_CONTRACT),
            ("execution.json", ArtifactType.EXECUTION),
            ("activity.json", ArtifactType.ACTIVITY),
        )
    )
    repository.save(report, artifacts=artifacts)
    path = tmp_path / "failure.sdbundle"
    path.write_bytes(serialize_bundle(export_bundle(repository, report.run_id)))
    repository.close()
    return path


@pytest.mark.asyncio
async def test_promotion_copies_only_reviewed_synthetic_evidence_and_explicit_criteria(
    tmp_path: Path,
) -> None:
    bundle = failure_bundle(tmp_path)
    case = CASES[1].model_copy(update={"case_id": "promoted_missing_activity"})
    path = promote_case(
        bundle, Path("fixtures/missing-activity"), case, tmp_path / "promoted", reviewed=True
    )

    copied = EvalCase.model_validate_json((path / "case.json").read_text())
    result = await run_case(copied, fixture_root=path)
    assert result.passed
    provenance = json.loads((path / "provenance.json").read_text())
    assert len(provenance["source_bundle_digest"]) == 64
    assert provenance["synthetic_reconstruction"] is True
    assert "syn_run" not in (path / "provenance.json").read_text()
    assert not (path / "failure.sdbundle").exists()


@pytest.mark.parametrize(
    "problem",
    [
        "unreviewed",
        "unsafe",
        "secret_field",
        "identifier",
        "tampered",
        "exists",
    ],
)
def test_promotion_refuses_unsafe_or_unreviewed_input_without_overwriting(
    tmp_path: Path,
    problem: str,
) -> None:
    bundle = failure_bundle(tmp_path)
    fixture = tmp_path / "fixture"
    shutil.copytree(Path("fixtures/missing-activity"), fixture)
    case = CASES[1]
    output = tmp_path / "promoted"
    if problem == "unsafe":
        (fixture / "execution.json").write_text('{"api_key":"sk-secret-do-not-copy"}')
    elif problem == "secret_field":
        execution = json.loads((fixture / "execution.json").read_text())
        execution["password"] = "p"
        (fixture / "execution.json").write_text(json.dumps(execution))
    elif problem == "identifier":
        execution = json.loads((fixture / "execution.json").read_text())
        execution["accountId"] = "customer_12345"
        (fixture / "execution.json").write_text(json.dumps(execution))
    elif problem == "tampered":
        bundle.write_bytes(b"invalid bundle")
    elif problem == "exists":
        (output / case.case_id).mkdir(parents=True)
        (output / case.case_id / "keep.txt").write_text("preserve me")
    with pytest.raises(ValueError):
        promote_case(bundle, fixture, case, output, reviewed=problem != "unreviewed")
    if problem == "exists":
        assert (output / case.case_id / "keep.txt").read_text() == "preserve me"
    else:
        assert not output.exists()


def test_promotion_rejects_manifest_path_escape(tmp_path: Path) -> None:
    bundle = failure_bundle(tmp_path)
    fixture = tmp_path / "fixture"
    shutil.copytree(Path("fixtures/missing-activity"), fixture)
    manifest = json.loads((fixture / "manifest.json").read_text())
    manifest["artifacts"].append("../outside.json")
    (tmp_path / "outside.json").write_text("{}")
    (fixture / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="artifact filename"):
        promote_case(bundle, fixture, CASES[1], tmp_path / "promoted", reviewed=True)


@pytest.mark.parametrize("omitted", ["contract.json", "execution.json", "activity.json"])
def test_promotion_refuses_manifest_missing_required_replay_artifact(
    tmp_path: Path,
    omitted: str,
) -> None:
    bundle = failure_bundle(tmp_path)
    fixture = tmp_path / "fixture"
    shutil.copytree(Path("fixtures/missing-activity"), fixture)
    manifest = json.loads((fixture / "manifest.json").read_text())
    manifest["artifacts"].remove(omitted)
    (fixture / "manifest.json").write_text(json.dumps(manifest))
    output = tmp_path / "promoted"
    with pytest.raises(ValueError, match="required replay artifacts"):
        promote_case(bundle, fixture, CASES[1], output, reviewed=True)
    assert not output.exists()
