"""Small SQLite repository for immutable machine reports."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from threading import RLock
from typing import cast

from pydantic import JsonValue, TypeAdapter, ValidationError

from settlediff.application.run import RunEvent
from settlediff.domain.models import EvidenceArtifact, ExplanationRecord, MachineReport
from settlediff.domain.redaction import (
    redact_artifact,
    redact_embedded_identifiers,
    redact_report,
    redact_value,
)

_JSON_VALUE_ADAPTER: TypeAdapter[JsonValue] = TypeAdapter(JsonValue)


def _redact_rejected_output(value: str) -> str:
    try:
        parsed = _JSON_VALUE_ADAPTER.validate_python(json.loads(value))
    except (json.JSONDecodeError, ValidationError):
        return redact_embedded_identifiers(value)
    return json.dumps(redact_value(parsed), sort_keys=True, separators=(",", ":"))


def _redact_explanation(record: ExplanationRecord) -> ExplanationRecord:
    explanation = record.explanation
    redacted_explanation = explanation.model_copy(
        update={
            "summary": redact_embedded_identifiers(explanation.summary),
            "recommended_next_step": (
                redact_embedded_identifiers(explanation.recommended_next_step)
                if explanation.recommended_next_step is not None
                else None
            ),
        }
    )
    return record.model_copy(
        update={
            "explanation": redacted_explanation,
            "rejected_output": (
                _redact_rejected_output(record.rejected_output)
                if record.rejected_output is not None
                else None
            ),
        }
    )


class SQLiteReportRepository:
    def __init__(self, path: Path) -> None:
        self._connection = sqlite3.connect(path, check_same_thread=False)
        self._lock = RLock()
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._connection.execute("PRAGMA journal_mode = WAL")
        self._connection.execute("PRAGMA busy_timeout = 5000")
        self._migrate()

    def _migrate(self) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY)"
            )
            migrations = sorted(Path(__file__).with_name("migrations").glob("*.sql"))
            for migration in migrations:
                version = int(migration.name.split("_", maxsplit=1)[0])
                exists = self._connection.execute(
                    "SELECT 1 FROM schema_migrations WHERE version = ?", (version,)
                ).fetchone()
                if exists is None:
                    self._connection.executescript(migration.read_text())
                    self._connection.execute(
                        "INSERT INTO schema_migrations(version) VALUES (?)", (version,)
                    )

    def save(
        self,
        report: MachineReport,
        *,
        events: tuple[RunEvent, ...] = (),
        artifacts: tuple[EvidenceArtifact, ...] = (),
        explanation: ExplanationRecord | None = None,
    ) -> None:
        persisted_report = redact_report(report)
        persisted_explanation = (
            _redact_explanation(explanation) if explanation is not None else None
        )
        with self._lock, self._connection:
            self._connection.execute(
                "INSERT OR REPLACE INTO reports(run_id, report_json) VALUES (?, ?)",
                (report.run_id, persisted_report.model_dump_json()),
            )
            self._connection.execute("DELETE FROM run_events WHERE run_id = ?", (report.run_id,))
            self._connection.execute("DELETE FROM artifacts WHERE run_id = ?", (report.run_id,))
            self._connection.execute("DELETE FROM explanations WHERE run_id = ?", (report.run_id,))
            self._connection.executemany(
                "INSERT INTO run_events(run_id, position, event_json) VALUES (?, ?, ?)",
                (
                    (report.run_id, index, event.model_dump_json())
                    for index, event in enumerate(events)
                ),
            )
            self._connection.executemany(
                "INSERT INTO artifacts(run_id, artifact_id, artifact_json) VALUES (?, ?, ?)",
                (
                    (
                        report.run_id,
                        artifact.artifact_id,
                        redact_artifact(artifact).model_dump_json(),
                    )
                    for artifact in artifacts
                ),
            )
            if persisted_explanation is not None:
                self._connection.execute(
                    "INSERT INTO explanations(run_id, explanation_json) VALUES (?, ?)",
                    (report.run_id, persisted_explanation.model_dump_json()),
                )

    def get(self, run_id: str) -> MachineReport | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT report_json FROM reports WHERE run_id = ?", (run_id,)
            ).fetchone()
        return MachineReport.model_validate_json(row[0]) if row else None

    def list(self) -> tuple[MachineReport, ...]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT report_json FROM reports ORDER BY run_id DESC"
            ).fetchall()
        return tuple(MachineReport.model_validate_json(cast(str, row[0])) for row in rows)

    def delete(self, run_id: str) -> bool:
        with self._lock, self._connection:
            cursor = self._connection.execute("DELETE FROM reports WHERE run_id = ?", (run_id,))
            return cursor.rowcount == 1

    def events(self, run_id: str) -> tuple[RunEvent, ...]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT event_json FROM run_events WHERE run_id = ? ORDER BY position", (run_id,)
            ).fetchall()
        return tuple(RunEvent.model_validate_json(cast(str, row[0])) for row in rows)

    def artifacts(self, run_id: str) -> tuple[EvidenceArtifact, ...]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT artifact_json FROM artifacts WHERE run_id = ? ORDER BY artifact_id",
                (run_id,),
            ).fetchall()
        return tuple(EvidenceArtifact.model_validate_json(cast(str, row[0])) for row in rows)

    def explanation(self, run_id: str) -> ExplanationRecord | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT explanation_json FROM explanations WHERE run_id = ?", (run_id,)
            ).fetchone()
        return ExplanationRecord.model_validate_json(row[0]) if row else None

    def close(self) -> None:
        with self._lock:
            self._connection.close()
