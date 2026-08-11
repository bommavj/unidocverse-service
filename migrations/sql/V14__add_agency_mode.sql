-- V17__add_agency_mode.sql
-- Creates settings table if it doesn't exist, then adds agency_mode flag.
-- Safe to run on fresh installs and existing installs.

CREATE TABLE IF NOT EXISTS settings (
    id           SERIAL PRIMARY KEY,
    key          VARCHAR(100) UNIQUE NOT NULL,
    value        TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Insert a default row if none exists
INSERT INTO settings (key, value)
VALUES ('initialized', 'true')
ON CONFLICT (key) DO NOTHING;

-- Add agency_mode column
ALTER TABLE settings
    ADD COLUMN IF NOT EXISTS agency_mode BOOLEAN NOT NULL DEFAULT FALSE;

COMMENT ON COLUMN settings.agency_mode IS
    'When TRUE, enables Insurance Agency Management: Clients, Policies, Renewals.';