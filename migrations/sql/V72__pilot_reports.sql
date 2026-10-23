-- V72: Early-adopter pilot reports (30/60/90-day checkpoints).
-- Built from the organisation's own usage plus its feedback; never sent anywhere automatically —
-- the organisation exports the report and shares it if it chooses.
CREATE TABLE IF NOT EXISTS pilot_reports (
    id                 UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id          TEXT        NOT NULL,
    checkpoint         INTEGER     NOT NULL,          -- 30 | 60 | 90 (days into the pilot)
    period_start       DATE        NOT NULL,
    period_end         DATE        NOT NULL,
    organization       TEXT,
    contact            TEXT,
    metrics            JSONB       NOT NULL DEFAULT '{}'::jsonb,
    feedback           JSONB       NOT NULL DEFAULT '{}'::jsonb,
    publish_permission BOOLEAN     NOT NULL DEFAULT FALSE,
    created_by         TEXT,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_pilot_reports_tenant ON pilot_reports (tenant_id, created_at DESC);
