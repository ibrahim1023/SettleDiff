CREATE TABLE IF NOT EXISTS evidence_timeline_events (
    run_id TEXT NOT NULL REFERENCES run_records(run_id) ON DELETE CASCADE,
    sequence INTEGER NOT NULL CHECK (sequence >= 0),
    event_json TEXT NOT NULL,
    PRIMARY KEY (run_id, sequence)
);
