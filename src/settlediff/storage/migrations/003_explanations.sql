CREATE TABLE IF NOT EXISTS explanations (
    run_id TEXT PRIMARY KEY REFERENCES reports(run_id) ON DELETE CASCADE,
    explanation_json TEXT NOT NULL
);
