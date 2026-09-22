# Local Data Operations

## Scope

SettleDiff stores durable run records and finalized reports in one local SQLite database. Migration 4 backfills existing reports into the run ledger and adds incremental events, artifacts, failure evidence, and provenance. Migration 5 adds `evidence_timeline_events`, a persisted per-run evidence timeline built once when a run is saved. Migration 6 adds `contract_snapshots` plus `contract_snapshot_observations`, content-addressed and immutable — update/delete triggers on both tables reject modification, so repeated observations of the same contract accumulate rather than overwrite. Migrations are forward-only and run when `SQLiteReportRepository` opens the database. There is no downgrade command.

The tested upgrade baseline is a schema-4 database carried through every later migration: `test_database_schema_four_copy_migrates_through_every_new_migration` in `tests/integration/storage/test_sqlite.py` applies migrations 1–4 by hand and opens the repository to apply 5 and 6. Migration 5 creates the timeline table but does not synthesize timeline rows for pre-existing schema-4 reports; a later explicit save or finalization derives the timeline from persisted report and event data, preserving `source_time = null` where no source time was available. The test performs that explicit save and also proves the snapshot APIs work on the migrated database. Back up the database before upgrading; the backup remains required because migrations are not reversible.

## Back up before upgrading

Stop `settlediff serve` and any other SettleDiff process using the database. Copy the closed database file before running a newer SettleDiff version:

```bash
cp /path/to/settlediff.sqlite3 /path/to/settlediff.sqlite3.backup
```

Do not copy a live database while WAL writers are active. Keep the backup local because it contains redacted financial evidence and investigation history.

## Upgrade

Run one read command with the new version to apply numbered migrations:

```bash
settlediff show RUN_ID --database /path/to/settlediff.sqlite3
```

Then run the fixture demo against a separate temporary database before resuming normal work.

## Roll back

Because migrations are not reversible, stop all SettleDiff processes and restore the complete pre-upgrade backup:

```bash
cp /path/to/settlediff.sqlite3.backup /path/to/settlediff.sqlite3
```

Use the SettleDiff version that created that backup. Do not run older code against a database that has newer migrations.

## Retention

Reports remain until explicitly deleted. Preview age-based deletion before applying it:

```bash
settlediff purge --database /path/to/settlediff.sqlite3 --older-than 30d
settlediff purge --database /path/to/settlediff.sqlite3 --older-than 30d --apply
```

Use `settlediff export` before deletion when a portable redacted evidence record is required.
