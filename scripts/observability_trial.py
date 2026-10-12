#!/usr/bin/env python3
"""Exercise private-default observability with synthetic evidence; export only with --export."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from opentelemetry.sdk.metrics.export import InMemoryMetricReader  # noqa: E402
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,  # noqa: E402
)

from settlediff.application.replay import replay_fixture  # noqa: E402
from settlediff.config import Settings  # noqa: E402
from settlediff.telemetry.setup import configure_telemetry  # noqa: E402


def run_trial(*, export: bool) -> dict[str, str | int | bool]:
    if export:
        settings = Settings()
        if not settings.otlp_endpoint:
            raise ValueError("export trial requires SETTLEDIFF_OTLP_ENDPOINT")
    else:
        settings = Settings(
            _env_file=None,  # pyright: ignore[reportCallIssue]
            otlp_endpoint=None,
            otlp_authorization=None,
        )
    exporter = None if export else InMemorySpanExporter()
    reader = None if export else InMemoryMetricReader()
    telemetry = configure_telemetry(settings, span_exporter=exporter, metric_reader=reader)
    try:
        with telemetry.span(
            "settlediff.run", {"run_id": "syn_observability_trial", "mode": "replay"}
        ):
            with telemetry.span("settlediff.verify", {"component": "domain"}):
                report = replay_fixture(
                    Path(__file__).resolve().parents[1] / "fixtures/x402-independent-confirmed"
                )
            telemetry.counter(
                "settlediff.runs", {"mode": "replay", "verdict": report.verdict.value}
            )
            for finding in report.findings:
                telemetry.counter(
                    "settlediff.checks",
                    {
                        "check_name": finding.check_id,
                        "check_status": finding.status.value,
                    },
                )
            telemetry.event("trial.synthetic", {"component": "application", "status": "complete"})
        telemetry.force_flush()
        result: dict[str, str | int | bool] = {
            "synthetic": True,
            "verdict": report.verdict.value,
            "export_requested": export,
        }
        if exporter is not None:
            result["spans"] = len(exporter.get_finished_spans())
        return result
    finally:
        telemetry.shutdown()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--export", action="store_true", help="Send synthetic telemetry to the configured backend"
    )
    args = parser.parse_args()
    print(json.dumps(run_trial(export=args.export), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
