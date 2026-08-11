-- Chatbot session isolation table
-- Each bank customer session maps to a set of their uploaded documents.
-- Questions are scoped to only those doc_ids.

CREATE TABLE IF NOT EXISTS chatbot_sessions (
    token           VARCHAR(64)  PRIMARY KEY,
    doc_ids         JSONB        NOT NULL DEFAULT '[]',
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    last_active_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    expires_at      TIMESTAMPTZ  NOT NULL DEFAULT (NOW() + INTERVAL '2 hours')
);

CREATE INDEX IF NOT EXISTS idx_chatbot_sessions_expires
    ON chatbot_sessions (expires_at);
