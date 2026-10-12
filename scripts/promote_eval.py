#!/usr/bin/env python3
"""Create a regression case from a reviewed synthetic reconstruction of a local failure."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tests.evals.dataset import EvalCase  # noqa: E402
from tests.evals.promotion import promote_case  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--criteria", type=Path, required=True, help="Explicit EvalCase JSON")
    parser.add_argument("--out", type=Path, default=Path("tests/evals/promoted"))
    parser.add_argument("--reviewed", action="store_true")
    args = parser.parse_args()
    case = EvalCase.model_validate_json(args.criteria.read_text())
    destination = promote_case(args.bundle, args.fixture, case, args.out, reviewed=args.reviewed)
    print(destination / "case.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
