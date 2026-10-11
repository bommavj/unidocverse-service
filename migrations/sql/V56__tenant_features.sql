-- Per-organisation module switches (Settings → Features). Replaces the single global row in `settings`,
-- which let one organisation's admin turn modules on or off for every organisation.
CREATE TABLE IF NOT EXISTS tenant_features (
    tenant_id   TEXT PRIMARY KEY,
    flags       JSONB NOT NULL DEFAULT '{}'::jsonb,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_by  TEXT
);
