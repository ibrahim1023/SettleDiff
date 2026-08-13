"""Small SQLite repository for immutable machine reports."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from threading import RLock
from typing import cast

from settlediff.application.run import RunEvent
from settlediff.domain.models import EvidenceArtifact, MachineReport
from settlediff.domain.redaction import redact_artifact


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
    ) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                "INSERT OR REPLACE INTO reports(run_id, report_json) VALUES (?, ?)",
                (report.run_id, report.model_dump_json()),
            )
            self._connection.execute("DELETE FROM run_events WHERE run_id = ?", (report.run_id,))
            self._connection.execute("DELETE FROM artifacts WHERE run_id = ?", (report.run_id,))
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

    def close(self) -> None:
        with self._lock:
            self._connection.close()
