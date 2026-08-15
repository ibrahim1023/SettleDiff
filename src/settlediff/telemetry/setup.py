"""Redacted structured logs and optional OpenTelemetry export."""

from __future__ import annotations

import hashlib
import json
import logging
import re
import secrets
import sys
from collections.abc import Generator, Mapping
from contextlib import contextmanager, suppress
from datetime import UTC, datetime
from decimal import Decimal
from typing import IO, cast

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.metrics import Counter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import MetricReader, PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor, SpanExporter
from opentelemetry.trace import Span, Status, StatusCode, Tracer
from opentelemetry.util.types import AttributeValue
from pydantic import SecretStr
from pydantic_ai import Agent
from pydantic_ai.agent import InstrumentationSettings

from settlediff.config import Settings
from settlediff.domain.models import CheckStatus, Verdict
from settlediff.domain.redaction import redact_embedded_identifiers

SAFE_ATTRIBUTE_KEYS = frozenset(
    {
        "action_class",
        "activity_candidate_count",
        "artifact_type",
        "check_name",
        "check_status",
        "component",
        "correlation_id",
        "cost_estimate",
        "duration_ms",
        "error_class",
        "error_code",
        "input_tokens",
        "limit_type",
        "matcher_strategy",
        "mode",
        "output_tokens",
        "parse_status",
        "provider",
        "recoverable",
        "reportable",
        "request_count",
        "status",
        "stderr_bytes",
        "stdout_bytes",
        "submission_uncertain",
        "tool_call_count",
        "verdict",
    }
)
_EVENT_NAME = re.compile(r"[a-z][a-z0-9_.]{0,63}\Z")
_SPAN_NAME = re.compile(r"settlediff\.[a-z][a-z0-9_.]{0,63}\Z")
_MAX_STRING_CHARS = 256
_RUN_MODES = frozenset({"live", "replay"})
_CHECK_NAMES = frozenset(
    {
        "activity_persistence",
        "asset",
        "budget",
        "chain",
        "ledger_outcome",
        "paid_failure",
        "price",
        "protocol",
        "recipient",
        "service_execution",
        "settlement",
    }
)
_METRIC_ATTRIBUTE_ENUMS = {
    "settlediff.runs": {
        "mode": _RUN_MODES,
        "verdict": frozenset(verdict.value for verdict in Verdict),
    },
    "settlediff.checks": {
        "check_name": _CHECK_NAMES,
        "check_status": frozenset(status.value for status in CheckStatus),
    },
}


class JsonLogFormatter(logging.Formatter):
    """Render only pre-sanitized diagnostic records."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname.lower(),
            "event": record.getMessage(),
            **cast(dict[str, AttributeValue], getattr(record, "safe_attributes", {})),
        }
        return json.dumps(payload, separators=(",", ":"), sort_keys=True)


class TelemetryRuntime:
    """One run-scoped privacy boundary for local logs and exported spans."""

    def __init__(
        self,
        *,
        provider: TracerProvider,
        meter_provider: MeterProvider,
        owns_meter_provider: bool,
        instrumentation: InstrumentationSettings,
        logger: logging.Logger,
        handler: logging.Handler,
        secret_values: frozenset[str],
    ) -> None:
        self._provider = provider
        self._meter_provider = meter_provider
        self._owns_meter_provider = owns_meter_provider
        self.instrumentation = instrumentation
        self._logger = logger
        self._handler = handler
        self._secret_values = secret_values
        self._correlation_salt = secrets.token_bytes(32)
        self._tracer: Tracer = provider.get_tracer("settlediff")
        meter = meter_provider.get_meter("settlediff")
        self._counters: dict[str, Counter] = {
            name: meter.create_counter(name) for name in _METRIC_ATTRIBUTE_ENUMS
        }

    def safe_attributes(self, attributes: Mapping[str, object]) -> dict[str, AttributeValue]:
        """Allow only bounded operational fields; replace local run IDs with a hash."""
        safe: dict[str, AttributeValue] = {}
        run_id = attributes.get("run_id")
        if isinstance(run_id, str):
            safe["correlation_id"] = self._correlation_id(run_id)
        for key, value in attributes.items():
            if key not in SAFE_ATTRIBUTE_KEYS or key == "correlation_id":
                continue
            converted = self._safe_scalar(value)
            if converted is not None:
                safe[key] = converted
        return safe

    def counter(self, name: str, attributes: Mapping[str, object]) -> None:
        """Increment one approved counter after validating its bounded labels."""
        attribute_enums = _METRIC_ATTRIBUTE_ENUMS.get(name)
        if attribute_enums is None:
            raise ValueError("unknown telemetry metric name")
        if set(attributes) != set(attribute_enums):
            raise ValueError("metric attributes must exactly match the approved keys")

        bounded_attributes: dict[str, AttributeValue] = {}
        for key, value in attributes.items():
            if not isinstance(value, str) or value not in attribute_enums[key]:
                raise ValueError("metric attributes must use bounded enum values")
            bounded_attributes[key] = value

        with suppress(Exception):
            self._counters[name].add(1, bounded_attributes)

    def event(
        self,
        name: str,
        attributes: Mapping[str, object],
        *,
        level: int = logging.INFO,
    ) -> None:
        if not _EVENT_NAME.fullmatch(name):
            raise ValueError("telemetry event names must be bounded lowercase identifiers")
        safe = self.safe_attributes(attributes)
        with suppress(Exception):
            self._logger.log(level, name, extra={"safe_attributes": safe})

    @contextmanager
    def span(self, name: str, attributes: Mapping[str, object]) -> Generator[Span]:
        """Create a domain span without recording exception messages or stack traces."""
        if not _SPAN_NAME.fullmatch(name):
            raise ValueError("telemetry span names must use the settlediff namespace")
        span = self._tracer.start_span(name, attributes=self.safe_attributes(attributes))
        token = trace.use_span(
            span, end_on_exit=False, record_exception=False, set_status_on_exception=False
        )
        try:
            with token:
                yield span
        except BaseException as error:
            span.set_attribute("error_class", type(error).__name__[:_MAX_STRING_CHARS])
            span.set_status(Status(StatusCode.ERROR))
            raise
        finally:
            with suppress(Exception):
                span.end()

    def force_flush(self) -> None:
        with suppress(Exception):
            self._provider.force_flush()
        with suppress(Exception):
            self._meter_provider.force_flush()

    def shutdown(self) -> None:
        with suppress(Exception):
            self._provider.shutdown()
        if self._owns_meter_provider:
            with suppress(Exception):
                self._meter_provider.shutdown()
        self._logger.removeHandler(self._handler)
        with suppress(Exception):
            self._handler.close()

    def _safe_scalar(self, value: object) -> AttributeValue | None:
        if isinstance(value, bool):
            return value
        if isinstance(value, int | float):
            return value
        if isinstance(value, Decimal):
            return float(value)
        if not isinstance(value, str):
            return None
        redacted = value
        for secret_value in self._secret_values:
            redacted = redacted.replace(secret_value, "[REDACTED]")
        return redact_embedded_identifiers(redacted)[:_MAX_STRING_CHARS]

    def _correlation_id(self, run_id: str) -> str:
        digest = hashlib.sha256(self._correlation_salt + run_id.encode()).hexdigest()
        return digest[:24]


def configure_telemetry(
    settings: Settings,
    *,
    span_exporter: SpanExporter | None = None,
    meter_provider: MeterProvider | None = None,
    metric_reader: MetricReader | None = None,
    log_stream: IO[str] | None = None,
) -> TelemetryRuntime:
    """Configure local JSON diagnostics and optional private OTLP export."""
    if meter_provider is not None and metric_reader is not None:
        raise ValueError("inject either a meter provider or a metric reader, not both")

    resource = Resource.create({"service.name": "settlediff"})
    otlp_endpoint = settings.otlp_endpoint.strip() if settings.otlp_endpoint else None
    provider = TracerProvider(resource=resource)
    if span_exporter is not None:
        provider.add_span_processor(SimpleSpanProcessor(span_exporter))
    elif otlp_endpoint:
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=otlp_endpoint)))

    instrumentation = InstrumentationSettings(
        tracer_provider=provider,
        include_content=False,
        include_binary_content=False,
        event_mode="attributes",
    )
    Agent.instrument_all(instrumentation)

    owns_meter_provider = meter_provider is None
    if meter_provider is None:
        if metric_reader is None and otlp_endpoint:
            metric_reader = PeriodicExportingMetricReader(
                OTLPMetricExporter(endpoint=otlp_endpoint)
            )
        readers = (metric_reader,) if metric_reader is not None else ()
        meter_provider = MeterProvider(metric_readers=readers, resource=resource)

    logger = logging.getLogger(f"settlediff.telemetry.{id(provider)}")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    handler = logging.StreamHandler(log_stream if log_stream is not None else sys.stderr)
    handler.setFormatter(JsonLogFormatter())
    logger.addHandler(handler)

    return TelemetryRuntime(
        provider=provider,
        meter_provider=meter_provider,
        owns_meter_provider=owns_meter_provider,
        instrumentation=instrumentation,
        logger=logger,
        handler=handler,
        secret_values=_settings_secrets(settings),
    )


def _settings_secrets(settings: Settings) -> frozenset[str]:
    values: set[str] = set()
    for value in settings.__dict__.values():
        if isinstance(value, SecretStr):
            secret_value = value.get_secret_value()
            if secret_value:
                values.add(secret_value)
    return frozenset(values)
