"""Small SQLite repository for immutable machine reports."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from threading import RLock
from typing import cast

from settlediff.domain.models import MachineReport


class SQLiteReportRepository:
    def __init__(self, path: Path) -> None:
        self._connection = sqlite3.connect(path, check_same_thread=False)
        self._lock = RLock()
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._connection.execute("PRAGMA journal_mode = WAL")
        self._connection.execute("PRAGMA busy_timeout = 5000")
        self._migrate()

    def _migrate(self) -> None:
        migration = Path(__file__).with_name("migrations") / "001_initial.sql"
        with self._lock, self._connection:
            self._connection.executescript(migration.read_text())
            self._connection.execute("INSERT OR IGNORE INTO schema_migrations(version) VALUES (1)")

    def save(self, report: MachineReport) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                "INSERT OR REPLACE INTO reports(run_id, report_json) VALUES (?, ?)",
                (report.run_id, report.model_dump_json()),
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

    def close(self) -> None:
        with self._lock:
            self._connection.close()
