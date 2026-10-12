#!/usr/bin/env python3
"""Run offline evals and save a versioned, payload-free experiment record."""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pydantic_ai import models  # noqa: E402

from tests.evals.dataset import EvalCase, load_dataset  # noqa: E402
from tests.evals.experiments import Experiment, compare_runs, run_experiment  # noqa: E402
from tests.evals.runner import ROOT  # noqa: E402


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--baseline", type=Path)
    parser.add_argument(
        "--case", type=Path, help="A promoted case.json; defaults to the full dataset"
    )
    args = parser.parse_args()
    baseline = None
    if args.baseline is not None:
        same_file = args.baseline.resolve() == args.out.resolve()
        if args.baseline.exists() and args.out.exists():
            same_file = same_file or args.baseline.samefile(args.out)
        if same_file:
            raise ValueError("output must not overwrite the baseline")
        baseline = Experiment.model_validate_json(args.baseline.read_text())
    models.ALLOW_MODEL_REQUESTS = False
    revision = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, check=True, capture_output=True, text=True
    ).stdout.strip()
    dirty = bool(
        subprocess.run(
            ["git", "status", "--porcelain", "--", "src", "tests/evals", "scripts"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    )
    cases, fixture_roots = load_dataset()
    fixture_root = ROOT / "fixtures"
    if args.case is not None:
        cases = (EvalCase.model_validate_json(args.case.read_text()),)
        fixture_root = args.case.parent
        fixture_roots = {}
    run = await run_experiment(
        cases,
        revision=revision,
        dirty=dirty,
        fixture_root=fixture_root,
        fixture_roots=fixture_roots,
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(run.model_dump_json(indent=2) + "\n")
    summary: dict[str, object] = {
        "cases": len(run.results),
        "passed": sum(result.passed for result in run.results),
        "record": str(args.out),
    }
    if baseline is not None:
        summary["comparison"] = compare_runs(baseline, run).model_dump(mode="json")
    print(json.dumps(summary, sort_keys=True))
    return 0 if all(result.passed for result in run.results) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
