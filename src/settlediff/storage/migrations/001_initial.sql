CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY
);

CREATE TABLE IF NOT EXISTS reports (
    run_id TEXT PRIMARY KEY,
    report_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS run_events (
    run_id TEXT NOT NULL REFERENCES reports(run_id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    event_json TEXT NOT NULL,
    PRIMARY KEY (run_id, position)
);
