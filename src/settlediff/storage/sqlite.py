"""Small SQLite repository for immutable machine reports."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock
from typing import cast

from settlediff.application.run import (
    RunEvent,
    RunFailure,
    RunProvenance,
    RunRecord,
    RunState,
)
from settlediff.application.timeline import (
    EvidenceTimelineEvent,
    build_evidence_timeline,
    redact_timeline_event,
)
from settlediff.domain.drift import ContractSnapshot
from settlediff.domain.models import EvidenceArtifact, ExplanationRecord, MachineReport
from settlediff.domain.redaction import (
    redact_artifact,
    redact_contract,
    redact_explanation_record,
    redact_report,
    redact_value,
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

    def check_writable(self) -> None:
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            self._connection.rollback()

    def begin_run(
        self,
        run_id: str,
        *,
        task: str,
        provenance: RunProvenance,
        created_at: datetime,
    ) -> None:
        record = RunRecord(
            run_id=run_id,
            task=task,
            provenance=provenance,
            created_at=created_at,
            latest_state=RunState.PREFLIGHT,
            report=None,
            failure=None,
        )
        with self._lock, self._connection:
            self._connection.execute(
                "INSERT INTO run_records(run_id, task, provenance, created_at, latest_state, "
                "report_json, failure_json) VALUES (?, ?, ?, ?, ?, NULL, NULL)",
                (
                    record.run_id,
                    record.task,
                    record.provenance.value,
                    record.created_at.isoformat(),
                    record.latest_state.value,
                ),
            )
            event = RunEvent(state=RunState.PREFLIGHT, occurred_at=record.created_at)
            self._connection.execute(
                "INSERT INTO run_record_events(run_id, position, event_json) VALUES (?, 0, ?)",
                (run_id, event.model_dump_json()),
            )

    def append_event(self, run_id: str, event: RunEvent) -> None:
        with self._lock, self._connection:
            row = self._connection.execute(
                "SELECT position, event_json FROM run_record_events WHERE run_id = ? "
                "ORDER BY position DESC LIMIT 1",
                (run_id,),
            ).fetchone()
            if row is None:
                raise ValueError("run record not found")
            latest = RunEvent.model_validate_json(cast(str, row[1]))
            if latest.state is event.state:
                return
            self._connection.execute(
                "INSERT INTO run_record_events(run_id, position, event_json) VALUES (?, ?, ?)",
                (run_id, int(row[0]) + 1, event.model_dump_json()),
            )
            updated = self._connection.execute(
                "UPDATE run_records SET latest_state = ? WHERE run_id = ?",
                (event.state.value, run_id),
            )
            if updated.rowcount != 1:
                raise ValueError("run record not found")

    def save_artifacts(self, run_id: str, artifacts: tuple[EvidenceArtifact, ...]) -> None:
        with self._lock, self._connection:
            exists = self._connection.execute(
                "SELECT 1 FROM run_records WHERE run_id = ?", (run_id,)
            ).fetchone()
            if exists is None:
                raise ValueError("run record not found")
            self._connection.executemany(
                "INSERT INTO run_record_artifacts(run_id, artifact_id, artifact_json) "
                "VALUES (?, ?, ?) ON CONFLICT(run_id, artifact_id) DO UPDATE SET "
                "artifact_json = excluded.artifact_json",
                (
                    (
                        run_id,
                        artifact.artifact_id,
                        redact_artifact(artifact).model_dump_json(),
                    )
                    for artifact in artifacts
                ),
            )

    def record_failure(self, run_id: str, failure: RunFailure) -> None:
        with self._lock, self._connection:
            updated = self._connection.execute(
                "UPDATE run_records SET latest_state = ?, failure_json = ? WHERE run_id = ?",
                (RunState.FAILED.value, failure.model_dump_json(), run_id),
            )
            if updated.rowcount != 1:
                raise ValueError("run record not found")

    def _insert_timeline(self, run_id: str, timeline: tuple[EvidenceTimelineEvent, ...]) -> None:
        if not timeline:
            raise ValueError("final evidence timeline cannot be empty")
        for position, event in enumerate(timeline):
            if event.sequence != position:
                raise ValueError("timeline event sequence does not match its position")
        self._connection.executemany(
            "INSERT INTO evidence_timeline_events(run_id, sequence, event_json) VALUES (?, ?, ?)",
            (
                (run_id, event.sequence, redact_timeline_event(event).model_dump_json())
                for event in timeline
            ),
        )

    def finalize_run(
        self,
        report: MachineReport,
        *,
        explanation: ExplanationRecord | None,
        timeline: tuple[EvidenceTimelineEvent, ...],
    ) -> None:
        persisted_report = redact_report(report)
        persisted_explanation = (
            redact_explanation_record(explanation) if explanation is not None else None
        )
        with self._lock, self._connection:
            updated = self._connection.execute(
                "UPDATE run_records SET task = ?, latest_state = ?, report_json = ?, "
                "failure_json = NULL WHERE run_id = ?",
                (
                    persisted_report.intent.task,
                    RunState.COMPLETE.value,
                    persisted_report.model_dump_json(),
                    report.run_id,
                ),
            )
            if updated.rowcount != 1:
                raise ValueError("run record not found")
            self._connection.execute(
                "INSERT OR REPLACE INTO reports(run_id, report_json) VALUES (?, ?)",
                (report.run_id, persisted_report.model_dump_json()),
            )
            self._connection.execute(
                "DELETE FROM run_record_explanations WHERE run_id = ?", (report.run_id,)
            )
            self._connection.execute("DELETE FROM explanations WHERE run_id = ?", (report.run_id,))
            if persisted_explanation is not None:
                self._connection.execute(
                    "INSERT INTO run_record_explanations(run_id, explanation_json) VALUES (?, ?)",
                    (report.run_id, persisted_explanation.model_dump_json()),
                )
                self._connection.execute(
                    "INSERT INTO explanations(run_id, explanation_json) VALUES (?, ?)",
                    (report.run_id, persisted_explanation.model_dump_json()),
                )
            self._insert_timeline(report.run_id, timeline)

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
            redact_explanation_record(explanation) if explanation is not None else None
        )
        persisted_artifacts = tuple(redact_artifact(artifact) for artifact in artifacts)
        with self._lock, self._connection:
            self._connection.execute(
                "INSERT INTO run_records(run_id, task, provenance, created_at, latest_state, "
                "report_json, failure_json) VALUES (?, ?, ?, ?, ?, ?, NULL) "
                "ON CONFLICT(run_id) DO UPDATE SET task = excluded.task, "
                "latest_state = excluded.latest_state, report_json = excluded.report_json, "
                "failure_json = NULL",
                (
                    report.run_id,
                    persisted_report.intent.task,
                    RunProvenance.FIXTURE.value,
                    persisted_report.intent.created_at.isoformat(),
                    RunState.COMPLETE.value,
                    persisted_report.model_dump_json(),
                ),
            )
            self._connection.execute(
                "INSERT OR REPLACE INTO reports(run_id, report_json) VALUES (?, ?)",
                (report.run_id, persisted_report.model_dump_json()),
            )
            for table in (
                "run_events",
                "artifacts",
                "explanations",
                "run_record_events",
                "run_record_artifacts",
                "run_record_explanations",
            ):
                self._connection.execute(f"DELETE FROM {table} WHERE run_id = ?", (report.run_id,))
            self._connection.executemany(
                "INSERT INTO run_events(run_id, position, event_json) VALUES (?, ?, ?)",
                (
                    (report.run_id, index, event.model_dump_json())
                    for index, event in enumerate(events)
                ),
            )
            self._connection.executemany(
                "INSERT INTO run_record_events(run_id, position, event_json) VALUES (?, ?, ?)",
                (
                    (report.run_id, index, event.model_dump_json())
                    for index, event in enumerate(events)
                ),
            )
            artifact_rows = tuple(
                (report.run_id, artifact.artifact_id, artifact.model_dump_json())
                for artifact in persisted_artifacts
            )
            self._connection.executemany(
                "INSERT INTO artifacts(run_id, artifact_id, artifact_json) VALUES (?, ?, ?)",
                artifact_rows,
            )
            self._connection.executemany(
                "INSERT INTO run_record_artifacts(run_id, artifact_id, artifact_json) "
                "VALUES (?, ?, ?)",
                artifact_rows,
            )
            if persisted_explanation is not None:
                explanation_json = persisted_explanation.model_dump_json()
                self._connection.execute(
                    "INSERT INTO explanations(run_id, explanation_json) VALUES (?, ?)",
                    (report.run_id, explanation_json),
                )
                self._connection.execute(
                    "INSERT INTO run_record_explanations(run_id, explanation_json) VALUES (?, ?)",
                    (report.run_id, explanation_json),
                )
            has_timeline = self._connection.execute(
                "SELECT 1 FROM evidence_timeline_events WHERE run_id = ? LIMIT 1",
                (report.run_id,),
            ).fetchone()
            if has_timeline is None:
                self._insert_timeline(
                    report.run_id,
                    build_evidence_timeline(persisted_report, events, persisted_artifacts),
                )

    def record(self, run_id: str) -> RunRecord | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT run_id, task, provenance, created_at, latest_state, report_json, "
                "failure_json FROM run_records WHERE run_id = ?",
                (run_id,),
            ).fetchone()
        return self._run_record(row) if row is not None else None

    def records(self) -> tuple[RunRecord, ...]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT run_id, task, provenance, created_at, latest_state, report_json, "
                "failure_json FROM run_records ORDER BY created_at DESC, run_id DESC"
            ).fetchall()
        return tuple(self._run_record(row) for row in rows)

    @staticmethod
    def _run_record(row: tuple[object, ...]) -> RunRecord:
        report_json = cast(str | None, row[5])
        failure_json = cast(str | None, row[6])
        return RunRecord(
            run_id=cast(str, row[0]),
            task=cast(str, row[1]),
            provenance=RunProvenance(cast(str, row[2])),
            created_at=datetime.fromisoformat(cast(str, row[3])),
            latest_state=RunState(cast(str, row[4])),
            report=(
                MachineReport.model_validate_json(report_json) if report_json is not None else None
            ),
            failure=(
                RunFailure.model_validate_json(failure_json) if failure_json is not None else None
            ),
        )

    def get(self, run_id: str) -> MachineReport | None:
        record = self.record(run_id)
        return record.report if record is not None else None

    def list(self) -> tuple[MachineReport, ...]:
        return tuple(record.report for record in self.records() if record.report is not None)

    def delete(self, run_id: str) -> bool:
        with self._lock, self._connection:
            record = self._connection.execute("DELETE FROM run_records WHERE run_id = ?", (run_id,))
            report = self._connection.execute("DELETE FROM reports WHERE run_id = ?", (run_id,))
            return record.rowcount == 1 or report.rowcount == 1

    def events(self, run_id: str) -> tuple[RunEvent, ...]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT event_json FROM run_record_events WHERE run_id = ? ORDER BY position",
                (run_id,),
            ).fetchall()
        return tuple(RunEvent.model_validate_json(cast(str, row[0])) for row in rows)

    def timeline(self, run_id: str) -> tuple[EvidenceTimelineEvent, ...]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT event_json FROM evidence_timeline_events "
                "WHERE run_id = ? ORDER BY sequence",
                (run_id,),
            ).fetchall()
        return tuple(
            EvidenceTimelineEvent.model_validate_json(cast(str, row[0]), strict=True)
            for row in rows
        )

    def artifacts(self, run_id: str) -> tuple[EvidenceArtifact, ...]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT artifact_json FROM run_record_artifacts "
                "WHERE run_id = ? ORDER BY artifact_id",
                (run_id,),
            ).fetchall()
        return tuple(EvidenceArtifact.model_validate_json(cast(str, row[0])) for row in rows)

    def explanation(self, run_id: str) -> ExplanationRecord | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT explanation_json FROM run_record_explanations WHERE run_id = ?", (run_id,)
            ).fetchone()
        return ExplanationRecord.model_validate_json(row[0]) if row else None

    def save_contract_snapshot(self, snapshot: ContractSnapshot, observed_at: datetime) -> None:
        """Insert the content-addressed snapshot once and append one observation."""
        if observed_at.tzinfo is None or observed_at.utcoffset() != UTC.utcoffset(observed_at):
            raise ValueError("contract snapshot observations require an aware UTC time")
        snapshot = ContractSnapshot.model_validate_json(snapshot.model_dump_json(), strict=True)
        stored = ContractSnapshot.model_validate(
            {
                **snapshot.model_dump(mode="python"),
                "contract": redact_contract(snapshot.contract),
                "source_contract": redact_value(snapshot.source_contract),
            },
            strict=True,
        )
        with self._lock, self._connection:
            self._connection.execute(
                "INSERT INTO contract_snapshots(snapshot_digest, target, rail, "
                "semantic_fingerprint, source_digest, snapshot_json) "
                "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(snapshot_digest) DO NOTHING",
                (
                    snapshot.snapshot_digest,
                    snapshot.target,
                    snapshot.rail,
                    snapshot.semantic_fingerprint,
                    snapshot.source_digest,
                    stored.model_dump_json(),
                ),
            )
            self._connection.execute(
                "INSERT INTO contract_snapshot_observations(snapshot_digest, observed_at) "
                "VALUES (?, ?)",
                (snapshot.snapshot_digest, observed_at.isoformat()),
            )

    def contract_snapshots(self, target: str, rail: str) -> tuple[ContractSnapshot, ...]:
        if rail not in {"perflo", "x402"}:
            raise ValueError("unsupported contract snapshot rail")
        with self._lock:
            rows = self._connection.execute(
                "SELECT s.snapshot_json FROM contract_snapshots s "
                "JOIN (SELECT snapshot_digest, MIN(observation_id) AS first_seen "
                "FROM contract_snapshot_observations GROUP BY snapshot_digest) o "
                "ON o.snapshot_digest = s.snapshot_digest "
                "WHERE s.target = ? AND s.rail = ? ORDER BY o.first_seen",
                (target, rail),
            ).fetchall()
        return tuple(
            ContractSnapshot.model_validate_json(cast(str, row[0]), strict=True) for row in rows
        )

    def latest_contract_snapshot(self, target: str, rail: str) -> ContractSnapshot | None:
        if rail not in {"perflo", "x402"}:
            raise ValueError("unsupported contract snapshot rail")
        with self._lock:
            row = self._connection.execute(
                "SELECT s.snapshot_json FROM contract_snapshots s "
                "JOIN contract_snapshot_observations o "
                "ON o.snapshot_digest = s.snapshot_digest "
                "WHERE s.target = ? AND s.rail = ? "
                "ORDER BY o.observation_id DESC LIMIT 1",
                (target, rail),
            ).fetchone()
        return (
            ContractSnapshot.model_validate_json(cast(str, row[0]), strict=True)
            if row is not None
            else None
        )

    def contract_snapshot_observation_count(self, snapshot_digest: str) -> int:
        with self._lock:
            row = self._connection.execute(
                "SELECT COUNT(*) FROM contract_snapshot_observations WHERE snapshot_digest = ?",
                (snapshot_digest,),
            ).fetchone()
        return int(cast(tuple[int], row)[0])

    def close(self) -> None:
        with self._lock:
            self._connection.close()
