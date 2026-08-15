from __future__ import annotations

import io
from collections.abc import Sequence
from contextlib import suppress
from decimal import Decimal

from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from pydantic import SecretStr

from settlediff.application.replay import replay_fixture
from settlediff.config import Settings
from settlediff.telemetry.setup import SAFE_ATTRIBUTE_KEYS, configure_telemetry

CANARY = "syn_canary_secret_never_export"


def configured_settings() -> Settings:
    return Settings(
        _env_file=None,  # pyright: ignore[reportCallIssue]
        hyperfusion_api_key=SecretStr(CANARY),
        contextdev_api_key=SecretStr(CANARY),
    )


def exported_text(spans: tuple[ReadableSpan, ...]) -> str:
    return repr(
        [
            {
                "name": span.name,
                "attributes": dict(span.attributes or {}),
                "events": [
                    {"name": event.name, "attributes": dict(event.attributes or {})}
                    for event in span.events
                ],
            }
            for span in spans
        ]
    )


def test_canary_never_enters_local_logs_or_spans() -> None:
    exporter = InMemorySpanExporter()
    stream = io.StringIO()
    telemetry = configure_telemetry(
        configured_settings(), span_exporter=exporter, log_stream=stream
    )

    telemetry.event(
        "run.started",
        {
            "component": "application",
            "status": "started",
            "run_id": f"syn_run_{CANARY}",
            "api_key": CANARY,
            "prompt": f"prompt {CANARY}",
            "tool_arguments": {"authorization": CANARY},
            "request_body": {"token": CANARY},
            "artifact": {"secret": CANARY},
            "finding": f"observed {CANARY}",
            "stdout": CANARY,
        },
    )
    with (
        suppress(RuntimeError),
        telemetry.span(
            "settlediff.run",
            {
                "component": "application",
                "status": "failed",
                "run_id": f"syn_run_{CANARY}",
                "error_class": "SyntheticError",
                "error_message": f"failed with {CANARY}",
            },
        ),
    ):
        raise RuntimeError(f"synthetic exception {CANARY}")
    telemetry.force_flush()

    combined = stream.getvalue() + exported_text(exporter.get_finished_spans())
    assert CANARY not in combined
    for forbidden_key in (
        "api_key",
        "prompt",
        "tool_arguments",
        "request_body",
        "artifact",
        "finding",
        "stdout",
        "error_message",
        "run_id",
    ):
        assert forbidden_key not in combined
    assert "correlation_id" in combined
    assert "RuntimeError" in combined
    telemetry.shutdown()


def test_pydantic_ai_instrumentation_disables_content_capture() -> None:
    telemetry = configure_telemetry(configured_settings(), log_stream=io.StringIO())

    assert telemetry.instrumentation.include_content is False
    assert telemetry.instrumentation.include_binary_content is False
    assert telemetry.instrumentation.event_mode == "attributes"
    telemetry.shutdown()


def test_safe_attributes_accept_only_bounded_operational_fields() -> None:
    telemetry = configure_telemetry(configured_settings(), log_stream=io.StringIO())
    attributes = telemetry.safe_attributes(
        {
            "component": "perflo",
            "status": "failed",
            "duration_ms": 12.5,
            "submission_uncertain": True,
            "stdout_bytes": 42,
            "verdict": "PAID_FAILURE",
            "url": "https://example.invalid/?token=sensitive",
            "wallet": "syn_wallet_sensitive",
            "model_output": "sensitive",
        }
    )

    assert attributes == {
        "component": "perflo",
        "status": "failed",
        "duration_ms": 12.5,
        "submission_uncertain": True,
        "stdout_bytes": 42,
        "verdict": "PAID_FAILURE",
    }
    assert set(attributes) <= SAFE_ATTRIBUTE_KEYS
    telemetry.shutdown()


class FailingExporter(SpanExporter):
    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        del spans
        raise RuntimeError("synthetic exporter failure")

    def shutdown(self) -> None:
        pass


def test_exporter_failure_cannot_change_the_report() -> None:
    report = replay_fixture(__import__("pathlib").Path("fixtures/paid-failure"))
    telemetry = configure_telemetry(
        configured_settings(), span_exporter=FailingExporter(), log_stream=io.StringIO()
    )

    with telemetry.span(
        "settlediff.verify",
        {
            "component": "domain",
            "status": "complete",
            "verdict": report.verdict.value,
            "cost_estimate": Decimal("0.01"),
        },
    ):
        observed = report

    assert observed is report
    assert observed.verdict.value == "PAID_FAILURE"
    telemetry.force_flush()
    telemetry.shutdown()
