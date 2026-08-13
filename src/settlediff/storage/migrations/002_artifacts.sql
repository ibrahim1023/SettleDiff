CREATE TABLE IF NOT EXISTS artifacts (
    run_id TEXT NOT NULL REFERENCES reports(run_id) ON DELETE CASCADE,
    artifact_id TEXT NOT NULL,
    artifact_json TEXT NOT NULL,
    PRIMARY KEY (run_id, artifact_id)
);
