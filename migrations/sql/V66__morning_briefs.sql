-- V66: Morning brief — one stored brief per organisation per day (app/services/morning_brief.py)
CREATE TABLE IF NOT EXISTS morning_briefs (
    id         TEXT        PRIMARY KEY,
    tenant_id  TEXT        NOT NULL,
    brief_date DATE        NOT NULL,
    items      JSONB       NOT NULL DEFAULT '[]',
    problems   JSONB       NOT NULL DEFAULT '[]',
    counts     JSONB       NOT NULL DEFAULT '{}',
    delivered  JSONB       NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, brief_date)
);
