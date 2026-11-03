CREATE TABLE IF NOT EXISTS tasks (
    task_id TEXT PRIMARY KEY,
    task_type TEXT NOT NULL,
    source TEXT,
    status TEXT NOT NULL,
    title TEXT,
    original_input TEXT NOT NULL,
    result_summary TEXT,
    error_code TEXT,
    error_message TEXT,
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    updated_at TEXT,
    metadata_json TEXT
);

CREATE TABLE IF NOT EXISTS messages (
    message_id TEXT PRIMARY KEY,
    task_id TEXT,
    conversation_id TEXT,
    role TEXT NOT NULL,
    provider TEXT,
    transport TEXT,
    content TEXT,
    sequence_no INTEGER,
    created_at TEXT,
    metadata_json TEXT
);

CREATE TABLE IF NOT EXISTS task_events (
    event_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    event_data_json TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS provider_calls (
    call_id TEXT PRIMARY KEY,
    task_id TEXT,
    provider TEXT,
    transport TEXT,
    action TEXT,
    status TEXT,
    duration_ms INTEGER,
    input_length INTEGER,
    output_length INTEGER,
    quality_ok INTEGER,
    quality_reason TEXT,
    conversation_url TEXT,
    error_message TEXT,
    started_at TEXT,
    finished_at TEXT,
    metadata_json TEXT
);

CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_tasks_created ON tasks(created_at);
CREATE INDEX IF NOT EXISTS idx_messages_task ON messages(task_id);
CREATE INDEX IF NOT EXISTS idx_provider_calls_task ON provider_calls(task_id);
