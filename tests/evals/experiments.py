"""Versioned local experiment records and explicit comparison compatibility."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

from settlediff.domain.integrity import Sha256Digest, sha256_digest
from settlediff.domain.models import UtcDatetime
from tests.evals.dataset import EvalCase
from tests.evals.runner import ROOT, CaseResult, run_case


class Experiment(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    started_at: UtcDatetime
    revision: str
    dirty: bool
    model: Literal["scripted-function-model"] = "scripted-function-model"
    source_digest: Sha256Digest
    dataset_digest: Sha256Digest
    case_digests: dict[str, Sha256Digest]
    fixture_digests: dict[str, Sha256Digest]
    results: tuple[CaseResult, ...]


class Comparison(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    comparable: bool
    changed_cases: tuple[str, ...]
    added_cases: tuple[str, ...]
    removed_cases: tuple[str, ...]
    regressions: tuple[str, ...]
    improvements: tuple[str, ...]


def _file_digest(paths: tuple[Path, ...], root: Path) -> Sha256Digest:
    digest = hashlib.sha256()
    for path in sorted(paths):
        name = str(path.relative_to(root)).encode()
        content = path.read_bytes()
        digest.update(len(name).to_bytes(8, "big") + name)
        digest.update(len(content).to_bytes(8, "big") + content)
    return digest.hexdigest()


async def run_experiment(
    cases: tuple[EvalCase, ...],
    *,
    revision: str,
    dirty: bool,
    fixture_root: Path = ROOT / "fixtures",
    fixture_roots: dict[str, Path] | None = None,
) -> Experiment:
    if not cases or len({case.case_id for case in cases}) != len(cases):
        raise ValueError("experiments require non-empty, unique case IDs")
    started_at = datetime.now(UTC)
    roots = fixture_roots or {}
    results = tuple(
        [await run_case(case, fixture_root=roots.get(case.case_id, fixture_root)) for case in cases]
    )
    case_digests = {case.case_id: sha256_digest(case.model_dump(mode="json")) for case in cases}
    fixture_digests = {
        case.case_id: _file_digest(
            tuple((roots.get(case.case_id, fixture_root) / case.fixture).glob("*.json")),
            roots.get(case.case_id, fixture_root) / case.fixture,
        )
        for case in cases
    }
    source_paths = tuple((ROOT / "src/settlediff").rglob("*.py")) + tuple(
        (ROOT / "tests/evals").glob("*.py")
    )
    return Experiment(
        started_at=started_at,
        revision=revision,
        dirty=dirty,
        source_digest=_file_digest(source_paths, ROOT),
        dataset_digest=sha256_digest({"cases": case_digests, "fixtures": fixture_digests}),
        case_digests=case_digests,
        fixture_digests=fixture_digests,
        results=results,
    )


def compare_runs(previous: Experiment, current: Experiment) -> Comparison:
    before = {result.case_id: result.passed for result in previous.results}
    after = {result.case_id: result.passed for result in current.results}
    common = set(before) & set(after)
    changed = tuple(
        sorted(
            key
            for key in common
            if previous.case_digests[key] != current.case_digests[key]
            or previous.fixture_digests[key] != current.fixture_digests[key]
        )
    )
    added = tuple(sorted(set(after) - set(before)))
    removed = tuple(sorted(set(before) - set(after)))
    unchanged = common - set(changed)
    return Comparison(
        comparable=not (changed or added or removed),
        changed_cases=changed,
        added_cases=added,
        removed_cases=removed,
        regressions=tuple(sorted(key for key in unchanged if before[key] and not after[key])),
        improvements=tuple(sorted(key for key in unchanged if not before[key] and after[key])),
    )
