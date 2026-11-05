-- v1.2 Phase 2: confirmed API-only conversations; a pending send must never be replayed.
CREATE TABLE IF NOT EXISTS deepseek_sessions (
  name TEXT PRIMARY KEY,
  model TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('ready','pending','uncertain')),
  pending_task_id TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS deepseek_turns (
  turn_id TEXT PRIMARY KEY,
  session_name TEXT NOT NULL REFERENCES deepseek_sessions(name) ON DELETE CASCADE,
  sequence_no INTEGER NOT NULL,
  role TEXT NOT NULL CHECK (role IN ('user','assistant')),
  content TEXT NOT NULL,
  task_id TEXT NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE(session_name, sequence_no)
);
CREATE INDEX IF NOT EXISTS idx_deepseek_turns_session ON deepseek_turns(session_name, sequence_no);
