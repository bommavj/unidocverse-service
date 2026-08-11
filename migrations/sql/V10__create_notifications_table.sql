-- migrations/create_notifications_table.sql
-- Run once: psql -U unidocverse -p 54321 -d unidocverse_db -f this_file.sql

CREATE TABLE IF NOT EXISTS notifications (
    id          SERIAL PRIMARY KEY,
    doc_id      VARCHAR(255) NOT NULL,
    doc_type    VARCHAR(100),
    filename    VARCHAR(500),
    severity    VARCHAR(20) NOT NULL,  -- expired, critical, urgent, warning
    title       TEXT NOT NULL,
    message     TEXT,
    expiry_date VARCHAR(20),
    days_until  INTEGER,
    is_read     BOOLEAN DEFAULT FALSE,
    created_at  TIMESTAMP DEFAULT NOW(),
    updated_at  TIMESTAMP DEFAULT NOW(),
    -- One notification per doc per severity level — upsert on conflict
    UNIQUE (doc_id, severity)
);

CREATE INDEX IF NOT EXISTS idx_notifications_unread ON notifications (is_read, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_notifications_doc_id ON notifications (doc_id);