CREATE TABLE IF NOT EXISTS contract_snapshots (
    snapshot_digest TEXT PRIMARY KEY,
    target TEXT NOT NULL,
    rail TEXT NOT NULL CHECK (rail IN ('perflo', 'x402')),
    semantic_fingerprint TEXT NOT NULL,
    source_digest TEXT NOT NULL,
    snapshot_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS contract_snapshot_observations (
    observation_id INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_digest TEXT NOT NULL REFERENCES contract_snapshots(snapshot_digest),
    observed_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS contract_snapshots_target_rail
    ON contract_snapshots(target, rail);
CREATE INDEX IF NOT EXISTS contract_snapshot_observations_digest
    ON contract_snapshot_observations(snapshot_digest, observation_id);

CREATE TRIGGER IF NOT EXISTS contract_snapshots_no_update
    BEFORE UPDATE ON contract_snapshots
    BEGIN
        SELECT RAISE(ABORT, 'contract snapshots are immutable');
    END;
CREATE TRIGGER IF NOT EXISTS contract_snapshots_no_delete
    BEFORE DELETE ON contract_snapshots
    BEGIN
        SELECT RAISE(ABORT, 'contract snapshots are immutable');
    END;
CREATE TRIGGER IF NOT EXISTS contract_snapshot_observations_no_update
    BEFORE UPDATE ON contract_snapshot_observations
    BEGIN
        SELECT RAISE(ABORT, 'contract snapshot observations are immutable');
    END;
CREATE TRIGGER IF NOT EXISTS contract_snapshot_observations_no_delete
    BEFORE DELETE ON contract_snapshot_observations
    BEGIN
        SELECT RAISE(ABORT, 'contract snapshot observations are immutable');
    END;
