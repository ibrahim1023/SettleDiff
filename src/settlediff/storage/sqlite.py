"""Small SQLite repository for immutable machine reports."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from settlediff.domain.models import MachineReport


class SQLiteReportRepository:
    def __init__(self, path: Path) -> None:
        self._connection = sqlite3.connect(path)
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._connection.execute("PRAGMA busy_timeout = 5000")
        self._connection.execute(
            "CREATE TABLE IF NOT EXISTS reports "
            "(run_id TEXT PRIMARY KEY, report_json TEXT NOT NULL)"
        )

    def save(self, report: MachineReport) -> None:
        with self._connection:
            self._connection.execute(
                "INSERT OR REPLACE INTO reports(run_id, report_json) VALUES (?, ?)",
                (report.run_id, report.model_dump_json()),
            )

    def get(self, run_id: str) -> MachineReport | None:
        row = self._connection.execute(
            "SELECT report_json FROM reports WHERE run_id = ?", (run_id,)
        ).fetchone()
        return MachineReport.model_validate_json(row[0]) if row else None

    def close(self) -> None:
        self._connection.close()
