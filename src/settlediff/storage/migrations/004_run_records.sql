CREATE TABLE IF NOT EXISTS run_records (
    run_id TEXT PRIMARY KEY,
    task TEXT NOT NULL,
    provenance TEXT NOT NULL,
    created_at TEXT NOT NULL,
    latest_state TEXT NOT NULL,
    report_json TEXT,
    failure_json TEXT
);

CREATE TABLE IF NOT EXISTS run_record_events (
    run_id TEXT NOT NULL REFERENCES run_records(run_id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    event_json TEXT NOT NULL,
    PRIMARY KEY (run_id, position)
);

CREATE TABLE IF NOT EXISTS run_record_artifacts (
    run_id TEXT NOT NULL REFERENCES run_records(run_id) ON DELETE CASCADE,
    artifact_id TEXT NOT NULL,
    artifact_json TEXT NOT NULL,
    PRIMARY KEY (run_id, artifact_id)
);

CREATE TABLE IF NOT EXISTS run_record_explanations (
    run_id TEXT PRIMARY KEY REFERENCES run_records(run_id) ON DELETE CASCADE,
    explanation_json TEXT NOT NULL
);

INSERT OR IGNORE INTO run_records (
    run_id,
    task,
    provenance,
    created_at,
    latest_state,
    report_json,
    failure_json
)
SELECT
    run_id,
    json_extract(report_json, '$.intent.task'),
    'fixture',
    json_extract(report_json, '$.intent.created_at'),
    'complete',
    report_json,
    NULL
FROM reports;

INSERT OR IGNORE INTO run_record_events (run_id, position, event_json)
SELECT run_id, position, event_json FROM run_events;

INSERT OR IGNORE INTO run_record_artifacts (run_id, artifact_id, artifact_json)
SELECT run_id, artifact_id, artifact_json FROM artifacts;

INSERT OR IGNORE INTO run_record_explanations (run_id, explanation_json)
SELECT run_id, explanation_json FROM explanations;
