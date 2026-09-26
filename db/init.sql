-- LabOps Assistant + telemetria
CREATE TABLE IF NOT EXISTS conversations (
    id          TEXT PRIMARY KEY,
    title       TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS messages (
    id               TEXT PRIMARY KEY,
    conversation_id  TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role             TEXT NOT NULL CHECK (role IN ('user', 'assistant', 'tool')),
    content          TEXT NOT NULL,
    tool_name        TEXT,
    trace_id         TEXT,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS tool_runs (
    id               TEXT PRIMARY KEY,
    conversation_id  TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    tool_name        TEXT NOT NULL,
    input_json       JSONB,
    output_json      JSONB,
    status           TEXT NOT NULL,
    duration_ms      INT,
    trace_id         TEXT,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_messages_conv ON messages(conversation_id, created_at);
CREATE INDEX IF NOT EXISTS idx_tool_runs_conv ON tool_runs(conversation_id, created_at);
CREATE INDEX IF NOT EXISTS idx_messages_trace ON messages(trace_id);
